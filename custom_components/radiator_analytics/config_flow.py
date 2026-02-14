"""Config flow for Radiator Analytics."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import (
    CONF_ANALYSIS_WINDOW,
    CONF_MONITORED_ZONES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ANALYSIS_WINDOW,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_ANALYSIS_WINDOW,
    MAX_UPDATE_INTERVAL,
    MIN_ANALYSIS_WINDOW,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _find_ramses_zones(hass) -> dict[str, str]:
    """Find all Ramses CC climate entities using the entity registry."""
    zones = {}
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        if entry.domain == "climate" and entry.platform == "ramses_cc":
            # Skip the controller entity — it's not a zone
            if "controller" in entry.entity_id:
                continue
            state = hass.states.get(entry.entity_id)
            if state:
                friendly_name = state.attributes.get("friendly_name", entry.entity_id)
            else:
                friendly_name = entry.name or entry.original_name or entry.entity_id
            zones[entry.entity_id] = friendly_name
    return zones


class RadiatorAnalyticsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Radiator Analytics."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._zones: dict[str, str] = {}
        self._selected_zones: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Handle the zone selection step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        self._zones = _find_ramses_zones(self.hass)

        if not self._zones:
            return self.async_abort(reason="no_zones_found")

        errors = {}

        if user_input is not None:
            selected = user_input.get(CONF_MONITORED_ZONES, [])
            if not selected:
                errors["base"] = "no_zones_selected"
            else:
                self._selected_zones = selected
                return await self.async_step_settings()

        zone_options = {
            entity_id: f"{name} ({entity_id})"
            for entity_id, name in self._zones.items()
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MONITORED_ZONES,
                        default=list(self._zones.keys()),
                    ): cv.multi_select(zone_options),
                }
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Handle the settings step."""
        if user_input is not None:
            return self.async_create_entry(
                title="Radiator Analytics",
                data={
                    CONF_MONITORED_ZONES: self._selected_zones,
                    CONF_ANALYSIS_WINDOW: user_input.get(
                        CONF_ANALYSIS_WINDOW, DEFAULT_ANALYSIS_WINDOW
                    ),
                    CONF_UPDATE_INTERVAL: user_input.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                    ),
                },
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ANALYSIS_WINDOW,
                        default=DEFAULT_ANALYSIS_WINDOW,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_ANALYSIS_WINDOW, max=MAX_ANALYSIS_WINDOW),
                    ),
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=DEFAULT_UPDATE_INTERVAL,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return RadiatorAnalyticsOptionsFlow(config_entry)


class RadiatorAnalyticsOptionsFlow(OptionsFlow):
    """Handle options flow for Radiator Analytics."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Handle the options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        zones = _find_ramses_zones(self.hass)
        zone_options = {
            entity_id: f"{name} ({entity_id})"
            for entity_id, name in zones.items()
        }

        current_zones = self._config_entry.data.get(CONF_MONITORED_ZONES, [])
        current_window = self._config_entry.data.get(
            CONF_ANALYSIS_WINDOW, DEFAULT_ANALYSIS_WINDOW
        )
        current_interval = self._config_entry.data.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MONITORED_ZONES,
                        default=current_zones,
                    ): cv.multi_select(zone_options),
                    vol.Required(
                        CONF_ANALYSIS_WINDOW,
                        default=current_window,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_ANALYSIS_WINDOW, max=MAX_ANALYSIS_WINDOW),
                    ),
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=current_interval,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                    ),
                }
            ),
        )
