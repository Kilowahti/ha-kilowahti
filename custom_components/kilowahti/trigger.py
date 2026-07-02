"""Automation triggers for the Kilowahti integration.

Purpose-specific triggers (Home Assistant 2026.7+). Each trigger watches the
coordinator and fires on a semantic edge — e.g. the current price crossing down
to an acceptable level — rather than requiring the user to build state triggers
against raw sensor entities.
"""

from __future__ import annotations

from typing import cast

import voluptuous as vol

from homeassistant.const import CONF_OPTIONS, CONF_TARGET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.trigger import (
    Trigger,
    TriggerActionRunner,
    TriggerConfig,
    TriggerNotTriggeredReporter,
)
from homeassistant.helpers.typing import ConfigType

from ._target import resolve_coordinator
from .const import (
    TRIGGER_BECAME_CHEAPEST_SLOT,
    TRIGGER_FIXED_PERIOD_ENDED,
    TRIGGER_FIXED_PERIOD_STARTED,
    TRIGGER_PRICE_BECAME_ACCEPTABLE,
    TRIGGER_PRICE_NO_LONGER_ACCEPTABLE,
    TRIGGER_RANK_BECAME_ACCEPTABLE,
    TRIGGER_RANK_NO_LONGER_ACCEPTABLE,
    TRIGGER_TOMORROW_PRICES_AVAILABLE,
)
from .coordinator import KilowahtiCoordinator

_TRIGGER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Optional(CONF_OPTIONS, default={}): {},
    }
)

# Edge directions.
_RISING = "rising"  # fire on transition False -> True
_FALLING = "falling"  # fire on transition True -> False


class _KilowahtiTrigger(Trigger):
    """Base for Kilowahti coordinator-backed edge triggers.

    Subclasses provide a boolean predicate over the coordinator and a direction.
    The trigger seeds its initial state without firing, then fires whenever the
    predicate crosses the configured edge.
    """

    _schema = _TRIGGER_SCHEMA
    _direction: str
    _description: str

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        """Validate config."""
        return cast(ConfigType, cls._schema(config))

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Initialize the trigger."""
        super().__init__(hass, config)
        self._target = config.target
        self._options = config.options or {}

    def _predicate(self, coordinator: KilowahtiCoordinator) -> bool | None:
        """Return the current boolean state, or None when it cannot be determined."""
        raise NotImplementedError

    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: TriggerNotTriggeredReporter | None = None,
    ) -> CALLBACK_TYPE:
        """Attach the trigger to an action runner."""
        coordinator = resolve_coordinator(self._hass, self._target)
        if coordinator is None:
            return lambda: None

        state: dict[str, bool | None] = {"last": self._predicate(coordinator)}

        @callback
        def _check() -> None:
            now = self._predicate(coordinator)
            if self._direction == _RISING:
                fired = state["last"] is False and now is True
            else:
                fired = state["last"] is True and now is False
            if fired:
                run_action({"entry_id": coordinator.entry_id}, self._description)
            state["last"] = now

        return coordinator.async_add_listener(_check)


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


def _is_cheapest_slot(coordinator: KilowahtiCoordinator) -> bool | None:
    rank = coordinator.total_price_rank_now()
    if rank is None:
        return None
    return rank == 1


def _fixed_period_active(coordinator: KilowahtiCoordinator) -> bool:
    return coordinator.fixed_period_active_now() is not None


def _tomorrow_available(coordinator: KilowahtiCoordinator) -> bool:
    return coordinator.tomorrow_slots() is not None


class PriceBecameAcceptableTrigger(_KilowahtiTrigger):
    """Fire when the price crosses down to an acceptable level."""

    _direction = _RISING
    _description = "price became acceptable"
    _predicate = staticmethod(_price_acceptable)


class PriceNoLongerAcceptableTrigger(_KilowahtiTrigger):
    """Fire when the price crosses up past the acceptable threshold."""

    _direction = _FALLING
    _description = "price no longer acceptable"
    _predicate = staticmethod(_price_acceptable)


class RankBecameAcceptableTrigger(_KilowahtiTrigger):
    """Fire when the current slot rank drops to an acceptable level."""

    _direction = _RISING
    _description = "rank became acceptable"
    _predicate = staticmethod(_rank_acceptable)


class RankNoLongerAcceptableTrigger(_KilowahtiTrigger):
    """Fire when the current slot rank rises past the acceptable level."""

    _direction = _FALLING
    _description = "rank no longer acceptable"
    _predicate = staticmethod(_rank_acceptable)


class BecameCheapestSlotTrigger(_KilowahtiTrigger):
    """Fire when the current slot becomes the cheapest of the day."""

    _direction = _RISING
    _description = "became cheapest slot"
    _predicate = staticmethod(_is_cheapest_slot)


class FixedPeriodStartedTrigger(_KilowahtiTrigger):
    """Fire when a fixed-price period becomes active."""

    _direction = _RISING
    _description = "fixed period started"
    _predicate = staticmethod(_fixed_period_active)


class FixedPeriodEndedTrigger(_KilowahtiTrigger):
    """Fire when a fixed-price period stops being active."""

    _direction = _FALLING
    _description = "fixed period ended"
    _predicate = staticmethod(_fixed_period_active)


class TomorrowPricesAvailableTrigger(_KilowahtiTrigger):
    """Fire when tomorrow's prices become available."""

    _direction = _RISING
    _description = "tomorrow prices available"
    _predicate = staticmethod(_tomorrow_available)


TRIGGERS: dict[str, type[Trigger]] = {
    TRIGGER_PRICE_BECAME_ACCEPTABLE: PriceBecameAcceptableTrigger,
    TRIGGER_PRICE_NO_LONGER_ACCEPTABLE: PriceNoLongerAcceptableTrigger,
    TRIGGER_RANK_BECAME_ACCEPTABLE: RankBecameAcceptableTrigger,
    TRIGGER_RANK_NO_LONGER_ACCEPTABLE: RankNoLongerAcceptableTrigger,
    TRIGGER_BECAME_CHEAPEST_SLOT: BecameCheapestSlotTrigger,
    TRIGGER_FIXED_PERIOD_STARTED: FixedPeriodStartedTrigger,
    TRIGGER_FIXED_PERIOD_ENDED: FixedPeriodEndedTrigger,
    TRIGGER_TOMORROW_PRICES_AVAILABLE: TomorrowPricesAvailableTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by Kilowahti."""
    return TRIGGERS
