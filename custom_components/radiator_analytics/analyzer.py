"""Statistics computation engine for radiator analytics."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import MORNING_END_HOUR, MORNING_START_HOUR

_LOGGER = logging.getLogger(__name__)


@dataclass
class ZoneStats:
    """Computed statistics for a single zone."""

    zone_id: str
    heating_rate_avg: float | None = None
    heating_rate_morning: float | None = None
    duty_cycle: float | None = None
    time_to_setpoint_avg: float | None = None
    setpoint_achievement: float | None = None
    rate_alone: float | None = None
    rate_concurrent: float | None = None
    flow_impact: float | None = None
    circuit_position: int | None = None
    total_sessions: int = 0
    total_morning_sessions: int = 0


@dataclass
class SystemStats:
    """System-level analytics results."""

    circuit_order: list[str] = field(default_factory=list)
    balance_score: float | None = None
    recommendations: list[str] = field(default_factory=list)


@dataclass
class AnalyticsResult:
    """Complete analytics output consumed by sensor entities."""

    zone_stats: dict[str, ZoneStats] = field(default_factory=dict)
    system: SystemStats = field(default_factory=SystemStats)
    analysis_window_days: int = 7
    last_updated: str = ""


def compute_analytics(
    sessions: list[dict[str, Any]],
    monitored_zones: list[str],
    analysis_window_days: int,
) -> AnalyticsResult:
    """Run the full analytics computation.

    This is the main entry point called by the coordinator.
    """
    result = AnalyticsResult(
        analysis_window_days=analysis_window_days,
        last_updated=dt_util.utcnow().isoformat(),
    )

    if not sessions:
        for zone_id in monitored_zones:
            result.zone_stats[zone_id] = ZoneStats(zone_id=zone_id)
        return result

    # Group sessions by zone
    sessions_by_zone: dict[str, list[dict]] = {}
    for session in sessions:
        zone_id = session.get("zone_id", "")
        if zone_id in monitored_zones:
            sessions_by_zone.setdefault(zone_id, []).append(session)

    # Compute per-zone statistics
    for zone_id in monitored_zones:
        zone_sessions = sessions_by_zone.get(zone_id, [])
        result.zone_stats[zone_id] = _compute_zone_stats(zone_id, zone_sessions)

    # Compute system-level statistics
    result.system = _compute_system_stats(result.zone_stats, monitored_zones)

    return result


def _compute_zone_stats(
    zone_id: str,
    sessions: list[dict[str, Any]],
) -> ZoneStats:
    """Compute statistics for a single zone."""
    stats = ZoneStats(zone_id=zone_id, total_sessions=len(sessions))

    if not sessions:
        return stats

    # Heating rates
    rates = [s["rate_per_hour"] for s in sessions if s.get("rate_per_hour") is not None]
    if rates:
        stats.heating_rate_avg = round(statistics.mean(rates), 2)

    # Morning heating rates (05:00-09:00)
    morning_rates = []
    for s in sessions:
        start = s.get("start_time", "")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
            if MORNING_START_HOUR <= dt.hour < MORNING_END_HOUR:
                rate = s.get("rate_per_hour")
                if rate is not None:
                    morning_rates.append(rate)
        except (ValueError, TypeError):
            continue

    stats.total_morning_sessions = len(morning_rates)
    if morning_rates:
        stats.heating_rate_morning = round(statistics.mean(morning_rates), 2)

    # Duty cycle — total heating time / total window time
    total_heating_minutes = sum(
        s.get("duration_minutes", 0) for s in sessions
    )
    if sessions:
        try:
            earliest = min(
                datetime.fromisoformat(s["start_time"])
                for s in sessions
                if s.get("start_time")
            )
            latest_end = max(
                datetime.fromisoformat(s["end_time"])
                for s in sessions
                if s.get("end_time")
            )
            window_minutes = (latest_end - earliest).total_seconds() / 60
            if window_minutes > 0:
                stats.duty_cycle = round(
                    (total_heating_minutes / window_minutes) * 100, 1
                )
        except (ValueError, TypeError):
            pass

    # Time to setpoint — only for sessions that reached setpoint
    times_to_setpoint = [
        s["duration_minutes"]
        for s in sessions
        if s.get("reached_setpoint") is True
    ]
    if times_to_setpoint:
        stats.time_to_setpoint_avg = round(statistics.mean(times_to_setpoint), 1)

    # Setpoint achievement rate
    sessions_with_setpoint = [s for s in sessions if s.get("reached_setpoint") is not None]
    if sessions_with_setpoint:
        reached = sum(1 for s in sessions_with_setpoint if s["reached_setpoint"])
        stats.setpoint_achievement = round(
            (reached / len(sessions_with_setpoint)) * 100, 1
        )

    # Rate alone (0 concurrent zones) vs rate under concurrent demand (2+)
    rates_alone = [
        s["rate_per_hour"]
        for s in sessions
        if s.get("concurrent_count", 0) == 0 and s.get("rate_per_hour") is not None
    ]
    rates_concurrent = [
        s["rate_per_hour"]
        for s in sessions
        if s.get("concurrent_count", 0) >= 2 and s.get("rate_per_hour") is not None
    ]

    if rates_alone:
        stats.rate_alone = round(statistics.mean(rates_alone), 2)
    if rates_concurrent:
        stats.rate_concurrent = round(statistics.mean(rates_concurrent), 2)

    # Flow impact ratio
    if stats.rate_alone and stats.rate_concurrent and stats.rate_alone != 0:
        stats.flow_impact = round(stats.rate_concurrent / stats.rate_alone, 2)

    return stats


def _compute_system_stats(
    zone_stats: dict[str, ZoneStats],
    monitored_zones: list[str],
) -> SystemStats:
    """Compute system-level statistics from zone stats."""
    system = SystemStats()

    # Circuit order estimation
    # Primary signal: morning ramp rate (fastest = nearest boiler)
    # Secondary signal: flow impact (higher = better position)
    scored_zones: list[tuple[str, float]] = []
    for zone_id in monitored_zones:
        zs = zone_stats.get(zone_id)
        if not zs:
            continue

        # Use morning rate if available, fall back to overall rate
        rate = zs.heating_rate_morning or zs.heating_rate_avg
        if rate is None:
            continue

        # Flow impact bonus: zones that maintain performance under load
        # are likely in better circuit positions
        fi_bonus = 0.0
        if zs.flow_impact is not None:
            fi_bonus = (zs.flow_impact - 1.0) * 0.5  # positive if gains under load

        score = rate + fi_bonus
        scored_zones.append((zone_id, score))

    # Sort by score descending — highest score = nearest boiler = position 1
    scored_zones.sort(key=lambda x: x[1], reverse=True)
    system.circuit_order = [z[0] for z in scored_zones]

    # Assign circuit positions back to zone stats
    for position, (zone_id, _) in enumerate(scored_zones, start=1):
        zone_stats[zone_id].circuit_position = position

    # Balance score (0-100)
    # Based on coefficient of variation of heating rates
    rates = [
        zs.heating_rate_avg
        for zs in zone_stats.values()
        if zs.heating_rate_avg is not None and zs.heating_rate_avg > 0
    ]
    if len(rates) >= 2:
        mean_rate = statistics.mean(rates)
        stdev_rate = statistics.stdev(rates)
        if mean_rate > 0:
            cv = stdev_rate / mean_rate
            # CV of 0 = perfectly balanced = 100, CV of 2+ = very imbalanced = 0
            system.balance_score = round(max(0, min(100, (1 - cv / 2) * 100)), 0)

    # Recommendations
    system.recommendations = _generate_recommendations(zone_stats)

    return system


def _generate_recommendations(
    zone_stats: dict[str, ZoneStats],
) -> list[str]:
    """Generate actionable recommendations based on zone performance."""
    recs: list[str] = []

    for zone_id, zs in zone_stats.items():
        # Extract friendly zone name from entity_id
        name = zone_id.split(".")[-1].replace("_", " ").title()

        # Flow-starved zone: high duty cycle + low heating rate
        if (
            zs.duty_cycle is not None
            and zs.duty_cycle > 50
            and zs.heating_rate_avg is not None
            and zs.heating_rate_avg < 1.0
        ):
            recs.append(
                f"{name}: High duty cycle ({zs.duty_cycle}%) with low heating rate "
                f"({zs.heating_rate_avg} C/hr). Consider opening lockshield valve."
            )

        # Excess flow: heats too fast
        if (
            zs.time_to_setpoint_avg is not None
            and zs.time_to_setpoint_avg < 15
            and zs.total_sessions >= 3
        ):
            recs.append(
                f"{name}: Reaches setpoint very quickly ({zs.time_to_setpoint_avg} min avg). "
                f"Consider restricting lockshield valve to improve system balance."
            )

        # Severe flow loss under concurrent demand
        if zs.flow_impact is not None and zs.flow_impact < 0.5:
            recs.append(
                f"{name}: Loses significant flow under concurrent demand "
                f"(flow impact {zs.flow_impact}). Likely at end of circuit."
            )

        # Frequently fails to reach setpoint
        if (
            zs.setpoint_achievement is not None
            and zs.setpoint_achievement < 70
            and zs.total_sessions >= 3
        ):
            recs.append(
                f"{name}: Only reaches target temperature {zs.setpoint_achievement}% of the time. "
                f"May need lockshield adjustment or radiator sizing review."
            )

    return recs
