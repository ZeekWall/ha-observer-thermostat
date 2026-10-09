"""Observer Communicating Thermostat integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .capture import CaptureLog
from .const import (
    CAPTURE_DIR_NAME,
    CONF_CAPTURE_TO_FILE,
    CONF_SERVER_PORT,
    CONF_THERMOSTAT_SERIAL,
    DEFAULT_PORT,
    DOMAIN,
    SIGNAL_THERMOSTAT_UPDATE,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
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

    # Restore local-only settings and last-known thermostat values
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{serial}")
    data.load_store(await store.async_load())
    data.persist_callback = lambda: store.async_delay_save(
        data.to_store, STORAGE_SAVE_DELAY
    )

    capture = CaptureLog(serial)
    if entry.options.get(CONF_CAPTURE_TO_FILE, True):
        capture_dir = hass.config.path(CAPTURE_DIR_NAME)
        try:
            await hass.async_add_executor_job(capture.start_file, capture_dir)
        except OSError as err:
            _LOGGER.warning("Capture file logging disabled (%s): %s", capture_dir, err)

    announced_firmware = data.firmware

    @callback
    def _update() -> None:
        nonlocal announced_firmware
        if data.firmware and data.firmware != announced_firmware:
            announced_firmware = data.firmware
            registry = dr.async_get(hass)
            if device := registry.async_get_device(identifiers={(DOMAIN, serial)}):
                registry.async_update_device(device.id, sw_version=data.firmware)
        async_dispatcher_send(hass, f"{SIGNAL_THERMOSTAT_UPDATE}_{serial}")

    server = ObserverThermostatServer(
        data=data, port=port, update_callback=_update, capture=capture
    )

    try:
        await server.start()
    except OSError as err:
        await hass.async_add_executor_job(capture.stop_file)
        raise ConfigEntryNotReady(
            f"Could not start API server on port {port}: {err}. "
            "Check that the port is not already in use and reconfigure if needed."
        ) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "data": data,
        "server": server,
        "capture": capture,
        "store": store,
    }

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data = hass.data[DOMAIN].pop(entry.entry_id)
        await domain_data["server"].stop()
        await domain_data["store"].async_save(domain_data["data"].to_store())
        await hass.async_add_executor_job(domain_data["capture"].stop_file)
    return unload_ok
