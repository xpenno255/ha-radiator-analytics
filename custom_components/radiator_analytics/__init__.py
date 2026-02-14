"""Radiator Analytics integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .backfill import async_backfill_from_recorder
from .const import (
    CONF_ANALYSIS_WINDOW,
    CONF_MONITORED_ZONES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ANALYSIS_WINDOW,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import RadiatorAnalyticsCoordinator
from .session_tracker import HeatingSessionTracker
from .store import RadiatorAnalyticsStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Radiator Analytics from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    monitored_zones = entry.data.get(CONF_MONITORED_ZONES, [])
    analysis_window = entry.data.get(CONF_ANALYSIS_WINDOW, DEFAULT_ANALYSIS_WINDOW)
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

    # Initialize persistent store
    store = RadiatorAnalyticsStore(hass)
    await store.async_load()

    # Backfill from recorder if store is empty
    if store.is_empty:
        _LOGGER.info("No existing session data — running historical backfill")
        backfilled = await async_backfill_from_recorder(
            hass, store, monitored_zones, analysis_window
        )
        if backfilled > 0:
            await store.async_save()

    # Create coordinator
    coordinator = RadiatorAnalyticsCoordinator(
        hass,
        store,
        monitored_zones,
        analysis_window,
        update_interval,
    )

    # Run first refresh
    await coordinator.async_config_entry_first_refresh()

    # Start session tracker
    tracker = HeatingSessionTracker(hass, store, monitored_zones)
    tracker.start()

    # Store references for unload
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "tracker": tracker,
        "store": store,
    }

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    _LOGGER.info(
        "Radiator Analytics setup complete: %d zones, %d-day window, %d-min interval",
        len(monitored_zones),
        analysis_window,
        update_interval,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, {})
        tracker = data.get("tracker")
        if tracker:
            tracker.stop()
        store = data.get("store")
        if store:
            await store.async_save()

    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)
