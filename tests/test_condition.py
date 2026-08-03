"""Tests for Kilowahti automation conditions."""

from __future__ import annotations

from custom_components.kilowahti.condition import (
    PriceIsAcceptableCondition,
    RankIsAcceptableCondition,
    async_get_conditions,
)
from custom_components.kilowahti.const import (
    CONDITION_FIXED_PERIOD_ACTIVE,
    CONDITION_PRICE_IS_ACCEPTABLE,
    CONDITION_PRICE_IS_LOWEST_TODAY,
    CONDITION_RANK_IS_ACCEPTABLE,
    CONDITION_TOMORROW_AVAILABLE,
    DOMAIN,
)
from homeassistant.helpers.condition import ConditionConfig

_ALL_CONDITION_KEYS = {
    CONDITION_PRICE_IS_ACCEPTABLE,
    CONDITION_RANK_IS_ACCEPTABLE,
    CONDITION_PRICE_IS_LOWEST_TODAY,
    CONDITION_FIXED_PERIOD_ACTIVE,
    CONDITION_TOMORROW_AVAILABLE,
}


def _config():
    return ConditionConfig(options={}, target=None)


async def test_async_get_conditions_lists_all(hass) -> None:
    conditions = await async_get_conditions(hass)
    assert set(conditions) == _ALL_CONDITION_KEYS
    assert conditions[CONDITION_PRICE_IS_ACCEPTABLE] is PriceIsAcceptableCondition


async def test_price_true_when_below_threshold(hass, setup_integration, mock_utcnow) -> None:
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    coordinator._max_price_value = 1000.0
    assert PriceIsAcceptableCondition(hass, _config())._async_check() is True


async def test_price_false_when_above_threshold(hass, setup_integration, mock_utcnow) -> None:
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    coordinator._max_price_value = -1000.0
    assert PriceIsAcceptableCondition(hass, _config())._async_check() is False


async def test_rank_true_when_below_threshold(hass, setup_integration, mock_utcnow) -> None:
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    coordinator._max_rank_value = 999
    assert RankIsAcceptableCondition(hass, _config())._async_check() is True


async def test_rank_false_when_above_threshold(hass, setup_integration, mock_utcnow) -> None:
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    coordinator._max_rank_value = 0
    assert RankIsAcceptableCondition(hass, _config())._async_check() is False


async def test_false_when_no_coordinator(hass) -> None:
    assert PriceIsAcceptableCondition(hass, _config())._async_check() is False
