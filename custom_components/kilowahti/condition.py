"""Automation conditions for the Kilowahti integration.

Purpose-specific conditions (Home Assistant 2026.7+). These evaluate the
coordinator's current state at check time — e.g. whether the current price is
acceptable — so automations can gate actions on price without templating.
"""

from __future__ import annotations

from typing import Unpack, cast

import voluptuous as vol

from homeassistant.const import CONF_OPTIONS, CONF_TARGET
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.condition import (
    Condition,
    ConditionCheckParams,
    ConditionConfig,
)
from homeassistant.helpers.typing import ConfigType

from ._target import resolve_coordinator
from .const import (
    CONDITION_FIXED_PERIOD_ACTIVE,
    CONDITION_PRICE_IS_ACCEPTABLE,
    CONDITION_PRICE_IS_LOWEST_TODAY,
    CONDITION_RANK_IS_ACCEPTABLE,
    CONDITION_TOMORROW_AVAILABLE,
)
from .coordinator import KilowahtiCoordinator

_CONDITION_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Optional(CONF_OPTIONS, default={}): {},
    }
)


class _KilowahtiCondition(Condition):
    """Base for Kilowahti coordinator-backed conditions.

    Subclasses provide a boolean predicate over the coordinator. The condition
    fails (returns False) when no coordinator resolves or the value is unknown.
    """

    _schema = _CONDITION_SCHEMA

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        """Validate config."""
        return cast(ConfigType, cls._schema(config))

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        """Initialize the condition."""
        super().__init__(hass, config)
        self._target = config.target
        self._options = config.options or {}

    def _predicate(self, coordinator: KilowahtiCoordinator) -> bool | None:
        """Return the current boolean state, or None when it cannot be determined."""
        raise NotImplementedError

    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool:
        """Check the condition."""
        coordinator = resolve_coordinator(self._hass, self._target)
        if coordinator is None:
            return False
        return self._predicate(coordinator) is True


def _price_acceptable(coordinator: KilowahtiCoordinator) -> bool | None:
    price = coordinator._price_for_comparison()
    if price is None:
        return None
    return price <= coordinator._max_price


def _rank_acceptable(coordinator: KilowahtiCoordinator) -> bool | None:
    rank = coordinator.current_rank()
    if rank is None:
        return None
    return rank <= coordinator._max_rank


def _is_lowest_today(coordinator: KilowahtiCoordinator) -> bool | None:
    rank = coordinator.total_price_rank_now()
    if rank is None:
        return None
    return rank == 1


def _fixed_period_active(coordinator: KilowahtiCoordinator) -> bool:
    return coordinator.fixed_period_active_now() is not None


def _tomorrow_available(coordinator: KilowahtiCoordinator) -> bool:
    return coordinator.tomorrow_slots() is not None


class PriceIsAcceptableCondition(_KilowahtiCondition):
    """Pass when the current price is at or below the configured threshold."""

    _predicate = staticmethod(_price_acceptable)


class RankIsAcceptableCondition(_KilowahtiCondition):
    """Pass when the current slot rank is at or below the configured threshold."""

    _predicate = staticmethod(_rank_acceptable)


class PriceIsLowestTodayCondition(_KilowahtiCondition):
    """Pass when the current slot is the cheapest of the day."""

    _predicate = staticmethod(_is_lowest_today)


class FixedPeriodActiveCondition(_KilowahtiCondition):
    """Pass when a fixed-price period is currently active."""

    _predicate = staticmethod(_fixed_period_active)


class TomorrowAvailableCondition(_KilowahtiCondition):
    """Pass when tomorrow's prices are available."""

    _predicate = staticmethod(_tomorrow_available)


CONDITIONS: dict[str, type[Condition]] = {
    CONDITION_PRICE_IS_ACCEPTABLE: PriceIsAcceptableCondition,
    CONDITION_RANK_IS_ACCEPTABLE: RankIsAcceptableCondition,
    CONDITION_PRICE_IS_LOWEST_TODAY: PriceIsLowestTodayCondition,
    CONDITION_FIXED_PERIOD_ACTIVE: FixedPeriodActiveCondition,
    CONDITION_TOMORROW_AVAILABLE: TomorrowAvailableCondition,
}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return the conditions provided by Kilowahti."""
    return CONDITIONS
