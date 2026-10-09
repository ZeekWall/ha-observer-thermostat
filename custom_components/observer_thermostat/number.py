"""Number platform for Observer Thermostat configurable values."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
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


@dataclass(frozen=True, kw_only=True)
class ObserverNumberDescription(NumberEntityDescription):
    """Extends NumberEntityDescription with value accessors."""

    value_fn: Callable[[ThermostatData], float]
    set_fn: Callable[[ThermostatData, float], None]
    # False hides the control (unavailable) when the system doesn't support it
    supported_fn: Callable[[ThermostatData], bool] = lambda d: True


NUMBER_DESCRIPTIONS: tuple[ObserverNumberDescription, ...] = (
    ObserverNumberDescription(
        key="hum_setpoint",
        name="Humidification Setpoint",
        entity_registry_enabled_default=False,
        supported_fn=lambda d: d.humidifier_supported is not False,
        icon="mdi:water-plus",
        native_min_value=20,
        native_max_value=65,
        native_step=1,
        native_unit_of_measurement="%",
        mode=NumberMode.SLIDER,
        value_fn=lambda d: float(d.hum_setpoint),
        set_fn=lambda d, v: d.set_hum_setpoint(int(v)),
    ),
    ObserverNumberDescription(
        key="dehum_setpoint",
        name="Dehumidification Setpoint",
        entity_registry_enabled_default=False,
        icon="mdi:water-minus",
        native_min_value=20,
        native_max_value=65,
        native_step=1,
        native_unit_of_measurement="%",
        mode=NumberMode.SLIDER,
        value_fn=lambda d: float(d.dehum_setpoint),
        set_fn=lambda d, v: d.set_dehum_setpoint(int(v)),
    ),
    ObserverNumberDescription(
        key="blight",
        name="Backlight Brightness",
        icon="mdi:brightness-5",
        native_min_value=0,
        native_max_value=100,
        native_step=10,
        native_unit_of_measurement="%",
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: float(d.blight),
        set_fn=lambda d, v: d.set_blight(int(v)),
    ),
    ObserverNumberDescription(
        key="otmr",
        name="Hold Duration",
        icon="mdi:timer",
        native_min_value=0,
        native_max_value=240,
        native_step=15,
        native_unit_of_measurement="min",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: float(d.otmr),
        set_fn=lambda d, v: d.set_otmr(int(v)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Observer Thermostat number entities."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data: ThermostatData = domain_data["data"]
    name = entry.data.get(CONF_THERMOSTAT_NAME, "Thermostat")
    serial = entry.data[CONF_THERMOSTAT_SERIAL]

    async_add_entities(
        ObserverNumberEntity(data, name, serial, desc)
        for desc in NUMBER_DESCRIPTIONS
    )


class ObserverNumberEntity(ObserverEntity, NumberEntity):
    """A configurable number entity for the Observer Thermostat."""

    def __init__(
        self,
        data: ThermostatData,
        device_name: str,
        serial: str,
        description: ObserverNumberDescription,
    ) -> None:
        super().__init__(data, device_name, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.supported_fn(self._data)

    @property
    def native_value(self) -> float:
        return self.entity_description.value_fn(self._data)

    async def async_set_native_value(self, value: float) -> None:
        self.entity_description.set_fn(self._data, value)
        self.async_write_ha_state()
