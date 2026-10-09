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
class ObserverSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value accessor."""

    value_fn: Callable[[ThermostatData], Any]
    attrs_fn: Callable[[ThermostatData], dict[str, Any] | None] | None = None


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
        name="Filter Life Remaining",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
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
    ObserverSensorDescription(
        key="outdoor_unit_type",
        name="Outdoor Unit Type",
        icon="mdi:hvac",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.reported.get("odutype"),
    ),
    ObserverSensorDescription(
        key="indoor_unit_type",
        name="Indoor Unit Type",
        icon="mdi:furnace",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.reported.get("idutype"),
    ),
    ObserverSensorDescription(
        key="inducer_rpm",
        name="Inducer RPM",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="rpm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d._float("inducerrpm"),
    ),
    ObserverSensorDescription(
        key="schedule_period",
        name="Schedule Period",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.reported.get("period"),
    ),
    ObserverSensorDescription(
        key="hold_until",
        name="Hold Until",
        icon="mdi:timer-sand",
        value_fn=lambda d: d.hold_end_text,
    ),
    ObserverSensorDescription(
        key="last_fault",
        name="Last Fault",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_fault_text,
        attrs_fn=lambda d: (
            {
                "code": d.equipment_history[0].get("code"),
                "equipment": d.equipment_history[0].get("equip"),
                "occurrences": d.equipment_history[0].get("occurrences"),
                "time": d.equipment_history[0].get("time"),
                "history": [
                    {
                        k: e.get(k)
                        for k in ("description", "code", "equip", "source", "occurrences", "time", "active")
                    }
                    for e in d.equipment_history
                ],
            }
            if d.equipment_history
            else None
        ),
    ),
    ObserverSensorDescription(
        key="next_schedule_change",
        name="Next Schedule Change",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-arrow-right",
        value_fn=lambda d: (c := d.next_schedule_change()) and c[0],
        attrs_fn=lambda d: (
            {"heat_setpoint": c[1], "cool_setpoint": c[2], "period": c[3]}
            if (c := d.next_schedule_change())
            else None
        ),
    ),
    ObserverSensorDescription(
        key="equipment_stage",
        name="Equipment Stage",
        icon="mdi:numeric",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.equipment_stage,
        attrs_fn=lambda d: {
            "min_cool_stage": d.reported.get("mincoolstage"),
            "max_cool_stage": d.reported.get("maxcoolstage"),
            "min_heat_stage": d.reported.get("minheatstage"),
            "max_heat_stage": d.reported.get("maxheatstage"),
        },
    ),
    ObserverSensorDescription(
        key="deadband",
        name="Deadband",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="°F",
        value_fn=lambda d: d.config_number("dealer_config", "cfgdead"),
    ),
    ObserverSensorDescription(
        key="changeover",
        name="Changeover Setting",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("dealer_config", "cfgchgover"),
    ),
    ObserverSensorDescription(
        key="cool_lockout",
        name="Cool Lockout Setting",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("dealer_config", "lascoollckout"),
    ),
    ObserverSensorDescription(
        key="heat_lockout",
        name="Heat Lockout Setting",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("dealer_config", "lasheatlckout"),
    ),
    ObserverSensorDescription(
        key="temp_offset",
        name="Room Temperature Offset",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="°F",
        value_fn=lambda d: d.config_number("dealer_config", "tempoffset"),
    ),
    ObserverSensorDescription(
        key="filter_interval",
        name="Filter Interval",
        icon="mdi:air-filter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement="h",
        value_fn=lambda d: d.config_number("dealer_config", "filterinterval"),
    ),
    ObserverSensorDescription(
        key="indoor_capacity",
        name="Indoor Unit Capacity",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("profile", "iducapacity"),
    ),
    ObserverSensorDescription(
        key="outdoor_capacity",
        name="Outdoor Unit Capacity",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("profile", "oducapacity"),
    ),
    ObserverSensorDescription(
        key="indoor_stages",
        name="Indoor Unit Stages",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("profile", "idustages"),
    ),
    ObserverSensorDescription(
        key="service_level",
        name="Service Level",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("reported", "servicelvl"),
    ),
    ObserverSensorDescription(
        key="schedule_day",
        name="Schedule Day",
        icon="mdi:calendar",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.config_number("reported", "day"),
    ),
    ObserverSensorDescription(
        key="cooling_runtime_total",
        name="Cooling Runtime (Lifetime)",
        icon="mdi:clock-time-eight-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda d: d.history_number("odu", "coolhours"),
    ),
    ObserverSensorDescription(
        key="cooling_cycles_total",
        name="Cooling Cycles (Lifetime)",
        icon="mdi:sync",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.history_number("odu", "coolcycles"),
    ),
    ObserverSensorDescription(
        key="odu_on_hours",
        name="Outdoor Unit Powered-On Time",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.history_number("odu", "onhours"),
    ),
    ObserverSensorDescription(
        key="odu_power_cycles",
        name="Outdoor Unit Power Cycles",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.history_number("odu", "powercycles"),
    ),
    ObserverSensorDescription(
        key="odu_heat_hours",
        name="Outdoor Unit Heating Runtime (Lifetime)",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.history_number("odu", "heathours"),
    ),
    ObserverSensorDescription(
        key="odu_heat_cycles",
        name="Outdoor Unit Heating Cycles (Lifetime)",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.history_number("odu", "heatcycles"),
    ),
    ObserverSensorDescription(
        key="odu_defrost_cycles",
        name="Outdoor Unit Defrost Cycles",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.history_number("odu", "defrostcycles"),
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


class ObserverSensorEntity(ObserverEntity, SensorEntity):
    """A sensor entity for the Observer Thermostat."""

    def __init__(
        self,
        data: ThermostatData,
        device_name: str,
        serial: str,
        description: ObserverSensorDescription,
    ) -> None:
        super().__init__(data, device_name, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def available(self) -> bool:
        # Keep the "last communication" timestamp readable while offline
        if self.entity_description.key == "last_communication":
            return True
        return super().available

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self._data) if attrs_fn else None
