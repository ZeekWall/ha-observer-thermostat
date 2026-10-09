"""Diagnostics for Observer Thermostat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"thermostat_serial", "serial", "pin", "phone", "email", "street1", "street2", "scrLockoutCode", "name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics: state, pending changes and recent raw traffic."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data = domain_data["data"]
    capture = domain_data["capture"]

    def _redact(obj: Any) -> Any:
        return capture.redact(obj) if isinstance(obj, str) else obj

    rare_traffic = await hass.async_add_executor_job(capture.rare_from_files)

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
        "stored": async_redact_data(
            {
                k: v
                for k, v in data.to_store().items()
                if k
                not in (
                    "echo_xml",
                    "profile",
                    "dealer_config",
                    "idu_config",
                    "odu_config",
                    "equipment_history",
                )  # reported in their own sections
            },
            TO_REDACT,
        ),
        "profile": async_redact_data(
            {k: _redact(v) for k, v in data.profile.items()}, TO_REDACT
        ),
        "dealer_config": data.dealer_config,
        "idu_config": data.idu_config,
        "odu_config": data.odu_config,
        "equipment_history": data.equipment_history,
        "next_schedule_change": (
            str(data.next_schedule_change()) if data.next_schedule_change() else None
        ),
        "raw_last": {
            ep: (
                async_redact_data(
                    {k: _redact(v) for k, v in payload.items()}, TO_REDACT
                )
                if isinstance(payload, dict)
                else _redact(payload)
            )
            for ep, payload in data.raw_last.items()
        },
        "config_echo_xml": capture.redact(data.echo_xml or ""),
        "program": data.program(),
        "captures": capture.entries(200),
        # Everything that isn't routine polling, read back from the capture files
        "rare_traffic": rare_traffic,
    }
