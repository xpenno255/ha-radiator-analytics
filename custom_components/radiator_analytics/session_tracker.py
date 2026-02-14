"""Track heating sessions from climate entity state changes."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import MAX_SESSION_DURATION_HOURS, MIN_SESSION_DURATION_MINUTES
from .store import RadiatorAnalyticsStore

_LOGGER = logging.getLogger(__name__)


class HeatingSessionTracker:
    """Tracks heating sessions by listening to climate entity state changes."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: RadiatorAnalyticsStore,
        monitored_zones: list[str],
    ) -> None:
        """Initialize the session tracker."""
        self._hass = hass
        self._store = store
        self._monitored_zones = monitored_zones
        self._active_sessions: dict[str, dict[str, Any]] = {}
        self._unsub: list = []

    @property
    def active_zones(self) -> set[str]:
        """Return the set of zones currently in a heating session."""
        return set(self._active_sessions.keys())

    def start(self) -> None:
        """Start tracking state changes on monitored zones."""
        self._unsub.append(
            async_track_state_change_event(
                self._hass,
                self._monitored_zones,
                self._handle_state_change,
            )
        )
        # Initialize active sessions from current state
        for entity_id in self._monitored_zones:
            state = self._hass.states.get(entity_id)
            if state and state.attributes.get("hvac_action") == "heating":
                self._start_session(entity_id, state.attributes)

        _LOGGER.info(
            "Session tracker started for %d zones (%d already heating)",
            len(self._monitored_zones),
            len(self._active_sessions),
        )

    def stop(self) -> None:
        """Stop tracking."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        # Close any active sessions
        for entity_id in list(self._active_sessions.keys()):
            state = self._hass.states.get(entity_id)
            attrs = state.attributes if state else {}
            self._end_session(entity_id, attrs)

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle a state change event on a monitored climate entity."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if not new_state or not entity_id:
            return

        new_action = new_state.attributes.get("hvac_action", "")
        old_action = (
            old_state.attributes.get("hvac_action", "") if old_state else ""
        )

        if old_action != "heating" and new_action == "heating":
            self._start_session(entity_id, new_state.attributes)
        elif old_action == "heating" and new_action != "heating":
            self._end_session(entity_id, new_state.attributes)

    def _start_session(self, entity_id: str, attrs: dict) -> None:
        """Start a new heating session."""
        self._active_sessions[entity_id] = {
            "zone_id": entity_id,
            "start_time": dt_util.utcnow().isoformat(),
            "start_temp": attrs.get("current_temperature"),
            "setpoint": attrs.get("temperature"),
        }

    def _end_session(self, entity_id: str, attrs: dict) -> None:
        """End a heating session and store it."""
        session_data = self._active_sessions.pop(entity_id, None)
        if not session_data:
            return

        now = dt_util.utcnow()
        start_time = datetime.fromisoformat(session_data["start_time"])
        duration_seconds = (now - start_time).total_seconds()
        duration_minutes = duration_seconds / 60
        duration_hours = duration_seconds / 3600

        # Filter out very short or very long sessions
        if duration_minutes < MIN_SESSION_DURATION_MINUTES:
            return
        if duration_hours > MAX_SESSION_DURATION_HOURS:
            return

        start_temp = session_data.get("start_temp")
        end_temp = attrs.get("current_temperature")

        if start_temp is None or end_temp is None:
            return

        temp_rise = float(end_temp) - float(start_temp)
        rate_per_hour = temp_rise / duration_hours if duration_hours > 0 else 0

        # Determine which other zones were also heating during this session
        concurrent_zones = [
            z for z in self._active_sessions.keys() if z != entity_id
        ]

        completed_session = {
            "zone_id": entity_id,
            "start_time": session_data["start_time"],
            "end_time": now.isoformat(),
            "start_temp": float(start_temp),
            "end_temp": float(end_temp),
            "setpoint": float(session_data["setpoint"]) if session_data.get("setpoint") else None,
            "temp_rise": round(temp_rise, 2),
            "duration_minutes": round(duration_minutes, 1),
            "rate_per_hour": round(rate_per_hour, 2),
            "concurrent_zones": concurrent_zones,
            "concurrent_count": len(concurrent_zones),
            "reached_setpoint": (
                float(end_temp) >= float(session_data["setpoint"])
                if session_data.get("setpoint")
                else None
            ),
        }

        self._store.add_session(completed_session)
        _LOGGER.debug(
            "Session completed: %s, %.1f min, %+.2f C/hr, %d concurrent",
            entity_id,
            duration_minutes,
            rate_per_hour,
            len(concurrent_zones),
        )
