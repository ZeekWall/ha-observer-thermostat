"""Diagnostics for Observer Thermostat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"thermostat_serial", "serial"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics: state, pending changes and recent raw traffic."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data = domain_data["data"]
    capture = domain_data["capture"]

    def _redact(obj: Any) -> Any:
        return capture.redact(obj) if isinstance(obj, str) else obj

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "thermostat_ip": data.thermostat_ip,
        "firmware": data.firmware,
        "last_communication": (
            data.last_communication.isoformat() if data.last_communication else None
        ),
        "reported": {k: _redact(v) for k, v in data.reported.items()},
        "desired": {
            k: {
                "value": p.value,
                "set_at": p.set_at.isoformat(),
                "sent_at": p.sent_at.isoformat() if p.sent_at else None,
                "attempts": p.attempts,
            }
            for k, p in data.desired.items()
        },
        "stored": async_redact_data(data.to_store(), TO_REDACT),
        "profile": {k: _redact(v) for k, v in data.profile.items()},
        "raw_last": {
            ep: {k: _redact(v) for k, v in payload.items()}
            for ep, payload in data.raw_last.items()
        },
        "captures": capture.entries(200),
    }
