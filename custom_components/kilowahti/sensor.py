"""Sensor platform for the Kilowahti integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_CONTROL_FACTOR_PRICE,
    SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR,
    SENSOR_CONTROL_FACTOR_TRANSFER,
    SENSOR_EFFECTIVE_PRICE,
    SENSOR_NEXT_HOURS_AVG,
    SENSOR_PRICE_QUARTILE,
    SENSOR_PRICE_RANK,
    SENSOR_SETTING_ACCEPTABLE_RANK,
    SENSOR_SETTING_ACTIVE_FIXED_PERIOD,
    SENSOR_SETTING_ACTIVE_TRANSFER_GROUP,
    SENSOR_SETTING_ACTIVE_TRANSFER_TIER,
    SENSOR_SETTING_CONTROL_FACTOR_FUNCTION,
    SENSOR_SETTING_FORWARD_WINDOW,
    SENSOR_SETTING_MAX_PRICE,
    SENSOR_SETTING_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    SENSOR_SPOT_PRICE,
    SENSOR_TODAY_AVG,
    SENSOR_TODAY_MAX,
    SENSOR_TODAY_MIN,
    SENSOR_TOMORROW_AVG,
    SENSOR_TOMORROW_MAX,
    SENSOR_TOMORROW_MIN,
    SENSOR_TOTAL_PRICE,
    SENSOR_TRANSFER_PRICE,
    UNIT_EUROKWH,
)
from .coordinator import KilowahtiCoordinator
from .models import ScoreProfile

_LOGGER = logging.getLogger(__name__)

_PRICE_SENSOR_KEYS = frozenset(
    {
        SENSOR_SPOT_PRICE,
        SENSOR_EFFECTIVE_PRICE,
        SENSOR_TRANSFER_PRICE,
        SENSOR_TOTAL_PRICE,
        SENSOR_TODAY_AVG,
        SENSOR_TODAY_MIN,
        SENSOR_TODAY_MAX,
        SENSOR_TOMORROW_AVG,
        SENSOR_TOMORROW_MIN,
        SENSOR_TOMORROW_MAX,
        SENSOR_NEXT_HOURS_AVG,
        SENSOR_SETTING_MAX_PRICE,
    }
)
_CONTROL_FACTOR_SENSOR_KEYS = frozenset(
    {SENSOR_CONTROL_FACTOR_PRICE, SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR}
)


# ---------------------------------------------------------------------------
# Sensor descriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class KilowahtiSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[KilowahtiCoordinator], Any] | None = None


def _price_sensor(key: str, value_fn: Callable) -> KilowahtiSensorEntityDescription:
    return KilowahtiSensorEntityDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=value_fn,
        # native_unit_of_measurement is dynamic (set from coordinator in entity)
    )


_TRANSFER_SENSOR_KEYS = frozenset({SENSOR_TRANSFER_PRICE, SENSOR_CONTROL_FACTOR_TRANSFER})

SENSOR_DESCRIPTIONS: tuple[KilowahtiSensorEntityDescription, ...] = (
    _price_sensor(SENSOR_SPOT_PRICE, lambda c: c.format_price(c.spot_price_now())),
    _price_sensor(SENSOR_TRANSFER_PRICE, lambda c: c.format_price(c.transfer_price_now())),
    _price_sensor(SENSOR_TOTAL_PRICE, lambda c: c.format_price(c.total_price_now())),
    _price_sensor(SENSOR_TODAY_AVG, lambda c: c.format_price(c.today_avg())),
    _price_sensor(SENSOR_TODAY_MIN, lambda c: c.format_price(c.today_min())),
    _price_sensor(SENSOR_TODAY_MAX, lambda c: c.format_price(c.today_max())),
    _price_sensor(SENSOR_TOMORROW_AVG, lambda c: c.format_price(c.tomorrow_avg())),
    _price_sensor(SENSOR_TOMORROW_MIN, lambda c: c.format_price(c.tomorrow_min())),
    _price_sensor(SENSOR_TOMORROW_MAX, lambda c: c.format_price(c.tomorrow_max())),
    _price_sensor(SENSOR_NEXT_HOURS_AVG, lambda c: c.format_price(c.next_hours_avg())),
    KilowahtiSensorEntityDescription(
        key=SENSOR_PRICE_RANK,
        translation_key=SENSOR_PRICE_RANK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.current_rank(),
        native_unit_of_measurement=None,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_CONTROL_FACTOR_PRICE,
        translation_key=SENSOR_CONTROL_FACTOR_PRICE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: round(c.control_factor() or 0.0, 3),
        native_unit_of_measurement=None,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR,
        translation_key=SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: round(c.control_factor_bipolar() or 0.0, 3),
        native_unit_of_measurement=None,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_CONTROL_FACTOR_TRANSFER,
        translation_key=SENSOR_CONTROL_FACTOR_TRANSFER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=None,  # handled by KilowahtiTransferRankSensor
        native_unit_of_measurement=None,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_PRICE_QUARTILE,
        translation_key=SENSOR_PRICE_QUARTILE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.current_quartile(),
        native_unit_of_measurement=None,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_MAX_PRICE,
        translation_key=SENSOR_SETTING_MAX_PRICE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c._max_price,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_PRICE_THRESHOLD_INCLUDES_TRANSFER,
        translation_key=SENSOR_SETTING_PRICE_THRESHOLD_INCLUDES_TRANSFER,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c._price_threshold_includes_transfer,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_CONTROL_FACTOR_FUNCTION,
        translation_key=SENSOR_SETTING_CONTROL_FACTOR_FUNCTION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c._control_factor_function,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_ACCEPTABLE_RANK,
        translation_key=SENSOR_SETTING_ACCEPTABLE_RANK,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c._max_rank,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_FORWARD_WINDOW,
        translation_key=SENSOR_SETTING_FORWARD_WINDOW,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda c: c._forward_avg_hours,
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_ACTIVE_TRANSFER_GROUP,
        translation_key=SENSOR_SETTING_ACTIVE_TRANSFER_GROUP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.active_transfer_group_label(),
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_ACTIVE_TRANSFER_TIER,
        translation_key=SENSOR_SETTING_ACTIVE_TRANSFER_TIER,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.active_transfer_tier_label(),
    ),
    KilowahtiSensorEntityDescription(
        key=SENSOR_SETTING_ACTIVE_FIXED_PERIOD,
        translation_key=SENSOR_SETTING_ACTIVE_FIXED_PERIOD,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: (fp := c.fixed_period_active_now()) and fp.label,
    ),
)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: KilowahtiCoordinator = hass.data[DOMAIN][entry.entry_id]
    has_transfer = bool(coordinator._transfer_groups)
    entities: list[SensorEntity] = []

    for description in SENSOR_DESCRIPTIONS:
        if description.key in _TRANSFER_SENSOR_KEYS and not has_transfer:
            continue
        if description.key == SENSOR_SPOT_PRICE:
            entities.append(KilowahtiSpotPriceSensor(coordinator, entry, description))
        elif description.key == SENSOR_CONTROL_FACTOR_TRANSFER:
            entities.append(KilowahtiTransferRankSensor(coordinator, entry, description))
        else:
            entities.append(KilowahtiSensor(coordinator, entry, description))

    # Effective price sensor (has extra attributes)
    entities.append(
        KilowahtiEffectivePriceSensor(
            coordinator,
            entry,
            _price_sensor(
                SENSOR_EFFECTIVE_PRICE, lambda c: c.format_price(c.effective_price_now())
            ),
        )
    )

    # Optimization score sensors (one pair per profile) + formula diagnostic
    for profile in coordinator.score_profiles:
        entities.append(KilowahtiScoreSensor(coordinator, entry, profile, "daily"))
        entities.append(KilowahtiScoreSensor(coordinator, entry, profile, "monthly"))
        entities.append(KilowahtiScoreFormulaSensor(coordinator, entry, profile))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Kilowahti {entry.options.get('name', '')}".strip(),
        manufacturer="Kilowahti",
        model="Spot Price Integration",
    )


class KilowahtiSensorBase(CoordinatorEntity[KilowahtiCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KilowahtiCoordinator,
        entry: ConfigEntry,
        description: KilowahtiSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_unit_of_measurement(self) -> str | None:
        # Price sensors inherit dynamic unit from coordinator
        if self.entity_description.key in _PRICE_SENSOR_KEYS:
            return self.coordinator.native_unit
        return self.entity_description.native_unit_of_measurement

    @property
    def suggested_display_precision(self) -> int | None:
        key = self.entity_description.key
        if key in _CONTROL_FACTOR_SENSOR_KEYS:
            return 3
        if key in _PRICE_SENSOR_KEYS:
            base = 5 if self.coordinator._high_precision else 2
            euro_extra = 2 if self.coordinator.native_unit == UNIT_EUROKWH else 0
            return base + euro_extra
        return None


# ---------------------------------------------------------------------------
# Standard parametric sensor
# ---------------------------------------------------------------------------


class KilowahtiSensor(KilowahtiSensorBase):
    @property
    def native_value(self) -> Any:
        fn = self.entity_description.value_fn
        if fn is None:
            return None
        try:
            return fn(self.coordinator)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Spot price sensor (with optional price array attributes)
# ---------------------------------------------------------------------------


class KilowahtiSpotPriceSensor(KilowahtiSensor):
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        today_arr = self.coordinator.today_price_array()
        if today_arr is not None:
            attrs["today_prices"] = today_arr
        tomorrow_arr = self.coordinator.tomorrow_price_array()
        if tomorrow_arr is not None:
            attrs["tomorrow_prices"] = tomorrow_arr
        return attrs


# ---------------------------------------------------------------------------
# Effective price sensor (source + period_label attributes)
# ---------------------------------------------------------------------------


class KilowahtiEffectivePriceSensor(KilowahtiSensor):
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        period = self.coordinator.fixed_period_active_now()
        if period is not None:
            return {"source": "fixed", "period_label": period.label}
        return {"source": "spot", "period_label": None}


# ---------------------------------------------------------------------------
# Transfer rank sensor
# ---------------------------------------------------------------------------


class KilowahtiTransferRankSensor(KilowahtiSensorBase):
    @property
    def suggested_display_precision(self) -> int:
        return 2

    @property
    def native_value(self) -> float | None:
        info = self.coordinator.transfer_rank_info()
        if info is None:
            return None
        rank, total = info
        return 0.0 if total <= 1 else (rank - 1) / (total - 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.coordinator.transfer_rank_info()
        return {"tier_count": info[1]} if info is not None else {}


# ---------------------------------------------------------------------------
# Score sensor
# ---------------------------------------------------------------------------


class KilowahtiScoreSensor(CoordinatorEntity[KilowahtiCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: KilowahtiCoordinator,
        entry: ConfigEntry,
        profile: ScoreProfile,
        period: str,  # "daily" or "monthly"
    ) -> None:
        super().__init__(coordinator)
        self._profile = profile
        self._period = period
        suffix = f"score_{profile.id}_{period}"
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_name = f"{profile.label} score ({period})"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self._period == "daily":
            score = self.coordinator.get_daily_score(self._profile.id)
        else:
            score = self.coordinator.get_monthly_score(self._profile.id)

        if score is None:
            return None
        return round(score, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._period == "daily":
            prev = self.coordinator.get_previous_daily_score(self._profile.id)
        else:
            prev = self.coordinator.get_previous_monthly_score(self._profile.id)
        return {"previous": round(prev, 1) if prev is not None else None}


# ---------------------------------------------------------------------------
# Score formula diagnostic sensor (one per profile)
# ---------------------------------------------------------------------------


class KilowahtiScoreFormulaSensor(CoordinatorEntity[KilowahtiCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: KilowahtiCoordinator,
        entry: ConfigEntry,
        profile: ScoreProfile,
    ) -> None:
        super().__init__(coordinator)
        self._profile_id = profile.id
        self._attr_unique_id = f"{entry.entry_id}_score_{profile.id}_formula"
        self._attr_name = f"{profile.label} score formula"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str | None:
        profile = next(
            (p for p in self.coordinator.score_profiles if p.id == self._profile_id), None
        )
        return profile.formula if profile else None
