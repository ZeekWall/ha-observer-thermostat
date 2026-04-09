"""HTTP server that mimics the Observer cloud API for local thermostat control."""

from __future__ import annotations

import datetime
import logging
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from aiohttp import web

from .const import (
    CHANGES_PENDING_TIMEOUT_SECONDS,
    DEFAULT_BLIGHT,
    DEFAULT_DEHUM_SETPOINT,
    DEFAULT_HUM_SETPOINT,
    DEFAULT_OTMR,
    MONITORED_KEYS,
)

_LOGGER = logging.getLogger(__name__)


class ThermostatData:
    """Hold the current and candidate thermostat state."""

    def __init__(self, serial: str, api_address: str) -> None:
        self.serial = serial
        self.api_address = api_address

        # Live data reported by the thermostat
        self.current: dict[str, str | None] = {}

        # Desired state to push on next thermostat poll
        self.candidate: dict[str, str | None] = {}

        self.changes_pending = False
        self.changes_pending_since: datetime.datetime | None = None
        self.first_start = True

        self.firmware: str | None = None
        self.thermostat_ip: str | None = None
        self.last_communication: datetime.datetime | None = None

        # Equipment event — stored separately for clean sensor mapping
        self.latest_equip_description: str = "No Active Event"
        self.latest_equip_time: datetime.datetime | None = None

        # Configurable values sent back to the thermostat in /config responses
        self.hum_setpoint: int = DEFAULT_HUM_SETPOINT
        self.dehum_setpoint: int = DEFAULT_DEHUM_SETPOINT
        self.blight: int = DEFAULT_BLIGHT
        self.scr_lockout: bool = False
        self.otmr: int = DEFAULT_OTMR  # minutes; 0 = permanent hold

    # ── Convenience read properties ────────────────────────────────

    def _float(self, key: str) -> float | None:
        val = self.current.get(key)
        try:
            return float(val) if val is not None else None
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
        return self.current.get("mode")

    @property
    def fan_mode(self) -> str | None:
        return self.current.get("fan")

    @property
    def cooling_setpoint(self) -> float | None:
        return self._float("clsp")

    @property
    def heating_setpoint(self) -> float | None:
        return self._float("htsp")

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
        return self.current.get("coolicon") == "on"

    @property
    def is_heating(self) -> bool:
        return self.current.get("heaticon") == "on"

    @property
    def fan_running(self) -> bool:
        return self.current.get("fanicon") == "on"

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
        return self.current.get("hold")

    @property
    def opstat(self) -> str | None:
        return self.current.get("opstat")

    # ── Mutation methods ───────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        self.candidate["mode"] = mode
        self._mark_pending()

    def set_fan_mode(self, fan_mode: str) -> None:
        self.candidate["fan"] = fan_mode
        self._mark_pending()

    def set_temperature(self, temperature: float) -> None:
        temp_str = str(round(temperature))
        mode = self.candidate.get("mode", self.mode)
        if mode == "cool":
            self.candidate["clsp"] = temp_str
            self._mark_pending()
        elif mode == "heat":
            self.candidate["htsp"] = temp_str
            self._mark_pending()

    def set_cool_setpoint(self, temperature: float) -> None:
        self.candidate["clsp"] = str(round(temperature))
        self._mark_pending()

    def set_heat_setpoint(self, temperature: float) -> None:
        self.candidate["htsp"] = str(round(temperature))
        self._mark_pending()

    def set_hold(self, hold: str) -> None:
        self.candidate["hold"] = hold
        self._mark_pending()

    def set_hum_setpoint(self, value: int) -> None:
        self.hum_setpoint = value
        self._mark_pending()

    def set_dehum_setpoint(self, value: int) -> None:
        self.dehum_setpoint = value
        self._mark_pending()

    def set_blight(self, value: int) -> None:
        self.blight = value
        self._mark_pending()

    def set_scr_lockout(self, locked: bool) -> None:
        self.scr_lockout = locked
        self._mark_pending()

    def set_otmr(self, minutes: int) -> None:
        self.otmr = minutes
        self._mark_pending()

    def _mark_pending(self) -> None:
        self.changes_pending = True
        if self.changes_pending_since is None:
            self.changes_pending_since = datetime.datetime.now(datetime.timezone.utc)

    def clear_pending(self) -> None:
        self.changes_pending = False
        self.changes_pending_since = None

    def check_pending_timeout(self) -> bool:
        """Return True if pending changes have been waiting longer than the timeout."""
        if (
            self.changes_pending
            and self.changes_pending_since is not None
        ):
            age = (
                datetime.datetime.now(datetime.timezone.utc) - self.changes_pending_since
            ).total_seconds()
            return age > CHANGES_PENDING_TIMEOUT_SECONDS
        return False


class ObserverThermostatServer:
    """Aiohttp server mimicking the Observer cloud API."""

    def __init__(
        self,
        data: ThermostatData,
        port: int,
        update_callback,
    ) -> None:
        self.data = data
        self.port = port
        self._update_callback = update_callback
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("GET", "/{path:.*}", self._handle_get)
        app.router.add_route("POST", "/{path:.*}", self._handle_post)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        _LOGGER.info("Observer Thermostat API server started on port %s", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            _LOGGER.info("Observer Thermostat API server stopped")

    # ── Request handlers ───────────────────────────────────────────

    async def _handle_get(self, request: web.Request) -> web.Response:
        path = request.path
        _LOGGER.debug("GET %s", path)

        if "/Alive" in path:
            return web.Response(text="alive", content_type="text/plain")

        if "/time" in path:
            xml = (
                f'<time version="1.9" xmlns:atom="http://www.w3.org/2005/Atom">'
                f'<atom:link rel="self" href="http://{self.data.api_address}/time/"/>'
                f"<utc>{self._utcnow()}</utc>"
                f"</time>"
            )
            return self._xml_response(xml)

        if "/config" in path:
            # Thermostat is fetching config — clear pending flag and send candidate state
            self.data.clear_pending()
            _LOGGER.info("Thermostat fetched config: %s", self.data.candidate)
            return self._xml_response(self._config_xml())

        return web.Response(status=200)

    async def _handle_post(self, request: web.Request) -> web.Response:
        path = request.path
        body = await request.text()
        body = unquote(body).lstrip("data=")

        final_locator = f"/{path.split('/')[-1]}"
        _LOGGER.debug("POST %s body length=%s", final_locator, len(body))

        handled_paths = {
            "/status", "/odu_status", "/equipment_events",
            "/profile", "/idu_status", "/idu_faults", "/odu_faults", "/history",
        }

        if len(body) < 45 or final_locator not in handled_paths:
            if "/status" in path:
                return self._xml_response(self._status_xml())
            return web.Response(status=200)

        received = self._parse_xml(body, final_locator)
        if not received:
            if "/status" in path:
                return self._xml_response(self._status_xml())
            return web.Response(status=200)

        self.data.thermostat_ip = request.remote

        if "/status" in final_locator:
            return await self._handle_status(received)

        if "/equipment_events" in final_locator:
            return self._handle_equipment_events(received)

        if "/profile" in final_locator:
            fw = received.get("firmware")
            if fw:
                self.data.firmware = fw
            self._update_callback()
            return web.Response(status=200)

        # /odu_status, /idu_status, /idu_faults, /odu_faults, /history
        # Log at DEBUG for now — handlers can be expanded in a future version
        _LOGGER.debug("Endpoint %s data: %s", final_locator, received)
        return web.Response(status=200)

    async def _handle_status(self, received: dict) -> web.Response:
        """Handle the main /status POST from the thermostat."""
        # Update monitored sensor values
        for key in MONITORED_KEYS:
            if key in received:
                self.data.current[key] = received[key]

        # Keep candidate in sync with whatever the thermostat reports when we're
        # not actively pushing a change — prevents HA from snapping back manual
        # adjustments made on the thermostat screen next time any HA command fires
        if not self.data.changes_pending:
            for key in ("clsp", "htsp", "mode", "fan", "hold"):
                if key in self.data.current:
                    self.data.candidate[key] = self.data.current[key]

        # Check if a pending change has gone unacknowledged too long
        if self.data.check_pending_timeout():
            _LOGGER.warning(
                "Pending changes timed out after %s seconds — clearing. "
                "The thermostat may not have acknowledged the config update.",
                CHANGES_PENDING_TIMEOUT_SECONDS,
            )
            self.data.clear_pending()

        self.data.last_communication = datetime.datetime.now(datetime.timezone.utc)

        if self.data.first_start:
            # Seed candidate from whatever the thermostat currently has
            self.data.candidate["clsp"] = self.data.current.get("clsp", "75")
            self.data.candidate["htsp"] = self.data.current.get("htsp", "70")
            self.data.candidate["mode"] = self.data.current.get("mode", "off")
            self.data.candidate["hold"] = self.data.current.get("hold", "on")
            self.data.candidate["fan"] = self.data.current.get("fan", "auto")
            self.data.first_start = False
            _LOGGER.info(
                "Initialized candidate config from thermostat: %s",
                self.data.candidate,
            )

        self._update_callback()

        if self.data.changes_pending:
            _LOGGER.info("Notifying thermostat of pending config changes")
            return self._xml_response(self._status_xml(config_has_changes="on"))

        return self._xml_response(self._status_xml())

    def _handle_equipment_events(self, received: dict) -> web.Response:
        """Handle /equipment_events POST."""
        if received.get("active") == "on":
            lt = received.get("localtime", "")
            if lt.startswith("T"):
                lt = lt[1:]
            self.data.latest_equip_description = received.get("description", "Unknown event")
            # Store event time as a proper datetime (time-only from thermostat local clock)
            try:
                t = datetime.datetime.strptime(lt, "%H:%M:%S")
                today = datetime.datetime.now(datetime.timezone.utc).date()
                self.data.latest_equip_time = datetime.datetime(
                    today.year, today.month, today.day,
                    t.hour, t.minute, t.second,
                    tzinfo=datetime.timezone.utc,
                )
            except (ValueError, TypeError):
                self.data.latest_equip_time = datetime.datetime.now(datetime.timezone.utc)
        else:
            self.data.latest_equip_description = "No Active Event"
            self.data.latest_equip_time = None

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
        c = self.data.candidate
        scr = "on" if self.data.scr_lockout else "off"
        otmr_val = str(self.data.otmr) if self.data.otmr > 0 else ""
        return (
            f'<config version="1.9" xmlns:atom="http://www.w3.org/2005/Atom">'
            f'<atom:link rel="self" href="http://{self.data.api_address}/systems/{self.data.serial}/config"/>'
            f'<atom:link rel="http://{self.data.api_address}/rels/system"'
            f' href="http://{self.data.api_address}/systems/{self.data.serial}"/>'
            f'<atom:link rel="http://{self.data.api_address}/rels/dealer_config"'
            f' href="http://{self.data.api_address}/systems/{self.data.serial}/dealer_config"/>'
            f"<timestamp>{self._utcnow()}</timestamp>"
            f"<mode>{c.get('mode', 'off')}</mode>"
            f"<fan>{c.get('fan', 'auto')}</fan>"
            f"<blight>{self.data.blight}</blight>"
            f"<timeFormat>12</timeFormat>"
            f"<dst>on</dst>"
            f"<volume>high</volume>"
            f"<soundType>click</soundType>"
            f"<scrLockout>{scr}</scrLockout>"
            f"<scrLockoutCode>0000</scrLockoutCode>"
            f"<humSetpoint>{self.data.hum_setpoint}</humSetpoint>"
            f"<dehumSetpoint>{self.data.dehum_setpoint}</dehumSetpoint>"
            f"<utilityEvent/>"
            f"<zones>"
            f'<zone id="1">'
            f"<n>Zone 1</n>"
            f"<hold>{c.get('hold', 'on')}</hold>"
            f"<otmr>{otmr_val}</otmr>"
            f"<htsp>{c.get('htsp', '70')}</htsp>"
            f"<clsp>{c.get('clsp', '75')}</clsp>"
            f"<program></program>"
            f"</zone>"
            f"</zones>"
            f"</config>"
        )

    # ── Helpers ────────────────────────────────────────────────────

    def _parse_xml(self, raw: str, final_locator: str) -> dict[str, str | None]:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            _LOGGER.warning("Malformed XML from thermostat (%s): %s", final_locator, err)
            return {}

        received: dict[str, str | None] = {}
        if "/equipment_events" in final_locator:
            # Only keep first occurrence of each tag (latest event only)
            for child in root.iter():
                if child.tag not in received:
                    received[child.tag] = child.text
        else:
            for child in root.iter():
                received[child.tag] = child.text

        return received

    @staticmethod
    def _utcnow() -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _xml_response(xml: str) -> web.Response:
        return web.Response(
            text=xml,
            content_type="application/xml",
            headers={"Connection": "keep-alive"},
        )
