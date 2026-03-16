"""Coordinator for the Kilowahti integration."""

from __future__ import annotations

import calendar
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from kilowahti import calc
from kilowahti.sources.spot_hinta import SpotHintaRateLimitError, SpotHintaSource

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_POWER_KW,
    CONF_CONTROL_FACTOR_FUNCTION,
    CONF_CONTROL_FACTOR_SCALING,
    CONF_DISPLAY_UNIT,
    CONF_EAGER_END_HOUR,
    CONF_EAGER_START_HOUR,
    CONF_ELECTRICITY_TAX,
    CONF_EXPORT_COMMISSION,
    CONF_EXPORT_PRICE_THRESHOLD,
    CONF_EXPORT_PRICING_MODE,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_FIXED_EXPORT_RATE,
    CONF_FORWARD_AVG_HOURS,
    CONF_GENERATION_ENABLED,
    CONF_HIGH_PRECISION,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_MONTHLY_FIXED_COST,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_SHOW_ROLLING_AVERAGES,
    CONF_SOLAR_WINDOW_END,
    CONF_SOLAR_WINDOW_START,
    CONF_SPOT_COMMISSION,
    CONF_TRANSFER_GROUPS,
    CONF_VAT_RATE,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_POWER_KW,
    DEFAULT_CONTROL_FACTOR_FUNCTION,
    DEFAULT_CONTROL_FACTOR_SCALING,
    DEFAULT_EAGER_END_HOUR,
    DEFAULT_EAGER_START_HOUR,
    DEFAULT_ELECTRICITY_TAX,
    DEFAULT_EXPORT_COMMISSION,
    DEFAULT_EXPORT_PRICE_THRESHOLD,
    DEFAULT_EXPORT_PRICING_MODE,
    DEFAULT_EXPOSE_PRICE_ARRAYS,
    DEFAULT_FIXED_EXPORT_RATE,
    DEFAULT_FORWARD_AVG_HOURS,
    DEFAULT_GENERATION_ENABLED,
    DEFAULT_HIGH_PRECISION,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
    DEFAULT_MONTHLY_FIXED_COST,
    DEFAULT_PRICE_RESOLUTION,
    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    DEFAULT_SHOW_ROLLING_AVERAGES,
    DEFAULT_SOLAR_WINDOW_END,
    DEFAULT_SOLAR_WINDOW_START,
    DEFAULT_SPOT_COMMISSION,
    DEFAULT_VAT_RATE,
    DOMAIN,
    EXPORT_PRICING_FIXED,
    UNIT_EUROKWH,
    UNIT_SNTPERKWH,
)
from .models import FixedPeriod, PriceResolution, PriceSlot, ScoreProfile, TransferGroup
from .storage import KilowahtiStorage

_LOGGER = logging.getLogger(__name__)

# Debounce interval for persisting score accumulators
_SCORE_PERSIST_DEBOUNCE = 60  # seconds


class KilowahtiCoordinator(DataUpdateCoordinator[None]):
    """Manages price data lifecycle and all derived values for Kilowahti."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Any,  # ConfigEntry
        storage: KilowahtiStorage,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # We manage our own schedule
        )
        self._entry = entry
        self._storage = storage
        self._source = SpotHintaSource()

        # Threshold instance vars — updated by number entities and synced in the options listener
        self._max_price_value: float = entry.options.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE)
        self._max_rank_value: int = entry.options.get(CONF_MAX_RANK, DEFAULT_MAX_RANK)
        self._last_known_options: dict = dict(entry.options)

        # Price state
        self._today_slots: list[PriceSlot] = []
        self._tomorrow_slots: list[PriceSlot] | None = None
        self._today_date: date | None = None

        # Score state
        self._score_data: dict[str, dict[str, float]] = {}
        self._daily_history: list[dict] = []
        self._month_scores: list[dict] = []
        self._last_meter_values: dict[str, float] = {}
        self._score_persist_unsub: Callable | None = None

        # Timer management
        self._unsubscribers: list[Callable] = []
        self._eager_poll_unsub: Callable | None = None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @property
    def _opts(self) -> dict:
        return self._entry.options

    @property
    def _region(self) -> str:
        return self._opts[CONF_REGION]

    @property
    def _resolution(self) -> PriceResolution:
        return PriceResolution(self._opts.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION))

    @property
    def _vat_rate(self) -> float:
        return self._opts.get(CONF_VAT_RATE, DEFAULT_VAT_RATE)

    @property
    def _spot_commission(self) -> float:
        return self._opts.get(CONF_SPOT_COMMISSION, DEFAULT_SPOT_COMMISSION)

    @property
    def _electricity_tax(self) -> float:
        return self._opts.get(CONF_ELECTRICITY_TAX, DEFAULT_ELECTRICITY_TAX)

    @property
    def _display_unit(self) -> str:
        return self._opts.get(CONF_DISPLAY_UNIT, UNIT_SNTPERKWH)

    @property
    def _max_price(self) -> float:
        return self._max_price_value

    @property
    def _max_rank(self) -> int:
        return self._max_rank_value

    @property
    def _price_threshold_includes_transfer(self) -> bool:
        return self._opts.get(
            CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER, DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER
        )

    @property
    def _forward_avg_hours(self) -> float:
        return self._opts.get(CONF_FORWARD_AVG_HOURS, DEFAULT_FORWARD_AVG_HOURS)

    @property
    def _control_factor_function(self) -> str:
        return self._opts.get(CONF_CONTROL_FACTOR_FUNCTION, DEFAULT_CONTROL_FACTOR_FUNCTION)

    @property
    def _control_factor_scaling(self) -> float:
        return self._opts.get(CONF_CONTROL_FACTOR_SCALING, DEFAULT_CONTROL_FACTOR_SCALING)

    @property
    def _expose_price_arrays(self) -> bool:
        return self._opts.get(CONF_EXPOSE_PRICE_ARRAYS, DEFAULT_EXPOSE_PRICE_ARRAYS)

    @property
    def _high_precision(self) -> bool:
        return self._opts.get(CONF_HIGH_PRECISION, DEFAULT_HIGH_PRECISION)

    @property
    def generation_enabled(self) -> bool:
        return self._opts.get(CONF_GENERATION_ENABLED, DEFAULT_GENERATION_ENABLED)

    @property
    def show_rolling_averages(self) -> bool:
        if self._resolution == PriceResolution.HOUR:
            return False
        return self._opts.get(CONF_SHOW_ROLLING_AVERAGES, DEFAULT_SHOW_ROLLING_AVERAGES)

    @property
    def battery_sensors_enabled(self) -> bool:
        return self.generation_enabled and self._battery_capacity_kwh > 0

    @property
    def _export_pricing_mode(self) -> str:
        return self._opts.get(CONF_EXPORT_PRICING_MODE, DEFAULT_EXPORT_PRICING_MODE)

    @property
    def _export_commission(self) -> float:
        return self._opts.get(CONF_EXPORT_COMMISSION, DEFAULT_EXPORT_COMMISSION)

    @property
    def _fixed_export_rate(self) -> float:
        return self._opts.get(CONF_FIXED_EXPORT_RATE, DEFAULT_FIXED_EXPORT_RATE)

    @property
    def _export_price_threshold(self) -> float:
        return self._opts.get(CONF_EXPORT_PRICE_THRESHOLD, DEFAULT_EXPORT_PRICE_THRESHOLD)

    @property
    def _solar_window_start(self) -> int:
        return self._opts.get(CONF_SOLAR_WINDOW_START, DEFAULT_SOLAR_WINDOW_START)

    @property
    def _solar_window_end(self) -> int:
        return self._opts.get(CONF_SOLAR_WINDOW_END, DEFAULT_SOLAR_WINDOW_END)

    @property
    def _battery_capacity_kwh(self) -> float:
        return self._opts.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)

    @property
    def _battery_charge_power_kw(self) -> float:
        return self._opts.get(CONF_BATTERY_CHARGE_POWER_KW, DEFAULT_BATTERY_CHARGE_POWER_KW)

    @property
    def _monthly_fixed_cost(self) -> float:
        main = self._opts.get(CONF_MONTHLY_FIXED_COST, DEFAULT_MONTHLY_FIXED_COST)
        group = self._active_transfer_group
        group_cost = group.monthly_fixed_cost if group is not None else 0.0
        return main + group_cost

    @property
    def score_profiles(self) -> list[ScoreProfile]:
        return [ScoreProfile.from_dict(p) for p in self._opts.get(CONF_SCORE_PROFILES, [])]

    @property
    def _transfer_groups(self) -> list[TransferGroup]:
        return [TransferGroup.from_dict(g) for g in self._opts.get(CONF_TRANSFER_GROUPS, [])]

    @property
    def _active_transfer_group(self) -> TransferGroup | None:
        for g in self._transfer_groups:
            if g.active:
                return g
        return None

    # ------------------------------------------------------------------
    # Threshold setters — called by number entities
    # ------------------------------------------------------------------

    def set_price_threshold(self, value: float) -> None:
        """Update price threshold in memory and persist to options."""
        self._max_price_value = value
        self._last_known_options = {**self._entry.options, CONF_MAX_PRICE: value}
        self.hass.config_entries.async_update_entry(self._entry, options=self._last_known_options)
        self.async_update_listeners()

    def set_rank_threshold(self, value: int) -> None:
        """Update rank threshold in memory and persist to options."""
        self._max_rank_value = value
        self._last_known_options = {**self._entry.options, CONF_MAX_RANK: value}
        self.hass.config_entries.async_update_entry(self._entry, options=self._last_known_options)
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator overrides
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> None:
        """Load or fetch price data on startup. Called by async_config_entry_first_refresh."""
        today = dt_util.as_local(dt_util.utcnow()).date()

        # Try cache first
        if self._storage.is_cache_valid_for(today):
            raw_today, raw_tomorrow, _ = self._storage.get_cache()
            self._today_slots = [PriceSlot.from_dict(s) for s in (raw_today or [])]
            self._tomorrow_slots = (
                [PriceSlot.from_dict(s) for s in raw_tomorrow] if raw_tomorrow else None
            )
            self._today_date = today
            _LOGGER.debug("Restored %d today-slots from cache", len(self._today_slots))
        else:
            # Cache stale or missing — fetch from API
            try:
                self._today_slots = await self._source.fetch_today(
                    async_get_clientsession(self.hass), self._region, self._resolution
                )
            except Exception as err:
                raise UpdateFailed(f"Failed to fetch today's prices: {err}") from err

            self._today_date = today
            self._tomorrow_slots = None
            await self._storage.async_save_cache(self._today_slots, None, today)
            _LOGGER.info("Fetched %d today-slots from API", len(self._today_slots))

        # Restore score accumulators
        self._score_data = self._storage.get_score_data()
        self._daily_history = self._storage.get_daily_history()
        self._month_scores = self._storage.get_month_scores()
        self._last_meter_values = self._storage.get_last_meter_values()

        return None

    # ------------------------------------------------------------------
    # Timer setup / teardown
    # ------------------------------------------------------------------

    async def async_setup_timers(self) -> None:
        """Set up all time-based tracking. Call after first_refresh."""
        resolution_minutes = self._resolution.value
        eager_start = self._opts.get(CONF_EAGER_START_HOUR, DEFAULT_EAGER_START_HOUR)
        eager_end = self._opts.get(CONF_EAGER_END_HOUR, DEFAULT_EAGER_END_HOUR)

        # Slot boundary updates: fire at actual clock boundaries (:00/:15/:30/:45 or :00 only)
        # async_track_time_interval would drift if registered mid-slot; time_change aligns to wall clock.
        for _minute in range(0, 60, resolution_minutes):
            self._unsubscribers.append(
                async_track_time_change(
                    self.hass,
                    self._on_slot_boundary,
                    minute=_minute,
                    second=0,
                )
            )

        self._unsubscribers.extend(
            [
                # Midnight rollover
                async_track_time_change(
                    self.hass,
                    self._on_midnight,
                    hour=0,
                    minute=0,
                    second=0,
                ),
                # Start eager polling for tomorrow's prices
                async_track_time_change(
                    self.hass,
                    self._on_eager_fetch_start,
                    hour=eager_start,
                    minute=0,
                    second=0,
                ),
            ]
        )

        await self._async_setup_score_tracking()

        # If we're already past eager_start and missing tomorrow, start polling now
        now_local = dt_util.as_local(dt_util.utcnow())
        if self._tomorrow_slots is None and eager_start <= now_local.hour < eager_end:
            self.hass.async_create_task(self._async_eager_poll())

    def async_unload(self) -> None:
        """Cancel all subscriptions and pending tasks."""
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()

        if self._eager_poll_unsub is not None:
            self._eager_poll_unsub()
            self._eager_poll_unsub = None

        if self._score_persist_unsub is not None:
            self._score_persist_unsub()
            self._score_persist_unsub = None

    # ------------------------------------------------------------------
    # Timer callbacks
    # ------------------------------------------------------------------

    @callback
    def _on_slot_boundary(self, _now: datetime) -> None:
        """Fire at each price-slot boundary to push updated sensor states."""
        self.async_update_listeners()

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        """Schedule midnight rollover as an async task."""
        self.hass.async_create_task(self._async_midnight_rollover())

    @callback
    def _on_eager_fetch_start(self, _now: datetime) -> None:
        """Start eager polling for tomorrow's prices."""
        if self._tomorrow_slots is not None:
            return
        self.hass.async_create_task(self._async_eager_poll())

    # ------------------------------------------------------------------
    # Midnight rollover
    # ------------------------------------------------------------------

    async def _async_midnight_rollover(self) -> None:
        today = dt_util.as_local(dt_util.utcnow()).date()

        if self._tomorrow_slots:
            self._today_slots = self._tomorrow_slots
            self._today_date = today
            self._tomorrow_slots = None
            _LOGGER.info(
                "Midnight rollover: promoted tomorrow → today (%d slots)", len(self._today_slots)
            )
        else:
            # No tomorrow cache — fetch today fresh
            try:
                self._today_slots = await self._source.fetch_today(
                    async_get_clientsession(self.hass), self._region, self._resolution
                )
                self._today_date = today
                _LOGGER.info(
                    "Midnight rollover: fetched today from API (%d slots)", len(self._today_slots)
                )
            except Exception as err:
                _LOGGER.error("Midnight rollover: failed to fetch today's prices: %s", err)

        await self._storage.async_save_cache(self._today_slots, None, today)

        # Finalise yesterday's score and reset
        await self._async_finalise_daily_scores()

        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Eager fetch for tomorrow's prices
    # ------------------------------------------------------------------

    async def _async_eager_poll(self) -> None:
        """Single poll attempt for tomorrow's prices; reschedules if not yet available."""
        if self._tomorrow_slots is not None:
            return

        eager_end = self._opts.get(CONF_EAGER_END_HOUR, DEFAULT_EAGER_END_HOUR)
        now_local = dt_util.as_local(dt_util.utcnow())
        if now_local.hour >= eager_end:
            _LOGGER.debug("Eager fetch: window closed at %d:00", eager_end)
            return

        try:
            slots = await self._source.fetch_tomorrow(
                async_get_clientsession(self.hass), self._region, self._resolution
            )
        except SpotHintaRateLimitError as err:
            _LOGGER.warning("Eager fetch: rate-limited; retrying in %ds", err.retry_after)
            self._schedule_eager_poll(err.retry_after)
            return
        except Exception as err:
            _LOGGER.warning("Eager fetch: error polling for tomorrow: %s", err)
            self._schedule_eager_poll(60)
            return

        if slots is None:
            _LOGGER.debug("Eager fetch: tomorrow not yet published; retrying in 60s")
            self._schedule_eager_poll(60)
            return

        self._tomorrow_slots = slots
        today = self._today_date or dt_util.as_local(dt_util.utcnow()).date()
        await self._storage.async_save_cache(self._today_slots, self._tomorrow_slots, today)
        _LOGGER.info("Eager fetch: got %d tomorrow-slots", len(slots))
        self.async_update_listeners()

    def _schedule_eager_poll(self, delay_seconds: float) -> None:
        if self._eager_poll_unsub is not None:
            self._eager_poll_unsub()
        self._eager_poll_unsub = async_call_later(
            self.hass,
            delay_seconds,
            self._trigger_eager_poll,
        )

    @callback
    def _trigger_eager_poll(self, _now: datetime) -> None:
        self._eager_poll_unsub = None
        self.hass.async_create_task(self._async_eager_poll())

    # ------------------------------------------------------------------
    # Current slot lookups
    # ------------------------------------------------------------------

    def _now_local(self) -> datetime:
        return dt_util.as_local(dt_util.utcnow())

    def current_slot(self) -> PriceSlot | None:
        """Return the PriceSlot for the current time."""
        now = self._now_local()
        all_slots = self._today_slots + (self._tomorrow_slots or [])
        # Find the slot whose start time is <= now and is the most recent
        candidate = None
        for slot in all_slots:
            slot_local = dt_util.as_local(slot.dt_utc)
            if slot_local <= now:
                candidate = slot
            else:
                break
        return candidate

    def current_rank(self) -> int | None:
        slot = self.current_slot()
        return slot.rank if slot else None

    def total_price_rank_now(self) -> int | None:
        """Return rank of the current slot's total price (spot + transfer) among today's slots.

        1 = cheapest. Tied slots share the lowest rank (competition ranking).
        Returns None if today's slots are unavailable or the current slot is not among them.
        """
        current = self.current_slot()
        if current is None:
            return None
        return calc.total_price_rank(
            current,
            self._today_slots,
            self._vat_rate,
            self._spot_commission,
            self._active_transfer_group,
            dt_util.as_local,
        )

    def current_quartile(self) -> int | None:
        rank = self.current_rank()
        if rank is None:
            return None
        return calc.price_quartile(rank, self._resolution.slots_per_day)

    def today_slots(self) -> list[PriceSlot]:
        return list(self._today_slots)

    def tomorrow_slots(self) -> list[PriceSlot] | None:
        return list(self._tomorrow_slots) if self._tomorrow_slots else None

    def slots_in_range(self, start: datetime, end: datetime) -> list[PriceSlot]:
        """Return all slots whose start time falls within [start, end)."""
        all_slots = self._today_slots + (self._tomorrow_slots or [])
        return calc.slots_in_range(all_slots, start, end)

    # ------------------------------------------------------------------
    # Price calculations
    # ------------------------------------------------------------------

    def _spot_effective(self, slot: PriceSlot) -> float:
        """Apply VAT to raw spot price, then add commission (gross). API always returns prices excl. VAT."""
        return calc.spot_effective(slot, self._vat_rate, self._spot_commission)

    def _energy_price_for_slot(self, slot: PriceSlot) -> float:
        """Return effective energy price for a slot, respecting fixed-price periods."""
        slot_date = dt_util.as_local(slot.dt_utc).date()
        fixed = self.fixed_period_for_date(slot_date)
        return fixed.price if fixed is not None else self._spot_effective(slot)

    def _synthetic_slots_for_date(self, d: date) -> list[PriceSlot]:
        """Generate zero-price slots for a date (used when no spot data but fixed period is active)."""
        tz_local = dt_util.get_time_zone(self.hass.config.time_zone)
        current = datetime(d.year, d.month, d.day, 0, 0, tzinfo=tz_local)
        slots = []
        while current.date() == d:
            slots.append(
                PriceSlot(dt_utc=current.astimezone(dt_util.UTC), price_no_tax=0.0, rank=0)
            )
            current += timedelta(minutes=self._resolution)
        return slots

    def spot_price_now(self) -> float | None:
        slot = self.current_slot()
        if slot is None:
            return None
        return self._spot_effective(slot)

    def fixed_period_for_date(self, d: date) -> FixedPeriod | None:
        return calc.fixed_period_for_date(self._storage.periods, d)

    def fixed_period_active_now(self) -> FixedPeriod | None:
        return self.fixed_period_for_date(self._now_local().date())

    def effective_price_now(self) -> float | None:
        period = self.fixed_period_active_now()
        if period is not None:
            return period.price
        return self.spot_price_now()

    def active_transfer_group_label(self) -> str | None:
        group = self._active_transfer_group
        return group.label if group else None

    def active_transfer_tier_label(self) -> str | None:
        group = self._active_transfer_group
        if group is None:
            return None
        now = self._now_local()
        for tier in sorted(group.tiers, key=lambda t: t.priority):
            if tier.matches(now.month, now.weekday(), now.hour):
                return tier.label
        return None

    def transfer_rank_info(self) -> tuple[int, int] | None:
        """Return (rank, tier_count) for the current transfer price among today's unique tiers.

        rank 1 = cheapest, tier_count = number of distinct prices that occur today.
        Returns None if no transfer group is active or no price matches.
        """
        group = self._active_transfer_group
        if group is None:
            return None
        return calc.transfer_rank_info(group, self._now_local())

    def transfer_price_for_slot(self, slot: PriceSlot) -> float | None:
        return calc.transfer_price_for_slot(slot, self._active_transfer_group, dt_util.as_local)

    def transfer_price_now(self) -> float | None:
        group = self._active_transfer_group
        if group is None:
            return None
        now = self._now_local()
        return group.price_at(now.month, now.weekday(), now.hour)

    def total_price_now(self) -> float | None:
        effective = self.effective_price_now()
        if effective is None:
            return None
        transfer = self.transfer_price_now() or 0.0
        return effective + transfer

    def _price_for_comparison(self) -> float | None:
        """Price compared against max_price threshold."""
        effective = self.effective_price_now()
        if effective is None:
            return None
        if self._price_threshold_includes_transfer:
            transfer = self.transfer_price_now() or 0.0
            return effective + transfer
        return effective

    def format_price(self, price_snt: float | None) -> float | None:
        """Convert c/kWh to display unit (€/kWh if configured)."""
        if price_snt is None:
            return None
        if self._display_unit == UNIT_EUROKWH:
            return price_snt / 100.0
        return price_snt

    @property
    def native_unit(self) -> str:
        return self._display_unit

    # ------------------------------------------------------------------
    # Today / tomorrow statistics
    # ------------------------------------------------------------------

    def _effective_prices_for_slots(self, slots: list[PriceSlot]) -> list[float]:
        return calc.effective_prices(slots, self._vat_rate, self._spot_commission)

    def _total_prices_for_slots(self, slots: list[PriceSlot]) -> list[float]:
        return [
            self._energy_price_for_slot(s) + (self.transfer_price_for_slot(s) or 0.0) for s in slots
        ]

    def today_spot_avg(self) -> float | None:
        if not self._today_slots:
            return None
        prices = self._effective_prices_for_slots(self._today_slots)
        return sum(prices) / len(prices)

    def today_spot_min(self) -> float | None:
        if not self._today_slots:
            return None
        return min(self._effective_prices_for_slots(self._today_slots))

    def today_spot_max(self) -> float | None:
        if not self._today_slots:
            return None
        return max(self._effective_prices_for_slots(self._today_slots))

    def tomorrow_spot_avg(self) -> float | None:
        if not self._tomorrow_slots:
            return None
        prices = self._effective_prices_for_slots(self._tomorrow_slots)
        return sum(prices) / len(prices)

    def tomorrow_spot_min(self) -> float | None:
        if not self._tomorrow_slots:
            return None
        return min(self._effective_prices_for_slots(self._tomorrow_slots))

    def tomorrow_spot_max(self) -> float | None:
        if not self._tomorrow_slots:
            return None
        return max(self._effective_prices_for_slots(self._tomorrow_slots))

    def today_total_avg(self) -> float | None:
        if not self._today_slots:
            return None
        prices = self._total_prices_for_slots(self._today_slots)
        return sum(prices) / len(prices)

    def today_total_min(self) -> float | None:
        if not self._today_slots:
            return None
        return min(self._total_prices_for_slots(self._today_slots))

    def today_total_max(self) -> float | None:
        if not self._today_slots:
            return None
        return max(self._total_prices_for_slots(self._today_slots))

    def _tomorrow_total_slots(self) -> list[PriceSlot] | None:
        """Return slots for tomorrow total stats: synthetic if fixed period active, otherwise spot slots."""
        tomorrow = (self._now_local() + timedelta(days=1)).date()
        if self.fixed_period_for_date(tomorrow) is not None:
            return self._synthetic_slots_for_date(tomorrow)
        return self._tomorrow_slots or None

    def tomorrow_total_avg(self) -> float | None:
        slots = self._tomorrow_total_slots()
        if not slots:
            return None
        prices = self._total_prices_for_slots(slots)
        return sum(prices) / len(prices)

    def tomorrow_total_min(self) -> float | None:
        slots = self._tomorrow_total_slots()
        if not slots:
            return None
        return min(self._total_prices_for_slots(slots))

    def tomorrow_total_max(self) -> float | None:
        slots = self._tomorrow_total_slots()
        if not slots:
            return None
        return max(self._total_prices_for_slots(slots))

    def next_hours_avg(self) -> float | None:
        now = self._now_local()
        end = now + timedelta(hours=self._forward_avg_hours)
        slots = self.slots_in_range(now, end)
        if not slots:
            return None
        prices = self._effective_prices_for_slots(slots)
        return sum(prices) / len(prices)

    # ------------------------------------------------------------------
    # E1 — Export price methods
    # ------------------------------------------------------------------

    def export_price_for_slot(self, slot: PriceSlot) -> float:
        """Feed-in price for a given slot. No VAT (small producers don't collect VAT in FI)."""
        if self._export_pricing_mode == EXPORT_PRICING_FIXED:
            return self._fixed_export_rate
        return max(0.0, slot.price_no_tax - self._export_commission)

    def _export_prices_for_slots(self, slots: list[PriceSlot]) -> list[float]:
        return [self.export_price_for_slot(s) for s in slots]

    def export_price_now(self) -> float | None:
        slot = self.current_slot()
        if slot is None:
            return None
        return self.export_price_for_slot(slot)

    def export_today_avg(self) -> float | None:
        if not self._today_slots:
            return None
        prices = self._export_prices_for_slots(self._today_slots)
        return sum(prices) / len(prices)

    def export_today_min(self) -> float | None:
        if not self._today_slots:
            return None
        return min(self._export_prices_for_slots(self._today_slots))

    def export_today_max(self) -> float | None:
        if not self._today_slots:
            return None
        return max(self._export_prices_for_slots(self._today_slots))

    def export_tomorrow_avg(self) -> float | None:
        if not self._tomorrow_slots:
            return None
        prices = self._export_prices_for_slots(self._tomorrow_slots)
        return sum(prices) / len(prices)

    def export_tomorrow_min(self) -> float | None:
        if not self._tomorrow_slots:
            return None
        return min(self._export_prices_for_slots(self._tomorrow_slots))

    def export_tomorrow_max(self) -> float | None:
        if not self._tomorrow_slots:
            return None
        return max(self._export_prices_for_slots(self._tomorrow_slots))

    def import_export_spread_now(self) -> float | None:
        """Difference between total import price and export price now."""
        total = self.total_price_now()
        export = self.export_price_now()
        if total is None or export is None:
            return None
        return total - export

    def self_consumption_value_now(self) -> float | None:
        """Value of each kWh consumed from own generation (= avoided import cost)."""
        return self.total_price_now()

    def current_rolling_avg(self, minutes: int) -> float | None:
        """Average total price for the current slot and the next `minutes` minutes forward."""
        now = self._now_local()
        current = self.current_slot()
        if current is None:
            return None
        slot_start = dt_util.as_local(current.dt_utc)
        slots = self.slots_in_range(slot_start, now + timedelta(minutes=minutes))
        if not slots:
            return None
        prices = self._total_prices_for_slots(slots)
        return sum(prices) / len(prices)

    def next_solar_window_avg(self) -> float | None:
        """Average export price for the next upcoming solar production window."""
        now = self._now_local()
        start_h = self._solar_window_start
        end_h = self._solar_window_end

        # Try today's window first if it hasn't ended yet
        today_end = now.replace(hour=end_h, minute=0, second=0, microsecond=0)
        if now < today_end:
            today_start = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
            window_start = today_start if now < today_start else now
            slots = self.slots_in_range(window_start, today_end)
            if slots:
                prices = self._export_prices_for_slots(slots)
                return sum(prices) / len(prices)

        # Fall through to tomorrow's window
        if not self._tomorrow_slots:
            return None
        tomorrow = now.date() + timedelta(days=1)
        tmrw_start = now.replace(
            year=tomorrow.year,
            month=tomorrow.month,
            day=tomorrow.day,
            hour=start_h,
            minute=0,
            second=0,
            microsecond=0,
        )
        tmrw_end = tmrw_start.replace(hour=end_h)
        slots = self.slots_in_range(tmrw_start, tmrw_end)
        if not slots:
            return None
        prices = self._export_prices_for_slots(slots)
        return sum(prices) / len(prices)

    # ------------------------------------------------------------------
    # E2 — Battery optimization methods
    # ------------------------------------------------------------------

    def arbitrage_spread_today(self) -> float | None:
        """Price spread between cheapest and most expensive total price slot today."""
        max_p = self.today_total_max()
        min_p = self.today_total_min()
        if max_p is None or min_p is None:
            return None
        return max_p - min_p

    def charge_opportunity_factor(self) -> float | None:
        """Normalized 0–1 indicator of how good now is for grid charging.

        1.0 = current slot is the cheapest today (best time to charge).
        0.0 = current slot is the most expensive today (worst time to charge).
        """
        total = self.total_price_now()
        min_p = self.today_total_min()
        max_p = self.today_total_max()
        if total is None or min_p is None or max_p is None:
            return None
        spread = max_p - min_p
        if spread == 0.0:
            return 0.5
        return round(1.0 - (total - min_p) / spread, 4)

    def optimal_charge_window(self) -> tuple[datetime, datetime] | None:
        """Start and end of the cheapest window for a full battery charge cycle.

        Returns None if battery is not configured or no price data is available.
        """
        if self._battery_capacity_kwh <= 0 or self._battery_charge_power_kw <= 0:
            return None
        all_slots = self._today_slots + (self._tomorrow_slots or [])
        if not all_slots:
            return None

        charge_hours = self._battery_capacity_kwh / self._battery_charge_power_kw
        resolution_minutes = self._resolution.value
        slots_needed = max(1, round(charge_hours * 60 / resolution_minutes))

        if slots_needed > len(all_slots):
            return None

        # Find the window with the lowest average total price
        best_start = 0
        best_avg = float("inf")
        for i in range(len(all_slots) - slots_needed + 1):
            window = all_slots[i : i + slots_needed]
            prices = self._total_prices_for_slots(window)
            avg = sum(prices) / len(prices)
            if avg < best_avg:
                best_avg = avg
                best_start = i

        window = all_slots[best_start : best_start + slots_needed]
        start_dt = dt_util.as_local(window[0].dt_utc)
        end_slot_start = dt_util.as_local(window[-1].dt_utc)
        end_dt = end_slot_start + timedelta(minutes=resolution_minutes)
        return start_dt, end_dt

    def battery_charge_recommendation(self) -> str | None:
        """String recommendation for battery action based on current total price rank.

        Returns None when battery is not configured.
        Recommendation is based solely on price position; does not account for SoC.
        """
        if self._battery_capacity_kwh <= 0:
            return None
        if not self._today_slots:
            return None

        total = self.total_price_now()
        min_p = self.today_total_min()
        max_p = self.today_total_max()
        if total is None or min_p is None or max_p is None:
            return None

        spread = max_p - min_p
        if spread == 0.0:
            return "hold"

        position = (total - min_p) / spread  # 0 = cheapest, 1 = most expensive
        if position <= 0.25:
            return "charge_from_grid"
        if position >= 0.75:
            return "discharge"
        return "hold"

    def charge_from_grid_recommended(self) -> bool | None:
        """True if current slot is in the cheapest quartile and more expensive slots follow."""
        if self._battery_capacity_kwh <= 0:
            return None
        if not self._today_slots:
            return None

        recommendation = self.battery_charge_recommendation()
        if recommendation != "charge_from_grid":
            return False

        # Also check that at least one more expensive slot exists later today
        now = self._now_local()
        total = self.total_price_now()
        if total is None:
            return None
        future_slots = [s for s in self._today_slots if dt_util.as_local(s.dt_utc) > now]
        future_prices = self._total_prices_for_slots(future_slots)
        return any(p > total for p in future_prices)

    def discharge_to_grid_recommended(self) -> bool | None:
        """True if current export price is in the top quartile of today's export prices."""
        if self._battery_capacity_kwh <= 0:
            return None
        if not self._today_slots:
            return None

        export_now = self.export_price_now()
        if export_now is None:
            return None
        export_prices = self._export_prices_for_slots(self._today_slots)
        if not export_prices:
            return None
        sorted_prices = sorted(export_prices)
        top_quartile_threshold = sorted_prices[int(len(sorted_prices) * 0.75)]
        return export_now >= top_quartile_threshold

    # ------------------------------------------------------------------
    # E3 — Fixed cost methods
    # ------------------------------------------------------------------

    def monthly_fixed_cost_today(self) -> float | None:
        """Today's share of monthly fixed costs: monthly_cost / days_in_month (€/day)."""
        if self._monthly_fixed_cost == 0.0:
            return None
        now = self._now_local()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        return round(self._monthly_fixed_cost / days_in_month, 4)

    # ------------------------------------------------------------------
    # Control factor
    # ------------------------------------------------------------------

    def control_factor(self) -> float | None:
        rank = self.current_rank()
        if rank is None:
            return None
        return calc.control_factor(
            rank,
            self._resolution.slots_per_day,
            self._control_factor_function,
            self._control_factor_scaling,
        )

    def control_factor_bipolar(self) -> float | None:
        cf = self.control_factor()
        if cf is None:
            return None
        return calc.control_factor_bipolar(cf)

    # ------------------------------------------------------------------
    # Price arrays (for optional attribute exposure)
    # ------------------------------------------------------------------

    def today_price_array(self) -> list[dict] | None:
        if not self._expose_price_arrays:
            return None
        return [
            {
                "time": dt_util.as_local(s.dt_utc).isoformat(),
                "price": self.format_price(self._spot_effective(s)),
                "rank": s.rank,
            }
            for s in self._today_slots
        ]

    def tomorrow_price_array(self) -> list[dict] | None:
        if not self._expose_price_arrays or not self._tomorrow_slots:
            return None
        return [
            {
                "time": dt_util.as_local(s.dt_utc).isoformat(),
                "price": self.format_price(self._spot_effective(s)),
                "rank": s.rank,
            }
            for s in self._tomorrow_slots
        ]

    # ------------------------------------------------------------------
    # Optimization scores
    # ------------------------------------------------------------------

    async def _async_setup_score_tracking(self) -> None:
        """Subscribe to meter state changes for score accumulation."""
        all_meters: set[str] = set()
        for profile in self.score_profiles:
            all_meters.update(profile.meters)

        if not all_meters:
            return

        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass,
                list(all_meters),
                self._on_meter_state_change,
            )
        )

    @callback
    def _on_meter_state_change(self, event: Any) -> None:
        """Handle meter entity state change for score accumulation."""
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if old_state is None or new_state is None:
            return

        try:
            new_val = float(new_state.state)
        except (ValueError, TypeError):
            return

        # Use persisted last value to survive restarts correctly
        old_val = self._last_meter_values.get(entity_id)
        if old_val is None:
            try:
                old_val = float(old_state.state)
            except (ValueError, TypeError):
                old_val = new_val

        kwh_delta = new_val - old_val
        self._last_meter_values[entity_id] = new_val

        if kwh_delta <= 0:
            return  # Ignore resets or unchanged

        rank = self.current_rank()
        if rank is None:
            return

        bucket = calc.rank_to_bucket(rank, self._resolution.slots_per_day)
        for profile in self.score_profiles:
            if entity_id in profile.meters:
                self._score_data.setdefault(profile.id, {})
                self._score_data[profile.id][bucket] = (
                    self._score_data[profile.id].get(bucket, 0.0) + kwh_delta
                )

        # Debounced persist
        self._schedule_score_persist()

    def _schedule_score_persist(self) -> None:
        if self._score_persist_unsub is not None:
            return  # Already scheduled
        self._score_persist_unsub = async_call_later(
            self.hass,
            _SCORE_PERSIST_DEBOUNCE,
            self._persist_scores,
        )

    @callback
    def _persist_scores(self, _now: datetime) -> None:
        self._score_persist_unsub = None
        self.hass.async_create_task(self._async_persist_scores())

    async def _async_persist_scores(self) -> None:
        await self._storage.async_save_score_data(
            self._score_data,
            self._daily_history,
            self._last_meter_values,
            self._month_scores,
        )

    async def _async_finalise_daily_scores(self) -> None:
        """At midnight: save today's scores to history, reset accumulators."""
        now_local = self._now_local()
        yesterday = (now_local - timedelta(days=1)).date()
        day_scores: dict[str, float] = {}
        for profile in self.score_profiles:
            bucket_data = self._score_data.get(profile.id, {})
            day_scores[profile.id] = calc.compute_score(bucket_data, profile.formula)

        self._daily_history.append({"date": str(yesterday), "scores": day_scores})

        # Keep only 90 days of history
        self._daily_history = self._daily_history[-90:]

        # If yesterday was the last day of its month, finalise that month's score
        if yesterday.month != now_local.month:
            month_key = f"{yesterday.year}-{yesterday.month:02d}"
            month_day_scores: dict[str, list[float]] = {}
            for entry in self._daily_history:
                if entry["date"].startswith(month_key):
                    for pid, score in entry.get("scores", {}).items():
                        month_day_scores.setdefault(pid, []).append(score)
            if month_day_scores:
                finalised: dict[str, float] = {
                    pid: sum(scores) / len(scores) for pid, scores in month_day_scores.items()
                }
                self._month_scores.append({"month": month_key, "scores": finalised})
                # Keep only last two completed months
                self._month_scores = self._month_scores[-2:]

        # Reset accumulators
        self._score_data = {}

        await self._async_persist_scores()

    def get_daily_score(self, profile_id: str) -> float:
        """Return the in-progress daily optimization score (0–100)."""
        bucket_data = self._score_data.get(profile_id, {})
        profile = next((p for p in self.score_profiles if p.id == profile_id), None)
        formula = profile.formula if profile else "default"
        return calc.compute_score(bucket_data, formula)

    def get_previous_daily_score(self, profile_id: str) -> float | None:
        """Return yesterday's completed daily score, or None if unavailable."""
        yesterday = (self._now_local() - timedelta(days=1)).date()
        yesterday_str = str(yesterday)
        for entry in reversed(self._daily_history):
            if entry["date"] == yesterday_str:
                return entry.get("scores", {}).get(profile_id)
        return None

    def get_monthly_score(self, profile_id: str) -> float:
        """Return average of completed daily scores for the current calendar month.

        Returns 0.0 when no completed days exist yet (consistent with get_daily_score).
        """
        now_local = self._now_local()
        month_key = f"{now_local.year}-{now_local.month:02d}"

        scores = [
            entry["scores"][profile_id]
            for entry in self._daily_history
            if entry["date"].startswith(month_key) and profile_id in entry.get("scores", {})
        ]
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def get_previous_monthly_score(self, profile_id: str) -> float | None:
        """Return the finalised score for the previous calendar month, or None."""
        now_local = self._now_local()
        # Previous month key
        if now_local.month == 1:
            prev_key = f"{now_local.year - 1}-12"
        else:
            prev_key = f"{now_local.year}-{now_local.month - 1:02d}"
        for entry in reversed(self._month_scores):
            if entry["month"] == prev_key:
                return entry.get("scores", {}).get(profile_id)
        return None
