"""HTTP server that mimics the Observer cloud API for local thermostat control."""

from __future__ import annotations

import copy
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
    HOLD_END_LEAD_MINUTES,
    HOLD_FALLBACK_MINUTES,
    LOCAL_KEYS,
    MAX_PUSH_ATTEMPTS,
    OFFLINE_AFTER_SECONDS,
    PENDING_MAX_AGE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_CONTROL_DEFAULTS = {
    "mode": "off",
    "fan": "auto",
    "hold": "on",
    "htsp": "70",
    "clsp": "75",
}

_LOCAL_DEFAULTS = {
    "blight": str(DEFAULT_BLIGHT),
    "humSetpoint": str(DEFAULT_HUM_SETPOINT),
    "dehumSetpoint": str(DEFAULT_DEHUM_SETPOINT),
    "scrLockout": "off",
}

# Statuses whose flat fields are merged into ``reported`` (sensor sources).
_MERGED_ENDPOINTS = {"status", "odu_status", "idu_status"}


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


def parse_event_time(text: str, tz: datetime.tzinfo | None) -> datetime.datetime | None:
    """Parse the thermostat's ``  7/18/26 12:07AM`` local timestamps."""
    try:
        parsed = datetime.datetime.strptime(" ".join(text.split()), "%m/%d/%y %I:%M%p")
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=tz)


def _set_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = text
    return child


@dataclass
class PendingChange:
    """A value HA wants the thermostat to adopt, awaiting confirmation."""

    value: str
    set_at: datetime.datetime
    sent_at: datetime.datetime | None = None
    attempts: int = 0


class ThermostatData:
    """Thermostat state: what it reported, and what HA wants it to become.

    ``reported`` is the flattened ``/status`` (plus odu/idu status) data.
    ``echo_xml`` is the full config the thermostat POSTs back after applying a
    push (``POST /systems/<serial>``); settings that ``/status`` doesn't carry
    (backlight, humidity setpoints, lockout) are confirmed from it, and it is
    the template for the next ``/config`` we send. ``desired`` holds only the
    fields HA changed and has not yet seen applied.
    """

    def __init__(
        self,
        serial: str,
        api_address: str,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self.serial = serial
        self.api_address = api_address
        # Thermostat-local wall clock (HA's configured time zone)
        self.clock = clock or (lambda: datetime.datetime.now().astimezone())

        self.reported: dict[str, str] = {}
        self.desired: dict[str, PendingChange] = {}
        self.last_known: dict[str, str] = {}
        self.last_on_mode: str = "cool"

        self.echo_xml: str | None = None
        self._echo_root: ET.Element | None = None

        self.profile: dict[str, str] = {}
        self.raw_last: dict[str, Any] = {}
        self.firmware: str | None = None
        self.thermostat_ip: str | None = None
        self.last_communication: datetime.datetime | None = None

        self.latest_equip_description: str = "No Active Event"
        self.latest_equip_time: datetime.datetime | None = None

        # HA-only setting: how long a hold lasts. 0 = until the next schedule period.
        self.otmr: int = DEFAULT_OTMR

        # Set by the integration to persist state (debounced) after changes.
        self.persist_callback: Callable[[], None] | None = None

    # ── Persistence ────────────────────────────────────────────────

    def to_store(self) -> dict[str, Any]:
        return {
            "otmr": self.otmr,
            "last_on_mode": self.last_on_mode,
            "last_known": self.last_known,
            "firmware": self.firmware,
            "echo_xml": self.echo_xml,
        }

    def load_store(self, stored: dict[str, Any] | None) -> None:
        if not stored:
            return
        self.otmr = int(stored.get("otmr", self.otmr))
        self.last_on_mode = stored.get("last_on_mode", self.last_on_mode)
        self.last_known = dict(stored.get("last_known", {}))
        self.firmware = stored.get("firmware", self.firmware)
        if stored.get("echo_xml"):
            self.set_echo(stored["echo_xml"])

    def _persist(self) -> None:
        if self.persist_callback:
            self.persist_callback()

    # ── Config echo ────────────────────────────────────────────────

    def set_echo(self, xml: str) -> bool:
        """Store the thermostat's own copy of its config. False if unparseable."""
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as err:
            _LOGGER.warning("Unparseable config echo from thermostat: %s", err)
            return False
        if root.tag != "config" and root.find("config") is None:
            return False
        self.echo_xml = xml
        self._echo_root = root
        return True

    def _echo_config(self) -> ET.Element | None:
        root = self._echo_root
        if root is None:
            return None
        return root if root.tag == "config" else root.find("config")

    def echo_value(self, tag: str) -> str | None:
        config = self._echo_config()
        if config is None:
            return None
        child = config.find(tag)
        return None if child is None else (child.text or "")

    def _echo_zone(self) -> ET.Element | None:
        config = self._echo_config()
        if config is None:
            return None
        for zone in config.iterfind("zones/zone"):
            if zone.get("id") == "1":
                return zone
        return None

    def program(self) -> dict[int, list[int]]:
        """Schedule as ``{day id: [period start, in minutes after midnight]}``."""
        zone = self._echo_zone()
        out: dict[int, list[int]] = {}
        if zone is None:
            return out
        for day in zone.iterfind("program/day"):
            starts = []
            for period in day.iterfind("period"):
                try:
                    hh, mm = (period.findtext("time") or "").split(":")
                    starts.append(int(hh) * 60 + int(mm))
                except ValueError:
                    continue
            try:
                out[int(day.get("id", ""))] = sorted(starts)
            except ValueError:
                continue
        return out

    # ── Reads ──────────────────────────────────────────────────────

    @property
    def current(self) -> dict[str, str]:
        """Raw last-reported values (alias kept for sensor lookups)."""
        return self.reported

    def observed(self, key: str) -> str | None:
        """What the thermostat itself says the value is."""
        if key in CONTROL_KEYS:
            return self.reported.get(key)
        return self.echo_value(key)

    def effective(self, key: str) -> str | None:
        """Pending desired value if any, else what the thermostat reported."""
        if (pending := self.desired.get(key)) is not None:
            return pending.value
        return self.observed(key)

    def _float(self, key: str, effective: bool = False) -> float | None:
        val = self.effective(key) if effective else self.reported.get(key)
        try:
            return float(val) if val not in (None, "") else None
        except (ValueError, TypeError):
            return None

    def _local(self, key: str) -> str:
        return self.effective(key) or _LOCAL_DEFAULTS[key]

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

    @property
    def hum_setpoint(self) -> int:
        return int(float(self._local("humSetpoint")))

    @property
    def dehum_setpoint(self) -> int:
        return int(float(self._local("dehumSetpoint")))

    @property
    def blight(self) -> int:
        return int(float(self._local("blight")))

    @property
    def scr_lockout(self) -> bool:
        return self._local("scrLockout") == "on"

    # ── Mutations (commands from HA) ───────────────────────────────

    def set_field(self, key: str, value: str) -> None:
        """Record a desired value; it stays pending until the thermostat shows it."""
        value = str(value)
        self.desired.pop(key, None)  # newer command supersedes, fresh attempt budget
        if values_equal(self.observed(key), value):
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

    def set_hum_setpoint(self, value: int) -> None:
        self.set_field("humSetpoint", str(int(value)))

    def set_dehum_setpoint(self, value: int) -> None:
        self.set_field("dehumSetpoint", str(int(value)))

    def set_blight(self, value: int) -> None:
        self.set_field("blight", str(int(value)))

    def set_scr_lockout(self, locked: bool) -> None:
        self.set_field("scrLockout", "on" if locked else "off")

    def set_otmr(self, minutes: int) -> None:
        self.otmr = minutes
        self._persist()

    # ── Push lifecycle ─────────────────────────────────────────────

    @property
    def needs_push(self) -> bool:
        """True if there is anything the thermostat hasn't been told yet."""
        return any(p.sent_at is None for p in self.desired.values())

    def expire_unsent(self) -> None:
        """Drop commands that were never delivered because the thermostat was away."""
        now = _now()
        for key, pending in list(self.desired.items()):
            if (
                pending.sent_at is None
                and (now - pending.set_at).total_seconds() > PENDING_MAX_AGE_SECONDS
            ):
                _LOGGER.warning(
                    "Dropping %s=%s: thermostat did not check in for %ss",
                    key,
                    pending.value,
                    PENDING_MAX_AGE_SECONDS,
                )
                del self.desired[key]

    @property
    def is_online(self) -> bool:
        """True if the thermostat has contacted us recently."""
        if self.last_communication is None:
            return False
        return (_now() - self.last_communication).total_seconds() < OFFLINE_AFTER_SECONDS

    def mark_sent(self) -> None:
        """The thermostat fetched /config: start the confirmation clock."""
        now = _now()
        for pending in self.desired.values():
            if pending.sent_at is None:
                pending.sent_at = now
                pending.attempts += 1

    def reconcile(self) -> None:
        """Drop confirmed changes, re-arm or abandon ones the thermostat ignored."""
        now = _now()
        for key, pending in list(self.desired.items()):
            seen = self.observed(key)
            if seen is not None and values_equal(seen, pending.value):
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
                    seen,
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

    # ── Hold timing ────────────────────────────────────────────────

    def hold_until(self, now: datetime.datetime | None = None) -> str:
        """``HH:MM`` the thermostat should end a new hold at.

        With a configured duration, now + duration. Otherwise mimic the wall
        unit: shortly before the next schedule period starts.
        """
        now = now or self.clock()
        if self.otmr > 0:
            return (now + datetime.timedelta(minutes=self.otmr)).strftime("%H:%M")

        now_min = now.hour * 60 + now.minute
        program = self.program()
        today = (now.weekday() + 1) % 7 + 1  # schedule days: Sunday = 1
        for start in program.get(today, []):
            if start - HOLD_END_LEAD_MINUTES > now_min:
                return self._hhmm(start - HOLD_END_LEAD_MINUTES)
        tomorrow = program.get(today % 7 + 1, [])
        if tomorrow:
            return self._hhmm(tomorrow[0] - HOLD_END_LEAD_MINUTES)
        return (now + datetime.timedelta(minutes=HOLD_FALLBACK_MINUTES)).strftime("%H:%M")

    @staticmethod
    def _hhmm(minutes: int) -> str:
        minutes %= 1440
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    # ── /config construction ───────────────────────────────────────

    def build_config(self) -> list[ET.Element] | None:
        """The thermostat's own config with HA's pending changes applied.

        Returns the child elements for a ``<config>``, or None if the thermostat
        has never echoed its config (caller falls back to a built-in template).
        """
        source = self._echo_config()
        if source is None:
            return None
        config = copy.deepcopy(source)

        _set_text(config, "mode", self.config_value("mode"))
        _set_text(config, "fan", self.config_value("fan"))
        for key in LOCAL_KEYS:
            if key in self.desired:
                _set_text(config, key, self.desired[key].value)

        zone = next((z for z in config.iterfind("zones/zone") if z.get("id") == "1"), None)
        if zone is not None:
            self._rebuild_zone(zone)

        for child in config:
            child.tail = None
        return list(config)

    def _rebuild_zone(self, zone: ET.Element) -> None:
        """Zone setpoints only exist while a hold is on; mirror that."""
        keep = {c.tag: c for c in zone if c.tag in ("name", "program")}
        old_otmr = (zone.findtext("otmr") or "").strip()
        hold = self.config_value("hold")
        for child in list(zone):
            zone.remove(child)

        if "name" in keep:
            zone.append(keep["name"])
        _set_text(zone, "hold", hold)
        if hold == "on":
            _set_text(zone, "htsp", self.config_value("htsp"))
            _set_text(zone, "clsp", self.config_value("clsp"))
            # An existing hold keeps its end time; a new one gets a fresh,
            # future one (an empty/stale otmr makes the thermostat drop the hold).
            keep_old = self.reported.get("hold") == "on" and old_otmr
            _set_text(zone, "otmr", old_otmr if keep_old else self.hold_until())
        if "program" in keep:
            zone.append(keep["program"])


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
            response = (
                self._xml_response(self._status_xml())
                if self._endpoint(request.path) == "status"
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

    def _endpoint(self, path: str) -> str:
        """Last path segment; ``/systems/<serial>`` itself is the config echo."""
        parts = path.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-2] == "systems" and parts[-1] == self.data.serial:
            return "system"
        return parts[-1]

    async def _handle_get(self, request: web.Request) -> web.Response:
        final = self._endpoint(request.path)
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
            # Changes stay pending until the thermostat shows them applied.
            self.data.mark_sent()
            _LOGGER.info(
                "Thermostat fetched config (pending: %s)",
                {k: p.value for k, p in self.data.desired.items()},
            )
            return self._xml_response(self._config_xml())

        return web.Response(status=200)

    async def _handle_post(self, request: web.Request) -> web.Response:
        final = self._endpoint(request.path)
        body = decode_body(await request.text())
        _LOGGER.debug("POST %s body length=%s", request.path, len(body))

        self.data.thermostat_ip = request.remote

        if final == "system":
            return self._handle_config_echo(body)

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

        if final in _MERGED_ENDPOINTS:
            # odu_status / idu_status carry the outdoor/indoor unit sensors
            self.data.reported.update({k: v for k, v in received.items() if v != ""})
            self.data.raw_last[final] = received
            self._update_callback()
            return web.Response(status=200)

        # notifications, faults, history, and anything unknown: keep the latest
        # payload for diagnostics / protocol discovery.
        self.data.raw_last[final] = body
        _LOGGER.debug("Endpoint %s data: %s", final, body)
        return web.Response(status=200)

    def _handle_config_echo(self, body: str) -> web.Response:
        """The thermostat reports its full config after applying a push."""
        if self.data.set_echo(body):
            self.data.raw_last["config_echo"] = "stored"
            self.data.reconcile()
            self.data._persist()
            self._update_callback()
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

        data.expire_unsent()
        data.reconcile()
        data.last_communication = _now()
        data._persist()
        self._update_callback()

        if data.needs_push:
            _LOGGER.info("Notifying thermostat of pending config changes")
            return self._xml_response(self._status_xml(config_has_changes="on"))
        return self._xml_response(self._status_xml())

    def _handle_equipment_events(self, received: dict[str, str]) -> web.Response:
        """Handle /equipment_events POST (first event is the most recent)."""
        if received.get("active") == "on":
            self.data.latest_equip_description = received.get("description", "Unknown event")
            self.data.latest_equip_time = parse_event_time(
                received.get("localtime", ""), self.data.clock().tzinfo
            ) or _now()
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
        head = (
            f'<config version="1.9" xmlns:atom="http://www.w3.org/2005/Atom">'
            f'<atom:link rel="self" href="http://{d.api_address}/systems/{d.serial}/config"/>'
            f'<atom:link rel="http://{d.api_address}/rels/system"'
            f' href="http://{d.api_address}/systems/{d.serial}"/>'
            f'<atom:link rel="http://{d.api_address}/rels/dealer_config"'
            f' href="http://{d.api_address}/systems/{d.serial}/dealer_config"/>'
            f"<timestamp>{self._utcnow()}</timestamp>"
        )
        children = d.build_config()
        if children is None:
            return head + self._fallback_body() + "</config>"
        body = "".join(ET.tostring(c, encoding="unicode") for c in children)
        return head + body + "<utilityEvent/></config>"

    def _fallback_body(self) -> str:
        """Config used only until the thermostat has echoed its own."""
        d = self.data
        hold = d.config_value("hold")
        zone_hold = (
            f"<hold>on</hold><htsp>{d.config_value('htsp')}</htsp>"
            f"<clsp>{d.config_value('clsp')}</clsp><otmr>{d.hold_until()}</otmr>"
            if hold == "on"
            else "<hold>off</hold>"
        )
        return (
            f"<mode>{d.config_value('mode')}</mode>"
            f"<fan>{d.config_value('fan')}</fan>"
            f"<blight>{d.blight}</blight>"
            f"<timeFormat>12</timeFormat>"
            f"<dst>on</dst>"
            f"<volume>high</volume>"
            f"<soundType>click</soundType>"
            f"<scrLockout>{'on' if d.scr_lockout else 'off'}</scrLockout>"
            f"<scrLockoutCode>0000</scrLockoutCode>"
            f"<humSetpoint>{d.hum_setpoint}</humSetpoint>"
            f"<dehumSetpoint>{d.dehum_setpoint}</dehumSetpoint>"
            f"<utilityEvent/>"
            f'<zones><zone id="1"><name>Zone 1</name>{zone_hold}</zone></zones>'
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
            if len(child) or "}" in child.tag or child.tag == "zone":
                continue  # containers, namespaced (atom:link) and empty zone stubs
            text = (child.text or "").strip()
            if child.tag in received and (first_only or text == ""):
                continue  # keep the first event / a real value over a repeat blank
            received[child.tag] = text
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
