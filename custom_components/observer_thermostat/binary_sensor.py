"""Binary sensor platform for Observer Thermostat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_THERMOSTAT_NAME, CONF_THERMOSTAT_SERIAL, DOMAIN
from .entity import ObserverEntity
from .server import ThermostatData


@dataclass(frozen=True, kw_only=True)
class ObserverBinaryDescription(BinarySensorEntityDescription):
    """Extends BinarySensorEntityDescription with a value accessor."""

    value_fn: Callable[[ThermostatData], bool | None]


BINARY_DESCRIPTIONS: tuple[ObserverBinaryDescription, ...] = (
    ObserverBinaryDescription(
        key="filter_service",
        name="Filter Service Needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:air-filter",
        # The thermostat shows "Service filter" when its filter level reaches 0
        value_fn=lambda d: None if d.filter_hours_remain is None else d.filter_hours_remain <= 0,
    ),
    ObserverBinaryDescription(
        key="indoor_lockout",
        name="Indoor Unit Lockout",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            None
            if "lockoutactive" not in d.reported
            else d.reported["lockoutactive"] == "on"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Observer Thermostat binary sensors."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data: ThermostatData = domain_data["data"]
    name = entry.data.get(CONF_THERMOSTAT_NAME, "Thermostat")
    serial = entry.data[CONF_THERMOSTAT_SERIAL]
    async_add_entities(
        ObserverBinarySensor(data, name, serial, desc) for desc in BINARY_DESCRIPTIONS
    )


class ObserverBinarySensor(ObserverEntity, BinarySensorEntity):
    """A binary sensor for the Observer Thermostat."""

    def __init__(
        self,
        data: ThermostatData,
        device_name: str,
        serial: str,
        description: ObserverBinaryDescription,
    ) -> None:
        super().__init__(data, device_name, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._data)
