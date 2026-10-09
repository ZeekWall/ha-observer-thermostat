"""Switch platform for Observer Thermostat."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_THERMOSTAT_NAME,
    CONF_THERMOSTAT_SERIAL,
    DOMAIN,
)
from .entity import ObserverEntity
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
    async_add_entities(
        [
            ObserverScreenLockoutSwitch(data, name, serial),
            ObserverIndefiniteHoldSwitch(data, name, serial),
        ]
    )


class ObserverScreenLockoutSwitch(ObserverEntity, SwitchEntity):
    """Switch to enable/disable the thermostat's physical screen lockout."""

    _attr_name = "Screen Lockout"
    _attr_icon = "mdi:lock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: ThermostatData, device_name: str, serial: str) -> None:
        super().__init__(data, device_name, serial)
        self._attr_unique_id = f"{serial}_scr_lockout"

    @property
    def is_on(self) -> bool:
        return self._data.scr_lockout

    async def async_turn_on(self, **kwargs) -> None:
        self._data.set_scr_lockout(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._data.set_scr_lockout(False)
        self.async_write_ha_state()


class ObserverIndefiniteHoldSwitch(ObserverEntity, SwitchEntity):
    """HA-side option: holds started from HA have no end time (until released)."""

    _attr_name = "Indefinite Hold"
    _attr_icon = "mdi:timer-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: ThermostatData, device_name: str, serial: str) -> None:
        super().__init__(data, device_name, serial)
        self._attr_unique_id = f"{serial}_hold_indefinite"

    @property
    def available(self) -> bool:
        return True  # local option, usable even while the thermostat is silent

    @property
    def is_on(self) -> bool:
        return self._data.hold_indefinite

    async def async_turn_on(self, **kwargs) -> None:
        self._data.set_hold_indefinite(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._data.set_hold_indefinite(False)
        self.async_write_ha_state()
