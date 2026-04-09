"""Sensor platform for Observer Thermostat."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
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
class ObserverSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value accessor."""

    value_fn: Callable[[ThermostatData], Any]


SENSOR_DESCRIPTIONS: tuple[ObserverSensorDescription, ...] = (
    ObserverSensorDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda d: d.temperature,
    ),
    ObserverSensorDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.humidity,
    ),
    ObserverSensorDescription(
        key="operating_mode",
        name="Operating Mode",
        icon="mdi:home-thermometer",
        value_fn=lambda d: d.mode,
    ),
    ObserverSensorDescription(
        key="fan_mode",
        name="Fan Mode",
        icon="mdi:fan",
        value_fn=lambda d: d.fan_mode,
    ),
    ObserverSensorDescription(
        key="state",
        name="State",
        icon="mdi:home-thermometer",
        value_fn=lambda d: (
            "Cooling" if d.is_cooling
            else "Heating" if d.is_heating
            else "Idle Fan" if d.fan_running
            else "Idle"
        ),
    ),
    ObserverSensorDescription(
        key="setpoint",
        name="Setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        icon="mdi:thermometer",
        value_fn=lambda d: d.target_temperature,
    ),
    ObserverSensorDescription(
        key="fan_status",
        name="Fan Status",
        icon="mdi:fan",
        value_fn=lambda d: d.current.get("fanicon"),
    ),
    ObserverSensorDescription(
        key="hold",
        name="Hold",
        icon="mdi:gesture-tap-hold",
        value_fn=lambda d: d.hold,
    ),
    ObserverSensorDescription(
        key="filter_hours_remain",
        name="Filter Hours Remaining",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:clock",
        value_fn=lambda d: d.filter_hours_remain,
    ),
    ObserverSensorDescription(
        key="outdoor_coil_temp",
        name="Outdoor Coil Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        icon="mdi:hvac",
        value_fn=lambda d: d.outdoor_coil_temp,
    ),
    ObserverSensorDescription(
        key="outdoor_ambient_temp",
        name="Outdoor Ambient Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        value_fn=lambda d: d.outdoor_ambient_temp,
    ),
    ObserverSensorDescription(
        key="indoor_cfm",
        name="Indoor CFM",
        icon="mdi:fan",
        native_unit_of_measurement="cfm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.indoor_cfm,
    ),
    # Equipment event — description and timestamp as separate sensors
    ObserverSensorDescription(
        key="equipment_event",
        name="Active Equipment Event",
        icon="mdi:alert",
        value_fn=lambda d: d.latest_equip_description,
    ),
    ObserverSensorDescription(
        key="equipment_event_time",
        name="Equipment Event Time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.latest_equip_time,
    ),
    ObserverSensorDescription(
        key="last_communication",
        name="Last Communication",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_communication,
    ),
    # Operating status — raw value from thermostat, useful for diagnostics
    ObserverSensorDescription(
        key="opstat",
        name="Operating Status",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.opstat,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Observer Thermostat sensor entities."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data: ThermostatData = domain_data["data"]
    name = entry.data.get(CONF_THERMOSTAT_NAME, "Thermostat")
    serial = entry.data[CONF_THERMOSTAT_SERIAL]

    async_add_entities(
        ObserverSensorEntity(data, name, serial, desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class ObserverSensorEntity(SensorEntity):
    """A sensor entity for the Observer Thermostat."""

    _attr_has_entity_name = True

    def __init__(
        self,
        data: ThermostatData,
        device_name: str,
        serial: str,
        description: ObserverSensorDescription,
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
            sw_version=data.firmware,
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
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._data)
