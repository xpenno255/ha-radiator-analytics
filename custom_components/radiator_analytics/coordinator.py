"""DataUpdateCoordinator for Radiator Analytics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .analyzer import AnalyticsResult, compute_analytics
from .const import DOMAIN
from .store import RadiatorAnalyticsStore

_LOGGER = logging.getLogger(__name__)

SHORT_WINDOW_DAYS = 1


@dataclass
class CoordinatorData:
    """Container for both long and short window analytics."""

    primary: AnalyticsResult
    short: AnalyticsResult


class RadiatorAnalyticsCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator that runs the analytics engine on a schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: RadiatorAnalyticsStore,
        monitored_zones: list[str],
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
        self._analysis_window_days = analysis_window_days

    @property
    def monitored_zones(self) -> list[str]:
        """Return the list of monitored zone entity IDs."""
        return self._monitored_zones

    async def _async_update_data(self) -> CoordinatorData:
        """Run the analytics computation for both windows."""
        # Get sessions for primary window
        sessions_primary = self._store.get_sessions_in_window(
            self._analysis_window_days
        )
        # Get sessions for 24h window
        sessions_short = self._store.get_sessions_in_window(SHORT_WINDOW_DAYS)

        _LOGGER.debug(
            "Running analytics: %d sessions (%d-day), %d sessions (24h) across %d zones",
            len(sessions_primary),
            self._analysis_window_days,
            len(sessions_short),
            len(self._monitored_zones),
        )

        # Run both computations
        primary = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_primary,
            self._monitored_zones,
            self._analysis_window_days,
        )
        short = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions_short,
            self._monitored_zones,
            SHORT_WINDOW_DAYS,
        )

        # Prune old sessions and save
        self._store.prune(self._analysis_window_days * 2)
        await self._store.async_save()

        return CoordinatorData(primary=primary, short=short)
