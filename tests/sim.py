"""A fake Observer thermostat that talks to the integration's API server."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

import aiohttp

SERIAL = "TESTSERIAL123"


class FakeThermostat:
    """Polls /status like the real device and applies /config when told to.

    ``reject`` lists fields the "firmware" refuses to apply; ``apply_late``
    makes it report one more stale status before adopting a fetched config.
    """

    def __init__(self, port: int, **state: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.state = {
            "rt": "72", "rh": "40", "mode": "heat", "fan": "auto",
            "hold": "off", "htsp": "68", "clsp": "75",
            "coolicon": "off", "heaticon": "off", "fanicon": "off",
            **state,
        }
        self.reject: set[str] = set()
        self.apply_late = False
        self._staged: dict[str, str] | None = None
        self.last_config: dict[str, str] = {}
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        await self.session.close()

    def _status_body(self) -> str:
        inner = "".join(f"<{k}>{v}</{k}>" for k, v in self.state.items())
        return "data=" + quote(f"<status><zones><zone id='1'>{inner}</zone></zones></status>")

    async def poll(self) -> bool:
        """One /status cycle. Returns True if the server announced config changes."""
        # Adopt a previously fetched config before reporting (unless late)
        if self._staged is not None and not self.apply_late:
            self._apply(self._staged)
            self._staged = None
        elif self._staged is not None:
            staged, self._staged = self._staged, None
            self.apply_late = False
            await self._post_status()
            self._apply(staged)
            return await self._post_status_has_changes()

        return await self._post_status_has_changes(follow=True)

    async def _post_status(self) -> str:
        async with self.session.post(
            f"{self.base}/systems/{SERIAL}/status",
            data=self._status_body(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            return await resp.text()

    async def _post_status_has_changes(self, follow: bool = False) -> bool:
        text = await self._post_status()
        changes = "<configHasChanges>on</configHasChanges>" in text
        if changes and follow:
            await self.fetch_config()
        return changes

    async def fetch_config(self) -> None:
        async with self.session.get(f"{self.base}/systems/{SERIAL}/config") as resp:
            root = ET.fromstring(await resp.text())
        flat = {c.tag: (c.text or "") for c in root.iter() if len(c) == 0 and "}" not in c.tag}
        self.last_config = flat
        self._staged = {k: flat[k] for k in ("mode", "fan", "hold", "htsp", "clsp") if k in flat}

    def _apply(self, config: dict[str, str]) -> None:
        for key, val in config.items():
            if key not in self.reject:
                self.state[key] = val

    async def post(self, endpoint: str, body: str) -> int:
        async with self.session.post(f"{self.base}/systems/{SERIAL}/{endpoint}", data=body) as resp:
            return resp.status
