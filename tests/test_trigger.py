"""Tests for Kilowahti automation triggers."""

from __future__ import annotations

from custom_components.kilowahti.const import (
    DOMAIN,
    TRIGGER_BECAME_CHEAPEST_SLOT,
    TRIGGER_FIXED_PERIOD_ENDED,
    TRIGGER_FIXED_PERIOD_STARTED,
    TRIGGER_PRICE_BECAME_ACCEPTABLE,
    TRIGGER_PRICE_NO_LONGER_ACCEPTABLE,
    TRIGGER_RANK_BECAME_ACCEPTABLE,
    TRIGGER_RANK_NO_LONGER_ACCEPTABLE,
    TRIGGER_TOMORROW_PRICES_AVAILABLE,
)
from custom_components.kilowahti.trigger import (
    PriceBecameAcceptableTrigger,
    RankBecameAcceptableTrigger,
    async_get_triggers,
)
from homeassistant.helpers.trigger import TriggerConfig

_ALL_TRIGGER_KEYS = {
    TRIGGER_PRICE_BECAME_ACCEPTABLE,
    TRIGGER_PRICE_NO_LONGER_ACCEPTABLE,
    TRIGGER_RANK_BECAME_ACCEPTABLE,
    TRIGGER_RANK_NO_LONGER_ACCEPTABLE,
    TRIGGER_BECAME_CHEAPEST_SLOT,
    TRIGGER_FIXED_PERIOD_STARTED,
    TRIGGER_FIXED_PERIOD_ENDED,
    TRIGGER_TOMORROW_PRICES_AVAILABLE,
}


class _Runner:
    """Collect run_action invocations."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict, str]] = []

    def __call__(self, payload, description, context=None):
        self.calls.append((payload, description))
        return None


def _config(key):
    return TriggerConfig(key=key, target=None, options={})


async def test_async_get_triggers_lists_all(hass) -> None:
    triggers = await async_get_triggers(hass)
    assert set(triggers) == _ALL_TRIGGER_KEYS
    assert triggers[TRIGGER_PRICE_BECAME_ACCEPTABLE] is PriceBecameAcceptableTrigger


async def test_price_fires_on_transition_to_acceptable(
    hass, setup_integration, mock_utcnow
) -> None:
    """Rising edge: price crossing from not-acceptable to acceptable fires once."""
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    runner = _Runner()

    coordinator._max_price_value = -1000.0
    trigger = PriceBecameAcceptableTrigger(hass, _config(TRIGGER_PRICE_BECAME_ACCEPTABLE))
    unsub = await trigger.async_attach_runner(runner)

    coordinator._max_price_value = 1000.0
    coordinator.async_update_listeners()
    unsub()

    assert len(runner.calls) == 1
    payload, description = runner.calls[0]
    assert payload == {"entry_id": setup_integration.entry_id}
    assert description == "price became acceptable"


async def test_price_no_longer_acceptable_fires_on_falling(
    hass, setup_integration, mock_utcnow
) -> None:
    """Falling edge: price crossing from acceptable to not-acceptable fires once."""
    from custom_components.kilowahti.trigger import PriceNoLongerAcceptableTrigger

    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    runner = _Runner()

    coordinator._max_price_value = 1000.0
    trigger = PriceNoLongerAcceptableTrigger(hass, _config(TRIGGER_PRICE_NO_LONGER_ACCEPTABLE))
    unsub = await trigger.async_attach_runner(runner)

    coordinator._max_price_value = -1000.0
    coordinator.async_update_listeners()
    unsub()

    assert len(runner.calls) == 1
    assert runner.calls[0][1] == "price no longer acceptable"


async def test_rank_fires_on_transition_to_acceptable(hass, setup_integration, mock_utcnow) -> None:
    """Rising edge on the rank threshold fires once."""
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    runner = _Runner()

    coordinator._max_rank_value = 0
    trigger = RankBecameAcceptableTrigger(hass, _config(TRIGGER_RANK_BECAME_ACCEPTABLE))
    unsub = await trigger.async_attach_runner(runner)

    coordinator._max_rank_value = 999
    coordinator.async_update_listeners()
    unsub()

    assert len(runner.calls) == 1
    assert runner.calls[0][1] == "rank became acceptable"


async def test_no_fire_when_already_acceptable(hass, setup_integration, mock_utcnow) -> None:
    """No fire when price is acceptable at attach time and stays acceptable."""
    coordinator = hass.data[DOMAIN][setup_integration.entry_id]
    runner = _Runner()

    coordinator._max_price_value = 1000.0
    trigger = PriceBecameAcceptableTrigger(hass, _config(TRIGGER_PRICE_BECAME_ACCEPTABLE))
    unsub = await trigger.async_attach_runner(runner)

    coordinator.async_update_listeners()
    unsub()

    assert runner.calls == []


async def test_no_coordinator_returns_noop(hass) -> None:
    """Attaching with no loaded entry yields a no-op detach and never fires."""
    runner = _Runner()
    trigger = PriceBecameAcceptableTrigger(hass, _config(TRIGGER_PRICE_BECAME_ACCEPTABLE))
    unsub = await trigger.async_attach_runner(runner)
    unsub()
    assert runner.calls == []
