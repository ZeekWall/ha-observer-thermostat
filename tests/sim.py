"""A fake Observer thermostat that talks to the integration's API server.

Behaviour modelled on captures of the real unit:
* polls ``POST /status`` and, if told ``configHasChanges=on``, GETs ``/config``;
* after applying a config it POSTs its full config to ``/systems/<serial>``;
* setpoints only exist in that config while a hold is on;
* a hold whose ``otmr`` is empty or not later than the thermostat's clock
  expires immediately (the reported "changes don't stick" bug).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

import aiohttp

SERIAL = "TESTSERIAL123"
CLOCK_MINUTES = 20 * 60 + 59  # 20:59, a Thursday (schedule day 5)
PERIODS = ("06:30", "09:30", "18:30", "22:30")


def _hhmm_minutes(text: str) -> int | None:
    try:
        hh, mm = text.split(":")
        return int(hh) * 60 + int(mm)
    except ValueError:
        return None


class FakeThermostat:
    def __init__(self, port: int, **state: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.status = {
            "rt": "72", "rh": "40", "mode": "heat", "fan": "auto",
            "hold": "off", "htsp": "68", "clsp": "75", "day": "5", "period": "3",
            "coolicon": "off", "heaticon": "off", "fanicon": "off",
            **state,
        }
        self.otmr = ""
        self.top = {
            "timeFormat": "12", "dst": "on", "volume": "high", "soundType": "click",
            "scrLockout": "off", "scrLockoutCode": "0000",
            "humSetpoint": "45", "dehumSetpoint": "45", "blight": "30",
        }
        self.reject: set[str] = set()
        self.last_config: ET.Element | None = None
        self.last_config_xml = ""
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        await self.session.close()

    # ── status cycle ───────────────────────────────────────────────

    def _status_body(self) -> str:
        top = "".join(f"<{k}>{v}</{k}>" for k, v in self.status.items() if k not in ("rt", "hold", "htsp", "clsp", "period", "coolicon", "heaticon", "fanicon"))
        zone = "".join(f"<{k}>{self.status[k]}</{k}>" for k in ("rt", "hold", "htsp", "clsp", "period", "coolicon", "heaticon", "fanicon"))
        xml = f"<status version='1.7'>{top}<zones><zone id='1'>{zone}</zone><zone id='2'/></zones></status>"
        return "data=" + quote(xml)

    async def poll(self) -> bool:
        """One /status cycle. Returns True if the server announced config changes."""
        async with self.session.post(
            f"{self.base}/systems/{SERIAL}/status",
            data=self._status_body(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            text = await resp.text()
        if "<configHasChanges>on</configHasChanges>" in text:
            await self.fetch_and_apply_config()
            return True
        return False

    # ── config exchange ────────────────────────────────────────────

    async def fetch_and_apply_config(self) -> None:
        async with self.session.get(f"{self.base}/systems/{SERIAL}/config") as resp:
            self.last_config_xml = await resp.text()
        self.last_config = ET.fromstring(self.last_config_xml)
        self._apply(self.last_config)
        await self.post_echo()

    def _apply(self, config: ET.Element) -> None:
        for key in ("mode", "fan"):
            if key not in self.reject and config.findtext(key):
                self.status[key] = config.findtext(key)
        for key in self.top:
            if key not in self.reject and config.findtext(key) is not None:
                self.top[key] = config.findtext(key)

        zone = next((z for z in config.iterfind("zones/zone") if z.get("id") == "1"), None)
        if zone is None:
            return
        hold, otmr = zone.findtext("hold"), (zone.findtext("otmr") or "").strip()
        if "hold" in self.reject:
            return
        if hold == "on":
            end = _hhmm_minutes(otmr)
            if end is None or end <= CLOCK_MINUTES:
                self.status["hold"], self.otmr = "off", ""  # expires immediately
                return
            self.status["hold"], self.otmr = "on", otmr
            for key in ("htsp", "clsp"):
                if key not in self.reject and zone.findtext(key):
                    self.status[key] = zone.findtext(key)
        elif hold == "off":
            self.status["hold"], self.otmr = "off", ""

    def config_echo_xml(self) -> str:
        hold_on = self.status["hold"] == "on"
        periods = "".join(
            f"<period id='{i}'><time>{t}</time><htsp>60</htsp><clsp>64</clsp></period>"
            for i, t in enumerate(PERIODS, 1)
        )
        days = "".join(f"<day id='{d}'>{periods}</day>" for d in range(1, 8))
        setpoints = (
            f"<htsp>{self.status['htsp']}</htsp><clsp>{self.status['clsp']}</clsp><otmr>{self.otmr}</otmr>"
            if hold_on else ""
        )
        top = "".join(f"<{k}>{v}</{k}>" for k, v in self.top.items())
        return (
            f"<system version='1.7'><config><mode>{self.status['mode']}</mode>"
            f"<fan>{self.status['fan']}</fan><zones><zone id='1'><name>Zone 1</name>"
            f"<hold>{self.status['hold']}</hold>{setpoints}<program>{days}</program></zone>"
            f"<zone id='2'/></zones>{top}</config></system>"
        )

    async def post_echo(self) -> None:
        await self.post("", self.config_echo_xml(), form=False)

    async def post(self, endpoint: str, body: str, form: bool = True) -> int:
        url = f"{self.base}/systems/{SERIAL}" + (f"/{endpoint}" if endpoint else "")
        async with self.session.post(url, data=body) as resp:
            return resp.status
