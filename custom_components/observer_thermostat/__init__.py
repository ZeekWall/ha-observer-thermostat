"""Observer Communicating Thermostat integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_SERVER_PORT,
    CONF_THERMOSTAT_SERIAL,
    DEFAULT_PORT,
    DOMAIN,
    SIGNAL_THERMOSTAT_UPDATE,
)
from .server import ObserverThermostatServer, ThermostatData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Observer Thermostat from a config entry."""
    serial = entry.data[CONF_THERMOSTAT_SERIAL]
    port = entry.data.get(CONF_SERVER_PORT, DEFAULT_PORT)

    # Use HA's known local IP — more reliable than parsing network.get_url()
    local_ip = hass.config.api.local_ip
    api_address = f"{local_ip}:{port}" if port != 80 else local_ip

    data = ThermostatData(serial=serial, api_address=api_address)

    @callback
    def _update() -> None:
        async_dispatcher_send(hass, f"{SIGNAL_THERMOSTAT_UPDATE}_{serial}")

    server = ObserverThermostatServer(data=data, port=port, update_callback=_update)

    try:
        await server.start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Could not start API server on port {port}: {err}. "
            "Check that the port is not already in use and reconfigure if needed."
        ) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"data": data, "server": server}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data = hass.data[DOMAIN].pop(entry.entry_id)
        await domain_data["server"].stop()
    return unload_ok
