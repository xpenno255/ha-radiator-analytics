"""DataUpdateCoordinator for Radiator Analytics."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .analyzer import AnalyticsResult, compute_analytics
from .const import DOMAIN
from .store import RadiatorAnalyticsStore

_LOGGER = logging.getLogger(__name__)


class RadiatorAnalyticsCoordinator(DataUpdateCoordinator[AnalyticsResult]):
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

    async def _async_update_data(self) -> AnalyticsResult:
        """Run the analytics computation."""
        # Get sessions within the analysis window
        sessions = self._store.get_sessions_in_window(self._analysis_window_days)

        _LOGGER.debug(
            "Running analytics on %d sessions across %d zones (window: %d days)",
            len(sessions),
            len(self._monitored_zones),
            self._analysis_window_days,
        )

        # Run compute in executor since it can be CPU-intensive with many sessions
        result = await self.hass.async_add_executor_job(
            compute_analytics,
            sessions,
            self._monitored_zones,
            self._analysis_window_days,
        )

        # Prune old sessions and save
        self._store.prune(self._analysis_window_days * 2)
        await self._store.async_save()

        return result
