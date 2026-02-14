"""Historical backfill from HA recorder."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import MAX_SESSION_DURATION_HOURS, MIN_SESSION_DURATION_MINUTES
from .store import RadiatorAnalyticsStore

_LOGGER = logging.getLogger(__name__)


async def async_backfill_from_recorder(
    hass: HomeAssistant,
    store: RadiatorAnalyticsStore,
    monitored_zones: list[str],
    days: int,
) -> int:
    """Pull historical data from the recorder and reconstruct heating sessions.

    Returns the number of sessions reconstructed.
    """
    # Import recorder history at runtime to avoid hard dependency issues
    try:
        from homeassistant.components.recorder.history import get_significant_states
    except ImportError:
        _LOGGER.warning("Recorder not available, skipping backfill")
        return 0

    start_time = dt_util.utcnow() - timedelta(days=days)
    end_time = dt_util.utcnow()

    _LOGGER.info(
        "Backfilling %d days of history for %d zones",
        days,
        len(monitored_zones),
    )

    # Fetch historical states from recorder
    history = await hass.async_add_executor_job(
        get_significant_states,
        hass,
        start_time,
        end_time,
        monitored_zones,
        None,  # filters
        True,  # include_start_time_state
        True,  # significant_changes_only
        True,  # minimal_response
    )

    total_sessions = 0

    for entity_id in monitored_zones:
        states = history.get(entity_id, [])
        if not states:
            continue

        sessions = _reconstruct_sessions(entity_id, states, monitored_zones, history)
        if sessions:
            store.add_sessions(sessions)
            total_sessions += len(sessions)
            _LOGGER.debug(
                "Backfilled %d sessions for %s",
                len(sessions),
                entity_id,
            )

    _LOGGER.info("Backfill complete: %d sessions reconstructed", total_sessions)
    return total_sessions


def _reconstruct_sessions(
    entity_id: str,
    states: list,
    all_zones: list[str],
    all_history: dict,
) -> list[dict[str, Any]]:
    """Reconstruct heating sessions from historical state data."""
    sessions = []
    session_start = None
    session_start_temp = None
    session_setpoint = None

    for state in states:
        attrs = state.attributes if hasattr(state, "attributes") else {}
        action = attrs.get("hvac_action", "")
        temp = attrs.get("current_temperature")
        setpoint = attrs.get("temperature")
        ts = state.last_changed if hasattr(state, "last_changed") else None

        if ts is None:
            continue

        if action == "heating" and session_start is None:
            session_start = ts
            session_start_temp = temp
            session_setpoint = setpoint

        elif action != "heating" and session_start is not None:
            # Session ended
            if temp is not None and session_start_temp is not None:
                duration_seconds = (ts - session_start).total_seconds()
                duration_minutes = duration_seconds / 60
                duration_hours = duration_seconds / 3600

                if (
                    duration_minutes >= MIN_SESSION_DURATION_MINUTES
                    and duration_hours <= MAX_SESSION_DURATION_HOURS
                ):
                    temp_rise = float(temp) - float(session_start_temp)
                    rate = temp_rise / duration_hours if duration_hours > 0 else 0

                    # Estimate concurrent zones from historical data
                    mid_time = session_start + (ts - session_start) / 2
                    concurrent = _count_concurrent_zones(
                        entity_id, mid_time, all_zones, all_history
                    )

                    sessions.append(
                        {
                            "zone_id": entity_id,
                            "start_time": session_start.isoformat(),
                            "end_time": ts.isoformat(),
                            "start_temp": float(session_start_temp),
                            "end_temp": float(temp),
                            "setpoint": float(session_setpoint) if session_setpoint else None,
                            "temp_rise": round(temp_rise, 2),
                            "duration_minutes": round(duration_minutes, 1),
                            "rate_per_hour": round(rate, 2),
                            "concurrent_zones": [],
                            "concurrent_count": concurrent,
                            "reached_setpoint": (
                                float(temp) >= float(session_setpoint)
                                if session_setpoint
                                else None
                            ),
                            "backfilled": True,
                        }
                    )

            session_start = None
            session_start_temp = None
            session_setpoint = None

    return sessions


def _count_concurrent_zones(
    entity_id: str,
    at_time: datetime,
    all_zones: list[str],
    all_history: dict,
) -> int:
    """Estimate how many other zones were heating at a given time."""
    count = 0
    for zone_id in all_zones:
        if zone_id == entity_id:
            continue
        states = all_history.get(zone_id, [])
        # Find the state at the given time (last state before at_time)
        last_action = ""
        for state in states:
            ts = state.last_changed if hasattr(state, "last_changed") else None
            if ts is None:
                continue
            if ts > at_time:
                break
            attrs = state.attributes if hasattr(state, "attributes") else {}
            last_action = attrs.get("hvac_action", "")
        if last_action == "heating":
            count += 1
    return count
