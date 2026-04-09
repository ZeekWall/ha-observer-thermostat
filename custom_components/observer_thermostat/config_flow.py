"""Config flow for Observer Thermostat integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import network

from .const import (
    CONF_SERVER_PORT,
    CONF_THERMOSTAT_NAME,
    CONF_THERMOSTAT_SERIAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
)


class ObserverThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Observer Thermostat."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        # Build a hint for the user showing the current HA IP
        try:
            ip_hint = self.hass.config.api.local_ip
        except Exception:
            ip_hint = "<HA_IP>"

        if user_input is not None:
            serial = user_input[CONF_THERMOSTAT_SERIAL].strip()

            # Prevent duplicate entries for the same serial
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input[CONF_THERMOSTAT_NAME],
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_THERMOSTAT_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_THERMOSTAT_SERIAL): str,
                vol.Required(CONF_SERVER_PORT, default=DEFAULT_PORT): vol.All(
                    int, vol.Range(min=1024, max=65535)
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={"ip_hint": ip_hint},
            errors=errors,
        )
