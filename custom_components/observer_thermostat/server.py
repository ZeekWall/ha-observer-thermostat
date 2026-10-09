"""HTTP server that mimics the Observer cloud API for local thermostat control."""

from __future__ import annotations

import datetime
import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote_plus

from aiohttp import web

from .capture import CaptureLog
from .const import (
    CONFIRM_GRACE_SECONDS,
    CONTROL_KEYS,
    DEFAULT_BLIGHT,
    DEFAULT_DEHUM_SETPOINT,
    DEFAULT_HUM_SETPOINT,
    DEFAULT_OTMR,
    MAX_PUSH_ATTEMPTS,
)

_LOGGER = logging.getLogger(__name__)

_CONTROL_DEFAULTS = {
    "mode": "off",
    "fan": "auto",
    "hold": "on",
    "htsp": "70",
    "clsp": "75",
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def values_equal(a: str | None, b: str | None) -> bool:
    """Compare thermostat values, tolerating "75" vs "75.0"."""
    if a == b:
        return True
    try:
        return float(a) == float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def decode_body(raw: str) -> str:
    """Strip the exact ``data=`` form prefix and URL-decode the XML payload."""
    if raw.startswith("data="):
        return unquote_plus(raw[5:])
    return raw


@dataclass
class PendingChange:
    """A value HA wants the thermostat to adopt, awaiting confirmation."""

    value: str
    set_at: datetime.datetime
    sent_at: datetime.datetime | None = None
    attempts: int = 0


class ThermostatData:
    """Thermostat state: what it reported, and what HA wants it to become.

    ``reported`` is the latest flattened ``/status`` payload. ``desired`` holds
    only the fields HA changed and has not yet seen echoed back. Reads go through
    ``effective()`` so the UI is optimistic and a stale status report cannot
    overwrite a change that is still in flight.
    """

    def __init__(self, serial: str, api_address: str) -> None:
        self.serial = serial
        self.api_address = api_address

        self.reported: dict[str, str] = {}
        self.desired: dict[str, PendingChange] = {}
        # Last control values seen from the thermostat; survives restarts so
        # /config is never built from hard-coded defaults.
        self.last_known: dict[str, str] = {}
        self.last_on_mode: str = "cool"

        self.profile: dict[str, str] = {}
        self.raw_last: dict[str, dict[str, str]] = {}
        self.firmware: str | None = None
        self.thermostat_ip: str | None = None
        self.last_communication: datetime.datetime | None = None

        self.latest_equip_description: str = "No Active Event"
        self.latest_equip_time: datetime.datetime | None = None

        # Settings the thermostat never reports back; HA is the source of truth.
        self.hum_setpoint: int = DEFAULT_HUM_SETPOINT
        self.dehum_setpoint: int = DEFAULT_DEHUM_SETPOINT
        self.blight: int = DEFAULT_BLIGHT
        self.scr_lockout: bool = False
        self.otmr: int = DEFAULT_OTMR  # minutes; 0 = permanent hold
        self.local_dirty = False

        # Set by the integration to persist state (debounced) after changes.
        self.persist_callback: Callable[[], None] | None = None

    # ── Persistence ────────────────────────────────────────────────

    def to_store(self) -> dict[str, Any]:
        return {
            "hum_setpoint": self.hum_setpoint,
            "dehum_setpoint": self.dehum_setpoint,
            "blight": self.blight,
            "scr_lockout": self.scr_lockout,
            "otmr": self.otmr,
            "last_on_mode": self.last_on_mode,
            "last_known": self.last_known,
            "firmware": self.firmware,
        }

    def load_store(self, stored: dict[str, Any] | None) -> None:
        if not stored:
            return
        self.hum_setpoint = int(stored.get("hum_setpoint", self.hum_setpoint))
        self.dehum_setpoint = int(stored.get("dehum_setpoint", self.dehum_setpoint))
        self.blight = int(stored.get("blight", self.blight))
        self.scr_lockout = bool(stored.get("scr_lockout", self.scr_lockout))
        self.otmr = int(stored.get("otmr", self.otmr))
        self.last_on_mode = stored.get("last_on_mode", self.last_on_mode)
        self.last_known = dict(stored.get("last_known", {}))
        self.firmware = stored.get("firmware", self.firmware)

    def _persist(self) -> None:
        if self.persist_callback:
            self.persist_callback()

    # ── Reads ──────────────────────────────────────────────────────

    @property
    def current(self) -> dict[str, str]:
        """Raw last-reported values (alias kept for sensor lookups)."""
        return self.reported

    def effective(self, key: str) -> str | None:
        """Pending desired value if any, else what the thermostat reported."""
        if (pending := self.desired.get(key)) is not None:
            return pending.value
        return self.reported.get(key)

    def _float(self, key: str, effective: bool = False) -> float | None:
        val = self.effective(key) if effective else self.reported.get(key)
        try:
            return float(val) if val not in (None, "") else None
        except (ValueError, TypeError):
            return None

    @property
    def temperature(self) -> float | None:
        return self._float("rt")

    @property
    def humidity(self) -> float | None:
        return self._float("rh")

    @property
    def mode(self) -> str | None:
        return self.effective("mode")

    @property
    def fan_mode(self) -> str | None:
        return self.effective("fan")

    @property
    def cooling_setpoint(self) -> float | None:
        return self._float("clsp", effective=True)

    @property
    def heating_setpoint(self) -> float | None:
        return self._float("htsp", effective=True)

    @property
    def target_temperature(self) -> float | None:
        mode = self.mode
        if mode == "cool":
            return self.cooling_setpoint
        if mode == "heat":
            return self.heating_setpoint
        return None

    @property
    def is_cooling(self) -> bool:
        return self.reported.get("coolicon") == "on"

    @property
    def is_heating(self) -> bool:
        return self.reported.get("heaticon") == "on"

    @property
    def fan_running(self) -> bool:
        return self.reported.get("fanicon") == "on"

    @property
    def hvac_action(self) -> str:
        if self.is_cooling:
            return "cooling"
        if self.is_heating:
            return "heating"
        return "idle"

    @property
    def outdoor_coil_temp(self) -> float | None:
        return self._float("oducoiltmp")

    @property
    def outdoor_ambient_temp(self) -> float | None:
        return self._float("oat")

    @property
    def indoor_cfm(self) -> float | None:
        return self._float("iducfm")

    @property
    def filter_hours_remain(self) -> float | None:
        return self._float("filtrlvl")

    @property
    def hold(self) -> str | None:
        return self.effective("hold")

    @property
    def opstat(self) -> str | None:
        return self.reported.get("opstat")

    # ── Mutations (commands from HA) ───────────────────────────────

    def set_field(self, key: str, value: str) -> None:
        """Record a desired value; it stays pending until the thermostat echoes it."""
        value = str(value)
        if key in self.desired:
            # Newer command supersedes the old one and gets a fresh attempt budget.
            del self.desired[key]
        if values_equal(self.reported.get(key), value):
            return  # already the case, nothing to push
        self.desired[key] = PendingChange(value=value, set_at=_now())
        if key == "mode" and value != "off":
            self.last_on_mode = value
            self._persist()

    def set_mode(self, mode: str) -> None:
        self.set_field("mode", mode)

    def set_fan_mode(self, fan_mode: str) -> None:
        self.set_field("fan", fan_mode)

    def set_hold(self, hold: str) -> None:
        self.set_field("hold", hold)

    def set_cool_setpoint(self, temperature: float) -> None:
        self.set_field("clsp", str(round(temperature)))

    def set_heat_setpoint(self, temperature: float) -> None:
        self.set_field("htsp", str(round(temperature)))

    def set_temperature(self, temperature: float) -> bool:
        """Set the setpoint for the active mode. Returns False if there isn't one."""
        mode = self.mode
        if mode == "cool":
            self.set_cool_setpoint(temperature)
            return True
        if mode == "heat":
            self.set_heat_setpoint(temperature)
            return True
        return False

    def _set_local(self, attr: str, value: Any) -> None:
        setattr(self, attr, value)
        self.local_dirty = True
        self._persist()

    def set_hum_setpoint(self, value: int) -> None:
        self._set_local("hum_setpoint", value)

    def set_dehum_setpoint(self, value: int) -> None:
        self._set_local("dehum_setpoint", value)

    def set_blight(self, value: int) -> None:
        self._set_local("blight", value)

    def set_scr_lockout(self, locked: bool) -> None:
        self._set_local("scr_lockout", locked)

    def set_otmr(self, minutes: int) -> None:
        self._set_local("otmr", minutes)

    # ── Push lifecycle ─────────────────────────────────────────────

    @property
    def needs_push(self) -> bool:
        """True if there is anything the thermostat hasn't been told yet."""
        return self.local_dirty or any(p.sent_at is None for p in self.desired.values())

    def mark_sent(self) -> None:
        """The thermostat fetched /config: start the confirmation clock."""
        now = _now()
        for pending in self.desired.values():
            if pending.sent_at is None:
                pending.sent_at = now
                pending.attempts += 1
        self.local_dirty = False

    def reconcile(self) -> None:
        """Drop confirmed changes, re-arm or abandon ones the thermostat ignored."""
        now = _now()
        for key, pending in list(self.desired.items()):
            if key in self.reported and values_equal(self.reported[key], pending.value):
                del self.desired[key]
                continue
            if pending.sent_at is None:
                continue
            if (now - pending.sent_at).total_seconds() <= CONFIRM_GRACE_SECONDS:
                continue
            if pending.attempts >= MAX_PUSH_ATTEMPTS:
                _LOGGER.warning(
                    "Thermostat never applied %s=%s after %s attempts "
                    "(it reports %s) — dropping the change",
                    key,
                    pending.value,
                    pending.attempts,
                    self.reported.get(key),
                )
                del self.desired[key]
            else:
                _LOGGER.info(
                    "%s=%s not confirmed after %ss — re-pushing (attempt %s)",
                    key,
                    pending.value,
                    CONFIRM_GRACE_SECONDS,
                    pending.attempts + 1,
                )
                pending.sent_at = None

    def config_value(self, key: str) -> str:
        """Value to send in /config for a control field."""
        return (
            self.effective(key)
            or self.last_known.get(key)
            or _CONTROL_DEFAULTS[key]
        )


class ObserverThermostatServer:
    """Aiohttp server mimicking the Observer cloud API."""

    def __init__(
        self,
        data: ThermostatData,
        port: int,
        update_callback: Callable[[], None],
        capture: CaptureLog | None = None,
    ) -> None:
        self.data = data
        self.port = port
        self._update_callback = update_callback
        self.capture = capture
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application(middlewares=[self._capture_middleware])
        app.router.add_route("GET", "/{path:.*}", self._handle_get)
        app.router.add_route("POST", "/{path:.*}", self._handle_post)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        _LOGGER.info("Observer Thermostat API server started on port %s", self.port)

    @property
    def bound_port(self) -> int | None:
        """The actual listening port (useful when started on port 0 in tests)."""
        if self._runner is None:
            return None
        for site in self._runner.sites:
            server = getattr(site, "_server", None)
            if server and server.sockets:
                return server.sockets[0].getsockname()[1]
        return None

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            _LOGGER.info("Observer Thermostat API server stopped")

    # ── Middleware ─────────────────────────────────────────────────

    @web.middleware
    async def _capture_middleware(self, request: web.Request, handler):
        req_body = await request.text() if request.method == "POST" else ""
        try:
            response = await handler(request)
        except Exception:  # noqa: BLE001 — never let the thermostat see a 500
            _LOGGER.exception("Error handling %s %s", request.method, request.path)
            final = request.path.rstrip("/").split("/")[-1]
            response = (
                self._xml_response(self._status_xml())
                if final == "status"
                else web.Response(status=200)
            )
        if self.capture is not None:
            self.capture.add(
                method=request.method,
                path=request.path,
                query=request.query_string,
                remote=request.remote,
                headers=dict(request.headers),
                req_body=req_body,
                status=response.status,
                resp_body=response.text or "",
            )
        return response

    # ── Request handlers ───────────────────────────────────────────

    @staticmethod
    def _final_segment(path: str) -> str:
        return path.rstrip("/").split("/")[-1]

    async def _handle_get(self, request: web.Request) -> web.Response:
        final = self._final_segment(request.path)
        _LOGGER.debug("GET %s", request.path)

        if final.lower() == "alive":
            return web.Response(text="alive", content_type="text/plain")

        if final == "time":
            xml = (
                f'<time version="1.9" xmlns:atom="http://www.w3.org/2005/Atom">'
                f'<atom:link rel="self" href="http://{self.data.api_address}/time/"/>'
                f"<utc>{self._utcnow()}</utc>"
                f"</time>"
            )
            return self._xml_response(xml)

        if final == "config":
            # Thermostat is fetching config. Changes stay pending until a later
            # /status shows them applied.
            self.data.mark_sent()
            _LOGGER.info(
                "Thermostat fetched config (pending: %s)",
                {k: p.value for k, p in self.data.desired.items()},
            )
            return self._xml_response(self._config_xml())

        return web.Response(status=200)

    async def _handle_post(self, request: web.Request) -> web.Response:
        final = self._final_segment(request.path)
        body = decode_body(await request.text())
        _LOGGER.debug("POST %s body length=%s", request.path, len(body))

        self.data.thermostat_ip = request.remote

        received = self._parse_xml(body, final) if body.strip() else {}

        if final == "status":
            if not received:
                return self._xml_response(self._status_xml())
            return self._handle_status(received)

        if not received:
            return web.Response(status=200)

        if final == "equipment_events":
            return self._handle_equipment_events(received)

        if final == "profile":
            self.data.profile = received
            if fw := received.get("firmware"):
                self.data.firmware = fw
            self.data.raw_last[final] = received
            self.data._persist()
            self._update_callback()
            return web.Response(status=200)

        # odu_status, idu_status, idu_faults, odu_faults, history, and anything
        # unknown: keep the latest payload for diagnostics / protocol discovery.
        self.data.raw_last[final] = received
        _LOGGER.debug("Endpoint %s data: %s", final, received)
        return web.Response(status=200)

    def _handle_status(self, received: dict[str, str]) -> web.Response:
        """Handle the main /status POST from the thermostat."""
        data = self.data
        data.reported.update(received)
        data.raw_last["status"] = received

        for key in CONTROL_KEYS:
            if (val := received.get(key)) not in (None, ""):
                data.last_known[key] = val
        if (mode := received.get("mode")) not in (None, "", "off"):
            data.last_on_mode = mode

        data.reconcile()
        data.last_communication = _now()
        data._persist()
        self._update_callback()

        if data.needs_push:
            _LOGGER.info("Notifying thermostat of pending config changes")
            return self._xml_response(self._status_xml(config_has_changes="on"))
        return self._xml_response(self._status_xml())

    def _handle_equipment_events(self, received: dict[str, str]) -> web.Response:
        """Handle /equipment_events POST."""
        if received.get("active") == "on":
            lt = received.get("localtime", "")
            if lt.startswith("T"):
                lt = lt[1:]
            self.data.latest_equip_description = received.get("description", "Unknown event")
            # Event time is time-only from the thermostat's local clock.
            try:
                t = datetime.datetime.strptime(lt, "%H:%M:%S")
                today = _now().date()
                self.data.latest_equip_time = datetime.datetime(
                    today.year, today.month, today.day,
                    t.hour, t.minute, t.second,
                    tzinfo=datetime.timezone.utc,
                )
            except (ValueError, TypeError):
                self.data.latest_equip_time = _now()
        else:
            self.data.latest_equip_description = "No Active Event"
            self.data.latest_equip_time = None

        self.data.raw_last["equipment_events"] = received
        self._update_callback()
        return web.Response(status=200)

    # ── XML builders ───────────────────────────────────────────────

    def _status_xml(self, config_has_changes: str = "off") -> str:
        return (
            f'<status version="1.9" xmlns:atom="http://www.w3.org/2005/Atom">'
            f'<atom:link rel="self" href="http://{self.data.api_address}/systems/{self.data.serial}/status"/>'
            f'<atom:link rel="http://{self.data.api_address}/rels/system"'
            f' href="http://{self.data.api_address}/systems/{self.data.serial}"/>'
            f"<timestamp>{self._utcnow()}</timestamp>"
            f"<pingRate>0</pingRate>"
            f"<dealerConfigPingRate>0</dealerConfigPingRate>"
            f"<weatherPingRate>14400</weatherPingRate>"
            f"<equipEventsPingRate>60</equipEventsPingRate>"
            f"<historyPingRate>86400</historyPingRate>"
            f"<iduFaultsPingRate>86400</iduFaultsPingRate>"
            f"<iduStatusPingRate>300</iduStatusPingRate>"
            f"<oduFaultsPingRate>86400</oduFaultsPingRate>"
            f"<oduStatusPingRate>0</oduStatusPingRate>"
            f"<configHasChanges>{config_has_changes}</configHasChanges>"
            f"<dealerConfigHasChanges>off</dealerConfigHasChanges>"
            f"<dealerHasChanges>off</dealerHasChanges>"
            f"<oduConfigHasChanges>off</oduConfigHasChanges>"
            f"<iduConfigHasChanges>off</iduConfigHasChanges>"
            f"<utilityEventsHasChanges>off</utilityEventsHasChanges>"
            f"</status>"
        )

    def _config_xml(self) -> str:
        d = self.data
        scr = "on" if d.scr_lockout else "off"
        otmr_val = str(d.otmr) if d.otmr > 0 else ""
        return (
            f'<config version="1.9" xmlns:atom="http://www.w3.org/2005/Atom">'
            f'<atom:link rel="self" href="http://{d.api_address}/systems/{d.serial}/config"/>'
            f'<atom:link rel="http://{d.api_address}/rels/system"'
            f' href="http://{d.api_address}/systems/{d.serial}"/>'
            f'<atom:link rel="http://{d.api_address}/rels/dealer_config"'
            f' href="http://{d.api_address}/systems/{d.serial}/dealer_config"/>'
            f"<timestamp>{self._utcnow()}</timestamp>"
            f"<mode>{d.config_value('mode')}</mode>"
            f"<fan>{d.config_value('fan')}</fan>"
            f"<blight>{d.blight}</blight>"
            f"<timeFormat>12</timeFormat>"
            f"<dst>on</dst>"
            f"<volume>high</volume>"
            f"<soundType>click</soundType>"
            f"<scrLockout>{scr}</scrLockout>"
            f"<scrLockoutCode>0000</scrLockoutCode>"
            f"<humSetpoint>{d.hum_setpoint}</humSetpoint>"
            f"<dehumSetpoint>{d.dehum_setpoint}</dehumSetpoint>"
            f"<utilityEvent/>"
            f"<zones>"
            f'<zone id="1">'
            f"<n>Zone 1</n>"
            f"<hold>{d.config_value('hold')}</hold>"
            f"<otmr>{otmr_val}</otmr>"
            f"<htsp>{d.config_value('htsp')}</htsp>"
            f"<clsp>{d.config_value('clsp')}</clsp>"
            f"<program></program>"
            f"</zone>"
            f"</zones>"
            f"</config>"
        )

    # ── Helpers ────────────────────────────────────────────────────

    def _parse_xml(self, raw: str, endpoint: str) -> dict[str, str]:
        """Flatten an XML payload's leaf elements into ``{tag: text}``."""
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            _LOGGER.warning("Malformed XML from thermostat (%s): %s", endpoint, err)
            return {}

        received: dict[str, str] = {}
        first_only = endpoint == "equipment_events"  # latest event only
        for child in root.iter():
            if len(child) or "}" in child.tag:
                continue  # containers and namespaced (atom:link) elements
            if first_only and child.tag in received:
                continue
            received[child.tag] = (child.text or "").strip()
        return received

    @staticmethod
    def _utcnow() -> str:
        return _now().strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _xml_response(xml: str) -> web.Response:
        return web.Response(
            text=xml,
            content_type="application/xml",
            headers={"Connection": "keep-alive"},
        )
