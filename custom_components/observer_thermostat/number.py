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


@dataclass(frozen=True, kw_only=True)
class ObserverNumberDescription(NumberEntityDescription):
    """Extends NumberEntityDescription with value accessors."""

    value_fn: Callable[[ThermostatData], float]
    set_fn: Callable[[ThermostatData, float], None]


NUMBER_DESCRIPTIONS: tuple[ObserverNumberDescription, ...] = (
    ObserverNumberDescription(
        key="hum_setpoint",
        name="Humidification Setpoint",
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
        native_max_value=10,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: float(d.blight),
        set_fn=lambda d, v: d.set_blight(int(v)),
    ),
    ObserverNumberDescription(
        key="otmr",
        name="Hold Override Timer",
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


class ObserverNumberEntity(NumberEntity):
    """A configurable number entity for the Observer Thermostat."""

    _attr_has_entity_name = True

    def __init__(
        self,
        data: ThermostatData,
        device_name: str,
        serial: str,
        description: ObserverNumberDescription,
    ) -> None:
        self._data = data
        self._serial = serial
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
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
    def native_value(self) -> float:
        return self.entity_description.value_fn(self._data)

    async def async_set_native_value(self, value: float) -> None:
        self.entity_description.set_fn(self._data, value)
        self.async_write_ha_state()
