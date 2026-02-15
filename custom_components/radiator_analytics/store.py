"""Persistent storage for heating session data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = DOMAIN


class RadiatorAnalyticsStore:
    """Manages persistent storage of heating session data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._sessions: list[dict[str, Any]] = []
        self._dirty = False

    @property
    def sessions(self) -> list[dict[str, Any]]:
        """Return the current sessions."""
        return self._sessions

    @property
    def is_empty(self) -> bool:
        """Return True if there are no stored sessions."""
        return len(self._sessions) == 0

    async def async_load(self) -> None:
        """Load sessions from persistent storage."""
        data = await self._store.async_load()
        if data and "sessions" in data:
            self._sessions = data["sessions"]
            _LOGGER.debug("Loaded %d sessions from store", len(self._sessions))
        else:
            self._sessions = []
            _LOGGER.debug("No existing session data found")

    async def async_save(self) -> None:
        """Save sessions to persistent storage."""
        if not self._dirty:
            return
        await self._store.async_save({"sessions": self._sessions})
        self._dirty = False
        _LOGGER.debug("Saved %d sessions to store", len(self._sessions))

    def add_session(self, session: dict[str, Any]) -> None:
        """Add a completed heating session."""
        self._sessions.append(session)
        self._dirty = True

    def add_sessions(self, sessions: list[dict[str, Any]]) -> None:
        """Add multiple sessions (e.g., from backfill)."""
        self._sessions.extend(sessions)
        self._dirty = True
        _LOGGER.debug("Added %d sessions to store", len(sessions))

    def prune(self, max_age_days: int) -> None:
        """Remove sessions older than max_age_days."""
        cutoff = dt_util.utcnow() - timedelta(days=max_age_days)
        cutoff_str = cutoff.isoformat()
        before = len(self._sessions)
        self._sessions = [
            s for s in self._sessions
            if s.get("start_time", "") > cutoff_str
        ]
        removed = before - len(self._sessions)
        if removed > 0:
            self._dirty = True
            _LOGGER.debug("Pruned %d old sessions", removed)

    def get_sessions_for_zone(self, zone_id: str) -> list[dict[str, Any]]:
        """Return sessions for a specific zone."""
        return [s for s in self._sessions if s.get("zone_id") == zone_id]

    def get_sessions_in_window(self, days: int) -> list[dict[str, Any]]:
        """Return sessions within the analysis window."""
        cutoff = dt_util.utcnow() - timedelta(days=days)
        cutoff_str = cutoff.isoformat()
        return [s for s in self._sessions if s.get("start_time", "") > cutoff_str]

    def get_sessions_in_range(
        self, start_days_ago: int, end_days_ago: int
    ) -> list[dict[str, Any]]:
        """Return sessions between start_days_ago and end_days_ago.

        E.g. get_sessions_in_range(2, 1) returns sessions from 48h-24h ago.
        """
        now = dt_util.utcnow()
        range_start = (now - timedelta(days=start_days_ago)).isoformat()
        range_end = (now - timedelta(days=end_days_ago)).isoformat()
        return [
            s for s in self._sessions
            if range_start < s.get("start_time", "") <= range_end
        ]
