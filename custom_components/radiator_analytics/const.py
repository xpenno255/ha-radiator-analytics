"""Constants for the Radiator Analytics integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "radiator_analytics"

CONF_MONITORED_ZONES: Final = "monitored_zones"
CONF_ANALYSIS_WINDOW: Final = "analysis_window_days"
CONF_UPDATE_INTERVAL: Final = "update_interval_minutes"

DEFAULT_ANALYSIS_WINDOW: Final = 7
DEFAULT_UPDATE_INTERVAL: Final = 15

MIN_ANALYSIS_WINDOW: Final = 3
MAX_ANALYSIS_WINDOW: Final = 14
MIN_UPDATE_INTERVAL: Final = 5
MAX_UPDATE_INTERVAL: Final = 60

MORNING_START_HOUR: Final = 5
MORNING_END_HOUR: Final = 9

MIN_SESSION_DURATION_MINUTES: Final = 3
MAX_SESSION_DURATION_HOURS: Final = 6

PLATFORMS: Final = ["sensor"]
