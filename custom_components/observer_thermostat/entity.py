"""Shared base entity for Observer Thermostat platforms."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, SIGNAL_THERMOSTAT_UPDATE
from .server import ThermostatData

AVAILABILITY_CHECK_INTERVAL = timedelta(seconds=30)


class ObserverEntity(Entity):
    """Updates on thermostat check-ins and goes unavailable when it is silent."""

    _attr_has_entity_name = True

    def __init__(self, data: ThermostatData, device_name: str, serial: str) -> None:
        self._data = data
        self._serial = serial
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
        # Availability depends on elapsed time, so re-evaluate periodically.
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_tick, AVAILABILITY_CHECK_INTERVAL
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_tick(self, _now) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._data.is_online
