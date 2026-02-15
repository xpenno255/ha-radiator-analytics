"""DataUpdateCoordinator for Radiator Analytics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .analyzer import (
    AnalyticsResult,
    ComparisonResult,
    compare_windows,
    compute_analytics,
)
from .const import DOMAIN
from .store import RadiatorAnalyticsStore

_LOGGER = logging.getLogger(__name__)

SHORT_WINDOW_DAYS = 1


@dataclass
class CoordinatorData:
    """Container for primary analytics, current 24h, and daily comparison."""

    primary: AnalyticsResult
    short: AnalyticsResult
    previous: AnalyticsResult
    comparison: ComparisonResult | None = None


class RadiatorAnalyticsCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator that runs the analytics engine on a schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: RadiatorAnalyticsStore,
        monitored_zones: list[str],
        zone_names: dict[str, str],
        analysis_window_days: int,
        update_interval_minutes: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self._store = store
        self._monitored_zones = monitored_zones
        self._zone_names = zone_names
        self._analysis_window_days = analysis_window_days
        self._comparison: ComparisonResult | None = None
        self._unsub_midnight: CALLBACK_TYPE | None = None

    @property
    def monitored_zones(self) -> list[str]:
        """Return the list of monitored zone entity IDs."""
        return self._monitored_zones

    @property
    def zone_names(self) -> dict[str, str]:
        """Return the zone entity_id -> friendly name mapping."""
        return self._zone_names

    def start_midnight_schedule(self) -> None:
        """Register the midnight comparison job."""
        self._unsub_midnight = async_track_time_change(
            self.hass, self._async_midnight_comparison, hour=0, minute=0, second=0
        )
        _LOGGER.debug("Midnight daily comparison scheduled")

    def stop_midnight_schedule(self) -> None:
        """Unregister the midnight comparison job."""
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def _async_midnight_comparison(self, _now: Any = None) -> None:
        """Run the daily comparison at midnight."""
        _LOGGER.info("Running midnight daily comparison")
        await self._run_comparison()
        # Trigger a coordinator update so sensors pick up the new comparison
        self.async_set_updated_data(self.data)

    async def _run_comparison(self) -> None:
        """Compute the comparison between current 24h and previous 24h."""
        self._refresh_zone_names()

        sessions_current = self._store.get_sessions_in_window(SHORT_WINDOW_DAYS)
        sessions_previous = self._store.get_sessions_in_range(2, 1)

        current = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_current,
            self._monitored_zones,
            SHORT_WINDOW_DAYS,
            self._zone_names,
        )
        previous = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_previous,
            self._monitored_zones,
            SHORT_WINDOW_DAYS,
            self._zone_names,
        )

        self._comparison = await self.hass.async_add_executor_job(
            compare_windows, current, previous, self._zone_names
        )
        _LOGGER.info(
            "Daily comparison complete: %s", self._comparison.summary
        )

    def _refresh_zone_names(self) -> None:
        """Refresh zone names from current HA state (picks up late-loading entities)."""
        for entity_id in self._monitored_zones:
            state = self.hass.states.get(entity_id)
            if state:
                name = state.attributes.get("friendly_name")
                if name:
                    self._zone_names[entity_id] = name

    async def _async_update_data(self) -> CoordinatorData:
        """Run the analytics computation for all windows."""
        # Refresh zone names in case entities loaded after our init
        self._refresh_zone_names()

        # Get sessions for each window
        sessions_primary = self._store.get_sessions_in_window(
            self._analysis_window_days
        )
        sessions_short = self._store.get_sessions_in_window(SHORT_WINDOW_DAYS)
        sessions_previous = self._store.get_sessions_in_range(2, 1)

        _LOGGER.debug(
            "Running analytics: %d sessions (%d-day), %d sessions (24h), "
            "%d sessions (prev 24h) across %d zones",
            len(sessions_primary),
            self._analysis_window_days,
            len(sessions_short),
            len(sessions_previous),
            len(self._monitored_zones),
        )

        # Run all three computations
        primary = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_primary,
            self._monitored_zones,
            self._analysis_window_days,
            self._zone_names,
        )
        short = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_short,
            self._monitored_zones,
            SHORT_WINDOW_DAYS,
            self._zone_names,
        )
        previous = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_previous,
            self._monitored_zones,
            SHORT_WINDOW_DAYS,
            self._zone_names,
        )

        # Prune old sessions and save
        self._store.prune(self._analysis_window_days * 2)
        await self._store.async_save()

        return CoordinatorData(
            primary=primary,
            short=short,
            previous=previous,
            comparison=self._comparison,
        )
