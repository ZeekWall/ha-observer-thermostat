"""Climate platform for Observer Thermostat."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_THERMOSTAT_NAME,
    CONF_THERMOSTAT_SERIAL,
    DOMAIN,
    FAN_MODES,
    MAX_TEMP,
    MIN_TEMP,
    PRESET_HOLD,
    PRESET_SCHEDULE,
)
from .entity import ObserverEntity
from .server import ThermostatData

_LOGGER = logging.getLogger(__name__)

MODE_TO_HVAC: dict[str, HVACMode] = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "off": HVACMode.OFF,
    "auto": HVACMode.HEAT_COOL,
}

HVAC_TO_MODE: dict[HVACMode, str] = {
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.OFF: "off",
    HVACMode.HEAT_COOL: "auto",
}

ACTION_MAP: dict[str, HVACAction] = {
    "cooling": HVACAction.COOLING,
    "heating": HVACAction.HEATING,
    "idle": HVACAction.IDLE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Observer Thermostat climate entity."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    data: ThermostatData = domain_data["data"]
    name = entry.data.get(CONF_THERMOSTAT_NAME, "Thermostat")
    serial = entry.data[CONF_THERMOSTAT_SERIAL]
    async_add_entities([ObserverClimateEntity(data, name, serial)])


class ObserverClimateEntity(ObserverEntity, ClimateEntity):
    """Representation of an Observer Communicating Thermostat."""

    _attr_name = None  # Use device name as the entity name
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.HEAT_COOL]
    _attr_fan_modes = FAN_MODES
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_preset_modes = [PRESET_SCHEDULE, PRESET_HOLD]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, data: ThermostatData, name: str, serial: str) -> None:
        super().__init__(data, name, serial)
        self._attr_unique_id = serial

    # ── State properties ───────────────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        return self._data.temperature

    @property
    def current_humidity(self) -> float | None:
        return self._data.humidity

    @property
    def hvac_mode(self) -> HVACMode:
        return MODE_TO_HVAC.get(self._data.mode or "off", HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction:
        if self._data.mode == "off":
            return HVACAction.OFF
        return ACTION_MAP.get(self._data.hvac_action, HVACAction.IDLE)

    @property
    def target_temperature(self) -> float | None:
        # In HEAT_COOL mode HA uses the range properties instead
        if self._data.mode == "auto":
            return None
        return self._data.target_temperature

    @property
    def target_temperature_low(self) -> float | None:
        """Heat setpoint — used as range low in HEAT_COOL mode."""
        return self._data.heating_setpoint

    @property
    def target_temperature_high(self) -> float | None:
        """Cool setpoint — used as range high in HEAT_COOL mode."""
        return self._data.cooling_setpoint

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose in-flight changes to make push problems visible."""
        return {
            "pending_changes": {k: p.value for k, p in self._data.desired.items()}
        }

    @property
    def fan_mode(self) -> str | None:
        return self._data.fan_mode

    @property
    def preset_mode(self) -> str:
        return PRESET_HOLD if self._data.hold == "on" else PRESET_SCHEDULE

    # ── Command handlers ───────────────────────────────────────────

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = HVAC_TO_MODE.get(hvac_mode)
        if mode:
            self._data.set_mode(mode)
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Restore the last non-off mode, defaulting to cool."""
        self._data.set_mode(self._data.last_on_mode or "cool")
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        self._data.set_mode("off")
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._data.set_fan_mode(fan_mode)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Handle both single-setpoint and dual-range (HEAT_COOL) temperature changes."""
        data = self._data
        if (hvac_mode := kwargs.get("hvac_mode")) is not None:
            if mode := HVAC_TO_MODE.get(hvac_mode):
                data.set_mode(mode)

        low = kwargs.get("target_temp_low")
        high = kwargs.get("target_temp_high")
        temp = kwargs.get(ATTR_TEMPERATURE)
        changed = False

        if low is not None:
            data.set_heat_setpoint(low)
            changed = True
        if high is not None:
            data.set_cool_setpoint(high)
            changed = True
        if temp is not None:
            if data.set_temperature(temp):
                changed = True
            else:
                _LOGGER.debug(
                    "Ignoring single target temperature in mode %s; "
                    "use the low/high range in heat_cool",
                    data.mode,
                )

        # Hold stops the schedule from overriding the new setpoint
        if changed and data.hold != "on":
            data.set_hold("on")

        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        hold_val = "on" if preset_mode == PRESET_HOLD else "off"
        self._data.set_hold(hold_val)
        self.async_write_ha_state()
