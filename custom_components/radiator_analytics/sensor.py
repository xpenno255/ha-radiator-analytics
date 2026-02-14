"""Sensor entities for Radiator Analytics."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .analyzer import AnalyticsResult, ZoneStats
from .const import DOMAIN
from .coordinator import CoordinatorData, RadiatorAnalyticsCoordinator

_LOGGER = logging.getLogger(__name__)

DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, DOMAIN)},
    name="Radiator Analytics",
    manufacturer="Custom",
    model="Radiator Analytics",
    entry_type=DeviceEntryType.SERVICE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: RadiatorAnalyticsCoordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    # Per-zone sensors
    for zone_id in coordinator.monitored_zones:
        entities.extend([
            ZoneHeatingRateSensor(coordinator, zone_id),
            ZoneMorningRateSensor(coordinator, zone_id),
            ZoneDutyCycleSensor(coordinator, zone_id),
            ZoneTimeToSetpointSensor(coordinator, zone_id),
            ZoneSetpointAchievementSensor(coordinator, zone_id),
            ZoneCircuitPositionSensor(coordinator, zone_id),
            ZoneFlowImpactSensor(coordinator, zone_id),
        ])

    # System sensors
    entities.extend([
        SystemBalanceScoreSensor(coordinator),
        SystemCircuitOrderSensor(coordinator),
        SystemRecommendationsSensor(coordinator),
        SystemRecommendations24hSensor(coordinator),
        SystemBalanceScore24hSensor(coordinator),
    ])

    async_add_entities(entities)


def _zone_name(zone_id: str, zone_names: dict[str, str] | None = None) -> str:
    """Get the friendly zone name, falling back to entity_id slug."""
    if zone_names and zone_id in zone_names:
        return zone_names[zone_id]
    parts = zone_id.split(".")[-1]
    return parts.replace("_", " ").title()


def _zone_slug(zone_id: str) -> str:
    """Extract a slug for use in entity IDs."""
    return zone_id.split(".")[-1]


class _ZoneBaseSensor(CoordinatorEntity[RadiatorAnalyticsCoordinator], SensorEntity):
    """Base class for per-zone analytics sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiatorAnalyticsCoordinator,
        zone_id: str,
        key: str,
        name_suffix: str,
    ) -> None:
        """Initialize the zone sensor."""
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._name_suffix = name_suffix
        self._attr_unique_id = f"{DOMAIN}_{_zone_slug(zone_id)}_{key}"
        self._attr_device_info = DEVICE_INFO

    @property
    def name(self) -> str:
        """Return the sensor name using the friendly zone name."""
        return f"{_zone_name(self._zone_id, self.coordinator.zone_names)} {self._name_suffix}"

    def _get_zone_stats(self) -> ZoneStats | None:
        """Get the zone stats from coordinator data."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.primary.zone_stats.get(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return common attributes."""
        attrs: dict[str, Any] = {"source_entity": self._zone_id}
        if self.coordinator.data:
            attrs["analysis_window_days"] = self.coordinator.data.primary.analysis_window_days
        zs = self._get_zone_stats()
        if zs:
            attrs["total_sessions"] = zs.total_sessions
        return attrs


class ZoneHeatingRateSensor(_ZoneBaseSensor):
    """Average heating rate for a zone."""

    _attr_native_unit_of_measurement = "°C/hr"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer-chevron-up"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "heating_rate", "Heating Rate")

    @property
    def native_value(self) -> float | None:
        zs = self._get_zone_stats()
        return zs.heating_rate_avg if zs else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        zs = self._get_zone_stats()
        if zs:
            attrs["rate_alone"] = zs.rate_alone
            attrs["rate_concurrent"] = zs.rate_concurrent
        return attrs


class ZoneMorningRateSensor(_ZoneBaseSensor):
    """Morning ramp-up rate for a zone."""

    _attr_native_unit_of_measurement = "°C/hr"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "morning_rate", "Morning Rate")

    @property
    def native_value(self) -> float | None:
        zs = self._get_zone_stats()
        return zs.heating_rate_morning if zs else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        zs = self._get_zone_stats()
        if zs:
            attrs["morning_sessions"] = zs.total_morning_sessions
        return attrs


class ZoneDutyCycleSensor(_ZoneBaseSensor):
    """Duty cycle percentage for a zone."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:percent-circle"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "duty_cycle", "Duty Cycle")

    @property
    def native_value(self) -> float | None:
        zs = self._get_zone_stats()
        return zs.duty_cycle if zs else None


class ZoneTimeToSetpointSensor(_ZoneBaseSensor):
    """Average time to reach setpoint for a zone."""

    _attr_native_unit_of_measurement = "min"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "time_to_setpoint", "Time to Setpoint")

    @property
    def native_value(self) -> float | None:
        zs = self._get_zone_stats()
        return zs.time_to_setpoint_avg if zs else None


class ZoneSetpointAchievementSensor(_ZoneBaseSensor):
    """Percentage of sessions reaching setpoint."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:target"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "setpoint_achievement", "Setpoint Achievement")

    @property
    def native_value(self) -> float | None:
        zs = self._get_zone_stats()
        return zs.setpoint_achievement if zs else None


class ZoneCircuitPositionSensor(_ZoneBaseSensor):
    """Estimated circuit position (1 = nearest boiler)."""

    _attr_icon = "mdi:order-numeric-ascending"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "circuit_position", "Circuit Position")

    @property
    def native_value(self) -> int | None:
        zs = self._get_zone_stats()
        return zs.circuit_position if zs else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        if self.coordinator.data:
            attrs["circuit_order"] = self.coordinator.data.primary.system.circuit_order
        return attrs


class ZoneFlowImpactSensor(_ZoneBaseSensor):
    """Flow impact ratio (rate under load / rate alone)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "flow_impact", "Flow Impact")

    @property
    def native_value(self) -> float | None:
        zs = self._get_zone_stats()
        return zs.flow_impact if zs else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        zs = self._get_zone_stats()
        if zs:
            attrs["rate_alone"] = zs.rate_alone
            attrs["rate_concurrent"] = zs.rate_concurrent
        return attrs


# --- System-level sensors ---


class _SystemBaseSensor(CoordinatorEntity[RadiatorAnalyticsCoordinator], SensorEntity):
    """Base class for system-level analytics sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RadiatorAnalyticsCoordinator,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_system_{key}"
        self._attr_name = name
        self._attr_device_info = DEVICE_INFO


class SystemBalanceScoreSensor(_SystemBaseSensor):
    """Overall system balance score (0-100)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator) -> None:
        super().__init__(coordinator, "balance_score", "System Balance Score")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.primary.system.balance_score


class SystemCircuitOrderSensor(_SystemBaseSensor):
    """Estimated circuit order of all zones."""

    _attr_icon = "mdi:order-numeric-ascending"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator) -> None:
        super().__init__(coordinator, "circuit_order", "Circuit Order")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        order = self.coordinator.data.primary.system.circuit_order
        if not order:
            return None
        names = self.coordinator.zone_names
        return " -> ".join(_zone_name(z, names) for z in order)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self.coordinator.data:
            names = self.coordinator.zone_names
            order = self.coordinator.data.primary.system.circuit_order
            for i, zone_id in enumerate(order, start=1):
                attrs[f"position_{i}"] = _zone_name(zone_id, names)
                attrs[f"position_{i}_entity"] = zone_id
                zs = self.coordinator.data.primary.zone_stats.get(zone_id)
                if zs:
                    attrs[f"position_{i}_rate"] = zs.heating_rate_avg
            attrs["total_zones"] = len(order)
        return attrs


class SystemRecommendationsSensor(_SystemBaseSensor):
    """Recommendations based on heating performance analysis."""

    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator) -> None:
        super().__init__(coordinator, "recommendations", "Recommendations")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.primary.system.recommendations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self.coordinator.data:
            recs = self.coordinator.data.primary.system.recommendations
            for i, rec in enumerate(recs, start=1):
                attrs[f"recommendation_{i}"] = rec
            attrs["total_recommendations"] = len(recs)
        return attrs


# --- 24-hour sensors ---


class SystemRecommendations24hSensor(_SystemBaseSensor):
    """Recommendations based on last 24 hours of data."""

    _attr_icon = "mdi:lightbulb-alert-outline"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator) -> None:
        super().__init__(coordinator, "recommendations_24h", "Recommendations (24h)")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.short.system.recommendations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"analysis_window": "24h"}
        if self.coordinator.data:
            recs = self.coordinator.data.short.system.recommendations
            for i, rec in enumerate(recs, start=1):
                attrs[f"recommendation_{i}"] = rec
            attrs["total_recommendations"] = len(recs)
            # Include 24h zone stats summary for quick reference
            names = self.coordinator.zone_names
            for zone_id, zs in self.coordinator.data.short.zone_stats.items():
                name = _zone_name(zone_id, names).lower().replace(" ", "_")
                if zs.total_sessions > 0:
                    attrs[f"{name}_sessions"] = zs.total_sessions
                    attrs[f"{name}_heating_rate"] = zs.heating_rate_avg
                    attrs[f"{name}_duty_cycle"] = zs.duty_cycle
        return attrs


class SystemBalanceScore24hSensor(_SystemBaseSensor):
    """System balance score based on last 24 hours."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

    def __init__(self, coordinator: RadiatorAnalyticsCoordinator) -> None:
        super().__init__(coordinator, "balance_score_24h", "System Balance Score (24h)")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.short.system.balance_score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"analysis_window": "24h"}
        if self.coordinator.data:
            names = self.coordinator.zone_names
            for zone_id, zs in self.coordinator.data.short.zone_stats.items():
                if zs.heating_rate_avg is not None:
                    name = _zone_name(zone_id, names).lower().replace(" ", "_")
                    attrs[f"{name}_rate"] = zs.heating_rate_avg
        return attrs
