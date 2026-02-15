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
    zone_names: dict[str, str] | None = None,
) -> AnalyticsResult:
    """Run the full analytics computation.

    This is the main entry point called by the coordinator.
    """
    if zone_names is None:
        zone_names = {}
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
    result.system = _compute_system_stats(result.zone_stats, monitored_zones, zone_names)

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
    # Exclude backfilled sessions — they always have concurrent_count=0
    # which would incorrectly inflate rates_alone
    live_sessions = [s for s in sessions if not s.get("backfilled")]
    rates_alone = [
        s["rate_per_hour"]
        for s in live_sessions
        if s.get("concurrent_count", 0) == 0 and s.get("rate_per_hour") is not None
    ]
    rates_concurrent = [
        s["rate_per_hour"]
        for s in live_sessions
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
    zone_names: dict[str, str] | None = None,
) -> SystemStats:
    """Compute system-level statistics from zone stats."""
    system = SystemStats()

    # Circuit order estimation
    # Primary signal: morning ramp rate (fastest = nearest boiler)
    # Secondary signal: flow impact penalty only — zones that lose performance
    # under concurrent demand are penalised (likely further from boiler)
    scored_zones: list[tuple[str, float]] = []
    for zone_id in monitored_zones:
        zs = zone_stats.get(zone_id)
        if not zs:
            continue

        # Use morning rate if available, fall back to overall rate
        rate = zs.heating_rate_morning or zs.heating_rate_avg
        if rate is None:
            continue

        # Only penalise zones that lose flow under concurrent demand
        # (flow_impact < 1.0 means zone heats slower when others are active)
        # Never apply a bonus — high flow_impact can be misleading when
        # the alone rate is very low (small denominator inflates the ratio)
        fi_penalty = 0.0
        if zs.flow_impact is not None and zs.flow_impact < 1.0:
            fi_penalty = (1.0 - zs.flow_impact) * rate * 0.3

        score = rate - fi_penalty
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
    system.recommendations = _generate_recommendations(zone_stats, zone_names)

    return system


def _generate_recommendations(
    zone_stats: dict[str, ZoneStats],
    zone_names: dict[str, str] | None = None,
) -> list[str]:
    """Generate actionable recommendations based on zone performance.

    Recommendations are prioritised by severity (highest first):
    1 = critical, 2 = warning, 3 = suggestion
    """
    if zone_names is None:
        zone_names = {}
    # Collect (priority, recommendation_text) tuples
    scored: list[tuple[int, str]] = []

    for zone_id, zs in zone_stats.items():
        name = zone_names.get(zone_id, zone_id.split(".")[-1].replace("_", " ").title())

        # Flow-starved zone: high duty cycle + low heating rate
        if (
            zs.duty_cycle is not None
            and zs.duty_cycle > 50
            and zs.heating_rate_avg is not None
            and zs.heating_rate_avg < 1.0
        ):
            scored.append((
                1,
                f"{name}: High duty cycle ({zs.duty_cycle}%) with low heating rate "
                f"({zs.heating_rate_avg} C/hr). Consider opening lockshield valve.",
            ))

        # Excess flow: heats too fast — only flag when the zone genuinely
        # reaches setpoint reliably (>70%), otherwise the fast time is
        # misleading (based on a small subset of lucky sessions)
        if (
            zs.time_to_setpoint_avg is not None
            and zs.time_to_setpoint_avg < 15
            and zs.total_sessions >= 3
            and zs.setpoint_achievement is not None
            and zs.setpoint_achievement > 70
        ):
            scored.append((
                3,
                f"{name}: Reaches setpoint very quickly ({zs.time_to_setpoint_avg} min avg). "
                f"Consider restricting lockshield valve to improve system balance.",
            ))

        # Severe flow loss under concurrent demand
        if zs.flow_impact is not None and zs.flow_impact < 0.5:
            scored.append((
                2,
                f"{name}: Loses significant flow under concurrent demand "
                f"(flow impact {zs.flow_impact}). Likely at end of circuit.",
            ))

        # Frequently fails to reach setpoint — differentiate severity and
        # require more sessions to avoid noisy early-data warnings
        if (
            zs.setpoint_achievement is not None
            and zs.total_sessions >= 5
        ):
            if zs.setpoint_achievement < 30:
                # Severely underperforming
                detail = "Rarely reaches target"
                if zs.duty_cycle and zs.duty_cycle > 60:
                    advice = "Heating constantly but not reaching target — likely undersized radiator or insufficient flow."
                else:
                    advice = "May need lockshield opened further or radiator sizing review."
                scored.append((
                    1,
                    f"{name}: {detail} ({zs.setpoint_achievement}% of sessions). {advice}",
                ))
            elif zs.setpoint_achievement < 50:
                # Moderately underperforming — only flag if heating hard
                if zs.duty_cycle and zs.duty_cycle > 40:
                    scored.append((
                        2,
                        f"{name}: Reaches target only {zs.setpoint_achievement}% of the time "
                        f"despite {zs.duty_cycle}% duty cycle. Consider opening lockshield valve.",
                    ))

    # Sort by priority (1=critical first) then alphabetically by text
    scored.sort(key=lambda x: (x[0], x[1]))
    return [text for _, text in scored]


# --- Daily comparison (24h vs previous 24h) ---


@dataclass
class ZoneComparison:
    """Change metrics for a single zone between two 24h windows."""

    zone_id: str
    heating_rate_delta: float | None = None
    duty_cycle_delta: float | None = None
    setpoint_achievement_delta: float | None = None
    time_to_setpoint_delta: float | None = None
    sessions_current: int = 0
    sessions_previous: int = 0
    improved: bool | None = None


@dataclass
class ComparisonResult:
    """Result of comparing two 24h analysis windows."""

    zone_comparisons: dict[str, ZoneComparison] = field(default_factory=dict)
    balance_score_delta: float | None = None
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    last_updated: str = ""


def _delta(current: float | None, previous: float | None) -> float | None:
    """Compute rounded delta between two values."""
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


def _fmt_delta(val: float | None) -> str:
    """Format a delta value with +/- prefix."""
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val}"


def compare_windows(
    current: AnalyticsResult,
    previous: AnalyticsResult,
    zone_names: dict[str, str] | None = None,
) -> ComparisonResult:
    """Compare two analysis windows and produce a change report."""
    if zone_names is None:
        zone_names = {}

    result = ComparisonResult(last_updated=dt_util.utcnow().isoformat())

    improved_count = 0
    regressed_count = 0
    unchanged_count = 0

    all_zones = set(current.zone_stats.keys()) | set(previous.zone_stats.keys())

    for zone_id in all_zones:
        cur = current.zone_stats.get(zone_id)
        prev = previous.zone_stats.get(zone_id)
        if not cur or not prev:
            continue

        comp = ZoneComparison(
            zone_id=zone_id,
            heating_rate_delta=_delta(cur.heating_rate_avg, prev.heating_rate_avg),
            duty_cycle_delta=_delta(cur.duty_cycle, prev.duty_cycle),
            setpoint_achievement_delta=_delta(
                cur.setpoint_achievement, prev.setpoint_achievement
            ),
            time_to_setpoint_delta=_delta(
                cur.time_to_setpoint_avg, prev.time_to_setpoint_avg
            ),
            sessions_current=cur.total_sessions,
            sessions_previous=prev.total_sessions,
        )

        # Determine overall direction — improvement means heating rate up
        # or setpoint achievement up (or both)
        signals: list[bool] = []
        if comp.heating_rate_delta is not None and abs(comp.heating_rate_delta) > 0.05:
            signals.append(comp.heating_rate_delta > 0)
        if (
            comp.setpoint_achievement_delta is not None
            and abs(comp.setpoint_achievement_delta) > 2.0
        ):
            signals.append(comp.setpoint_achievement_delta > 0)

        if signals:
            comp.improved = all(signals)
            if comp.improved:
                improved_count += 1
            else:
                regressed_count += 1
        else:
            unchanged_count += 1

        result.zone_comparisons[zone_id] = comp

    # Balance score delta
    result.balance_score_delta = _delta(
        current.system.balance_score, previous.system.balance_score
    )

    # Summary
    parts = []
    if improved_count:
        parts.append(f"{improved_count} improved")
    if regressed_count:
        parts.append(f"{regressed_count} regressed")
    if unchanged_count:
        parts.append(f"{unchanged_count} unchanged")
    result.summary = ", ".join(parts) if parts else "No data"

    # Comparison-specific recommendations
    result.recommendations = _generate_comparison_recommendations(
        result.zone_comparisons, zone_names
    )

    return result


def _generate_comparison_recommendations(
    comparisons: dict[str, ZoneComparison],
    zone_names: dict[str, str],
) -> list[str]:
    """Generate recommendations based on changes between two 24h windows."""
    scored: list[tuple[int, str]] = []

    for zone_id, comp in comparisons.items():
        name = zone_names.get(
            zone_id, zone_id.split(".")[-1].replace("_", " ").title()
        )

        # Skip zones with very little data in either window
        if comp.sessions_current < 2 and comp.sessions_previous < 2:
            continue

        rate_d = comp.heating_rate_delta
        ach_d = comp.setpoint_achievement_delta
        duty_d = comp.duty_cycle_delta

        # Significant heating rate improvement
        if rate_d is not None and rate_d > 0.1:
            detail = f"Heating rate improved {_fmt_delta(rate_d)} C/hr"
            if ach_d is not None and ach_d > 5:
                detail += f", setpoint achievement up {_fmt_delta(ach_d)}%"
            scored.append((3, f"{name}: {detail}. Adjustment helping."))

        # Significant heating rate regression
        elif rate_d is not None and rate_d < -0.1:
            detail = f"Heating rate dropped {_fmt_delta(rate_d)} C/hr"
            if ach_d is not None and ach_d < -5:
                detail += f", setpoint achievement down {_fmt_delta(ach_d)}%"
                scored.append((
                    1,
                    f"{name}: {detail}. Lockshield may be too restricted "
                    f"— consider opening slightly.",
                ))
            else:
                scored.append((2, f"{name}: {detail}. Monitor over next 24h."))

        # Setpoint achievement changed significantly (even if rate didn't)
        elif ach_d is not None and abs(ach_d) > 10:
            if ach_d > 0:
                scored.append((
                    3,
                    f"{name}: Setpoint achievement improved "
                    f"{_fmt_delta(ach_d)}%. Reaching target more often.",
                ))
            else:
                scored.append((
                    1,
                    f"{name}: Setpoint achievement dropped "
                    f"{_fmt_delta(ach_d)}%. May need lockshield adjustment.",
                ))

        # Duty cycle changed significantly with no rate improvement
        elif duty_d is not None and abs(duty_d) > 10:
            if duty_d < 0 and (rate_d is None or abs(rate_d or 0) < 0.05):
                scored.append((
                    3,
                    f"{name}: Duty cycle reduced {_fmt_delta(duty_d)}% with "
                    f"stable heating rate. More efficient.",
                ))
            elif duty_d > 0 and (rate_d is None or (rate_d or 0) < 0.05):
                scored.append((
                    2,
                    f"{name}: Duty cycle increased {_fmt_delta(duty_d)}% without "
                    f"rate improvement. Working harder for same result.",
                ))

        # Zone with enough data but no significant change
        elif comp.sessions_current >= 3 and comp.sessions_previous >= 3:
            if comp.improved is None:
                scored.append((
                    3,
                    f"{name}: No significant change. "
                    f"May need further adjustment.",
                ))

    scored.sort(key=lambda x: (x[0], x[1]))
    return [text for _, text in scored]
