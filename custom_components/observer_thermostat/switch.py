"""Switch platform for Observer Thermostat."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_THERMOSTAT_NAME,
    CONF_THERMOSTAT_SERIAL,
    DOMAIN,
    SIGNAL_THERMOSTAT_UPDATE,
)
from .server import ThermostatData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Observer Thermostat switch entities."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data: ThermostatData = domain_data["data"]
    name = entry.data.get(CONF_THERMOSTAT_NAME, "Thermostat")
    serial = entry.data[CONF_THERMOSTAT_SERIAL]
    async_add_entities([ObserverScreenLockoutSwitch(data, name, serial)])


class ObserverScreenLockoutSwitch(SwitchEntity):
    """Switch to enable/disable the thermostat's physical screen lockout."""

    _attr_has_entity_name = True
    _attr_name = "Screen Lockout"
    _attr_icon = "mdi:lock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: ThermostatData, device_name: str, serial: str) -> None:
        self._data = data
        self._serial = serial
        self._attr_unique_id = f"{serial}_scr_lockout"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=device_name,
            manufacturer="Observer",
            model="TSTAT0201CW",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_THERMOSTAT_UPDATE}_{self._serial}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._data.scr_lockout

    async def async_turn_on(self, **kwargs) -> None:
        self._data.set_scr_lockout(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._data.set_scr_lockout(False)
        self.async_write_ha_state()
