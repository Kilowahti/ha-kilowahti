"""Coordinator for the Kilowahti integration."""

from __future__ import annotations

import calendar
import functools
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from kilowahti.sources.ecb import fetch_ecb_rate
from kilowahti.sources.kilowahti_cdn import KilowahtiCdnSource, KilowahtiCdnZoneNotFoundError
from kilowahti.sources.spot_hinta import SpotHintaRateLimitError, SpotHintaSource

from .const import (
    API_REGIONS,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_POWER_KW,
    CONF_CONTROL_FACTOR_FUNCTION,
    CONF_CONTROL_FACTOR_SCALING,
    CONF_CURRENCY_MODE,
    CONF_DISPLAY_UNIT,
    CONF_EAGER_END_HOUR,
    CONF_EAGER_START_HOUR,
    CONF_ELECTRICITY_TAX,
    CONF_EXPORT_COMMISSION,
    CONF_EXPORT_PRICE_THRESHOLD,
    CONF_EXPORT_PRICING_MODE,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_EXPOSE_TOTAL_PRICE_ARRAYS,
    CONF_FIXED_EXPORT_RATE,
    CONF_FORWARD_AVG_HOURS,
    CONF_FX_MODE,
    CONF_FX_RATE,
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
    CURRENCY_FOR_REGION,
    CURRENCY_MODE_EUR,
    CURRENCY_MODE_LOCAL,
    CURRENCY_UNITS,
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
    DEFAULT_EXPOSE_TOTAL_PRICE_ARRAYS,
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
    ECB_CURRENCIES,
    EXPORT_PRICING_FIXED,
    FX_MODE_AUTO,
    FX_MODE_MANUAL,
    PRICE_SOURCE_KILOWAHTI_CDN,
    PRICE_SOURCE_SPOT_HINTA,
    UNIT_EUROKWH,
    UNIT_SNTPERKWH,
)
from .models import FixedPeriod, PriceResolution, PriceSlot, ScoreProfile, TransferGroup
from .storage import KilowahtiStorage

_LOGGER = logging.getLogger(__name__)

# Debounce interval for persisting score accumulators
_SCORE_PERSIST_DEBOUNCE = 60  # seconds

# Day-ahead exchange publication times are quoted in CET/CEST regardless of the
# HA instance's or the price region's own timezone — anchor the eager-poll
# window to it rather than local time.
_EAGER_POLL_TZ = ZoneInfo("Europe/Berlin")

# Nord Pool publishes ~12:45 CET. If the primary source still returns no
# tomorrow data (without erroring) past this CET hour, the eager poll starts
# trying fallback sources; before it, a silent None is normal pre-publication.
_FALLBACK_DEADLINE_HOUR = 15

# Placeholder daily score per quartile when no consumption is recorded yet —
# the literal midpoint of each quartile's score range (Q1: 75–100, Q4: 0–25).
_QUARTILE_PLACEHOLDER_SCORES = {1: 87.5, 2: 62.5, 3: 37.5, 4: 12.5}


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
        # Automatic source chain: CDN primary, spot-hinta.fi fallback only
        # where it has coverage. The euenergy layer slots in between once
        # token distribution is decided (price-source-chain-plan.md).
        self._sources: list[tuple[str, KilowahtiCdnSource | SpotHintaSource]] = [
            (PRICE_SOURCE_KILOWAHTI_CDN, KilowahtiCdnSource(now_fn=dt_util.utcnow))
        ]
        if self._region in API_REGIONS:
            self._sources.append((PRICE_SOURCE_SPOT_HINTA, SpotHintaSource()))
        self._active_source_name: str = self._sources[0][0]
        self._last_failover_utc: datetime | None = None

        # FX state for local-currency display. The active rate is frozen per
        # local day; the daily ECB fetch stages the next day's rate, which is
        # promoted at midnight rollover.
        self._fx_active_rate: float | None = None
        self._fx_active_date: str | None = None
        self._fx_staged_rate: float | None = None
        self._fx_staged_date: str | None = None

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
        self._eager_start_timer_unsub: Callable | None = None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @property
    def entry_id(self) -> str:
        return self._entry.entry_id

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
    def _expose_total_price_arrays(self) -> bool:
        return self._opts.get(CONF_EXPOSE_TOTAL_PRICE_ARRAYS, DEFAULT_EXPOSE_TOTAL_PRICE_ARRAYS)

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
            # Cache stale or missing — fetch from the source chain
            self._today_slots = await self._chain_fetch_today()
            self._today_date = today
            self._tomorrow_slots = None
            await self._storage.async_save_cache(self._today_slots, None, today)
            _LOGGER.info("Fetched %d today-slots from API", len(self._today_slots))

        # Restore score accumulators
        self._score_data = self._storage.get_score_data()
        self._daily_history = self._storage.get_daily_history()
        self._month_scores = self._storage.get_month_scores()
        self._last_meter_values = self._storage.get_last_meter_values()

        await self._async_init_fx()

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

        self._unsubscribers.append(
            # Midnight rollover
            async_track_time_change(
                self.hass,
                self._on_midnight,
                hour=0,
                minute=0,
                second=0,
            ),
        )

        # Start eager polling for tomorrow's prices, anchored to CET/CEST (see
        # _EAGER_POLL_TZ) rather than HA local time.
        self._schedule_eager_start_timer(eager_start)

        # Daily FX refresh after ECB publish (~16:00 CET); callback no-ops
        # unless local currency + auto mode are active, so fx_mode changes
        # need no reload.
        if self.currency in ECB_CURRENCIES:
            self._unsubscribers.append(
                async_track_time_change(
                    self.hass, self._on_fx_refresh, hour=17, minute=15, second=0
                )
            )

        await self._async_setup_score_tracking()

        # If we're already past eager_start and missing tomorrow, start polling now
        now_cet = self._now_cet()
        if self._tomorrow_slots is None and eager_start <= now_cet.hour < eager_end:
            self.hass.async_create_task(self._async_eager_poll())

    def async_unload(self) -> None:
        """Cancel all subscriptions and pending tasks."""
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()

        if self._eager_poll_unsub is not None:
            self._eager_poll_unsub()
            self._eager_poll_unsub = None

        if self._eager_start_timer_unsub is not None:
            self._eager_start_timer_unsub()
            self._eager_start_timer_unsub = None

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

    def _now_cet(self) -> datetime:
        return dt_util.utcnow().astimezone(_EAGER_POLL_TZ)

    def _schedule_eager_start_timer(self, eager_start: int) -> None:
        """Schedule the next CET/CEST eager-start trigger; reschedules itself daily."""
        now_cet = self._now_cet()
        target = now_cet.replace(hour=eager_start, minute=0, second=0, microsecond=0)
        if target <= now_cet:
            target += timedelta(days=1)
        delay = (target - now_cet).total_seconds()
        self._eager_start_timer_unsub = async_call_later(
            self.hass, delay, functools.partial(self._on_eager_start_timer, eager_start)
        )

    @callback
    def _on_eager_start_timer(self, eager_start: int, _now: datetime) -> None:
        """Fire at eager_start CET/CEST daily to start eager polling for tomorrow's prices."""
        self._eager_start_timer_unsub = None
        if self._tomorrow_slots is None:
            self.hass.async_create_task(self._async_eager_poll())
        self._schedule_eager_start_timer(eager_start)

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
                self._today_slots = await self._chain_fetch_today()
                self._today_date = today
                _LOGGER.info(
                    "Midnight rollover: fetched today from API (%d slots)", len(self._today_slots)
                )
            except Exception as err:
                _LOGGER.error("Midnight rollover: failed to fetch today's prices: %s", err)

        await self._storage.async_save_cache(self._today_slots, None, today)

        # New local day — the staged FX rate becomes active
        await self._async_promote_staged_fx()

        # Finalise yesterday's score and reset
        await self._async_finalise_daily_scores()

        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Source chain
    # ------------------------------------------------------------------

    def _mark_active_source(self, name: str) -> None:
        if name != self._active_source_name:
            self._last_failover_utc = dt_util.utcnow()
            _LOGGER.warning("Price data now served by %s (was %s)", name, self._active_source_name)
            self._active_source_name = name

    async def _chain_fetch_today(self) -> list[PriceSlot]:
        """Fetch today's prices from the first source in the chain that delivers."""
        session = async_get_clientsession(self.hass)
        last_err: Exception | None = None
        for name, source in self._sources:
            try:
                slots = await source.fetch_today(session, self._region, self._resolution)
            except KilowahtiCdnZoneNotFoundError as err:
                _LOGGER.warning(
                    "Price source %s: region %s not available; trying next source",
                    name,
                    self._region,
                )
                last_err = err
                continue
            except Exception as err:
                _LOGGER.warning("Price source %s failed for today's prices: %s", name, err)
                last_err = err
                continue
            self._mark_active_source(name)
            return slots
        raise UpdateFailed(f"All price sources failed for today's prices: {last_err}")

    # ------------------------------------------------------------------
    # FX rate lifecycle (auto mode)
    # ------------------------------------------------------------------

    async def _async_init_fx(self) -> None:
        """Restore persisted FX state; fetch immediately only on first start."""
        if self.currency == "EUR":
            return
        data = self._storage.get_fx()
        if data.get("currency") == self.currency:
            self._fx_active_rate = data.get("active_rate")
            self._fx_active_date = data.get("active_date")
            self._fx_staged_rate = data.get("staged_rate")
            self._fx_staged_date = data.get("staged_date")
        if (
            self._currency_mode == CURRENCY_MODE_LOCAL
            and self.fx_mode == FX_MODE_AUTO
            and self._fx_active_rate is None
            and self.currency in ECB_CURRENCIES
        ):
            await self._async_fetch_fx(stage_only=False)

    async def _async_fetch_fx(self, stage_only: bool = True) -> None:
        """Fetch the ECB reference rate. Staged rates apply at rollover so the
        active rate never changes mid-day; stage_only=False applies at once
        (first start without a persisted rate)."""
        try:
            result = await fetch_ecb_rate(async_get_clientsession(self.hass), self.currency)
        except Exception as err:
            _LOGGER.warning("ECB rate fetch failed for %s: %s", self.currency, err)
            return
        self._fx_staged_rate = result.rate
        self._fx_staged_date = str(result.rate_date)
        if not stage_only or self._fx_active_rate is None:
            self._fx_active_rate = result.rate
            self._fx_active_date = str(dt_util.as_local(dt_util.utcnow()).date())
            self.async_update_listeners()
        await self._async_save_fx()

    async def _async_save_fx(self) -> None:
        await self._storage.async_save_fx(
            {
                "currency": self.currency,
                "active_rate": self._fx_active_rate,
                "active_date": self._fx_active_date,
                "staged_rate": self._fx_staged_rate,
                "staged_date": self._fx_staged_date,
            }
        )

    async def _async_promote_staged_fx(self) -> None:
        """Apply the staged rate as the new day's active rate (midnight rollover)."""
        if self.currency == "EUR" or self._fx_staged_rate is None:
            return
        if self._fx_staged_rate != self._fx_active_rate:
            _LOGGER.info(
                "FX rate for %s updated at rollover: %s → %s per EUR",
                self.currency,
                self._fx_active_rate,
                self._fx_staged_rate,
            )
        self._fx_active_rate = self._fx_staged_rate
        self._fx_active_date = str(dt_util.as_local(dt_util.utcnow()).date())
        await self._async_save_fx()

    @callback
    def _on_fx_refresh(self, _now: datetime) -> None:
        """Daily post-ECB-publish fetch; stages the rate for the next day."""
        if self._currency_mode == CURRENCY_MODE_LOCAL and self.fx_mode == FX_MODE_AUTO:
            self.hass.async_create_task(self._async_fetch_fx())

    # ------------------------------------------------------------------
    # Eager fetch for tomorrow's prices
    # ------------------------------------------------------------------

    async def _async_eager_poll(self) -> None:
        """Single poll attempt for tomorrow's prices; reschedules if not yet available.

        The primary source returning None (not yet published) is normal
        before _FALLBACK_DEADLINE_HOUR CET and only reschedules; past the
        deadline the remaining sources are tried in chain order. Source
        errors always advance the chain. A tomorrow fetched from a fallback
        source is final for the day — polling stops once slots are stored.
        """
        if self._tomorrow_slots is not None:
            return

        eager_end = self._opts.get(CONF_EAGER_END_HOUR, DEFAULT_EAGER_END_HOUR)
        if self._now_cet().hour >= eager_end:
            _LOGGER.debug("Eager fetch: window closed at %d:00 CET/CEST", eager_end)
            return

        session = async_get_clientsession(self.hass)
        past_deadline = self._now_cet().hour >= _FALLBACK_DEADLINE_HOUR
        retry_delay: float = 60
        permanent_failures = 0
        slots: list[PriceSlot] | None = None
        served_by: str | None = None

        for index, (name, source) in enumerate(self._sources):
            is_primary = index == 0
            try:
                result = await source.fetch_tomorrow(session, self._region, self._resolution)
            except SpotHintaRateLimitError as err:
                _LOGGER.warning(
                    "Eager fetch: %s rate-limited; retrying in %ds", name, err.retry_after
                )
                retry_delay = max(retry_delay, err.retry_after)
                continue
            except KilowahtiCdnZoneNotFoundError:
                _LOGGER.warning(
                    "Eager fetch: region %s not available on %s; trying next source",
                    self._region,
                    name,
                )
                permanent_failures += 1
                continue
            except Exception as err:
                _LOGGER.warning("Eager fetch: %s error polling for tomorrow: %s", name, err)
                continue

            if result is None:
                if is_primary and not past_deadline:
                    _LOGGER.debug("Eager fetch: tomorrow not yet published; retrying in 60s")
                    self._schedule_eager_poll(60)
                    return
                _LOGGER.debug("Eager fetch: %s has no tomorrow data", name)
                continue

            slots = result
            served_by = name
            break

        if slots is None or served_by is None:
            if permanent_failures == len(self._sources):
                _LOGGER.error(
                    "Eager fetch: region %s has no data on any configured source; not retrying",
                    self._region,
                )
                return
            self._schedule_eager_poll(retry_delay)
            return

        self._mark_active_source(served_by)
        self._tomorrow_slots = slots
        today = self._today_date or dt_util.as_local(dt_util.utcnow()).date()
        await self._storage.async_save_cache(self._today_slots, self._tomorrow_slots, today)
        _LOGGER.info("Eager fetch: got %d tomorrow-slots from %s", len(slots), served_by)
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
        """Rank of the current slot by total price among today's slots.

        Uses fixed-period price when active, otherwise spot. Includes transfer.
        Normalized: 1 = cheapest, slots_per_day = most expensive.
        """
        return self._score_rank_now()

    def total_price_quartile(self) -> int | None:
        """Quartile (1–4) of the current slot by total price among today's slots."""
        rank = self.total_price_rank_now()
        if rank is None:
            return None
        return calc.price_quartile(rank, self._resolution.slots_per_day)

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
        """Apply FX and VAT to raw spot price, then add commission (gross).

        API always returns prices excl. VAT in EUR; user-entered values
        (commission, transfer, thresholds) are already in the local currency.
        """
        return calc.spot_effective(slot, self._vat_rate, self._spot_commission, rate=self.fx_rate)

    def _energy_price_for_slot(self, slot: PriceSlot) -> float:
        """Return effective energy price for a slot, respecting fixed-price periods."""
        slot_date = dt_util.as_local(slot.dt_utc).date()
        fixed = self.fixed_period_for_date(slot_date)
        return fixed.price if fixed is not None else self._spot_effective(slot)

    def _synthetic_slots_for_date(self, d: date) -> list[PriceSlot]:
        """Generate zero-price slots for a date (used when no spot data but fixed period is active).

        Arithmetic is done in UTC to correctly handle DST transitions (spring-forward produces
        23 slots, fall-back 25), matching the slot count the price API would return.
        """
        tz_local = dt_util.get_time_zone(self.hass.config.time_zone)
        start_local = datetime(d.year, d.month, d.day, 0, 0, tzinfo=tz_local)
        current_utc = start_local.astimezone(dt_util.UTC)
        slots = []
        while dt_util.as_local(current_utc).date() == d:
            slots.append(PriceSlot(dt_utc=current_utc, price_no_tax=0.0, rank=0))
            current_utc += timedelta(minutes=self._resolution)
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

    def active_transfer_tariff(self) -> str | None:
        """Group and tier the current transfer price comes from, e.g. "Group, Tier"."""
        group = self.active_transfer_group_label()
        if group is None:
            return None
        tier = self.active_transfer_tier_label()
        return f"{group}, {tier}" if tier else group

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

    # ------------------------------------------------------------------
    # Currency / FX
    # ------------------------------------------------------------------

    @property
    def currency(self) -> str:
        """Local currency of the configured region (EUR for euro zones)."""
        return CURRENCY_FOR_REGION.get(self._region, "EUR")

    @property
    def _currency_mode(self) -> str:
        if self.currency == "EUR":
            return CURRENCY_MODE_EUR
        return self._opts.get(CONF_CURRENCY_MODE, CURRENCY_MODE_EUR)

    @property
    def fx_mode(self) -> str:
        default = FX_MODE_AUTO if self.currency in ECB_CURRENCIES else FX_MODE_MANUAL
        return self._opts.get(CONF_FX_MODE, default)

    @property
    def fx_rate(self) -> float:
        """FX multiplier applied to EUR spot prices. 1.0 = EUR display.

        Fallback chain in auto mode: active (persisted) rate → manual rate
        option → 1.0, which falls the display back to EUR entirely.
        """
        if self._currency_mode != CURRENCY_MODE_LOCAL:
            return 1.0
        manual = float(self._opts.get(CONF_FX_RATE) or 0.0)
        if self.fx_mode == FX_MODE_MANUAL:
            return manual if manual > 0 else 1.0
        if self._fx_active_rate:
            return self._fx_active_rate
        return manual if manual > 0 else 1.0

    @property
    def local_display_active(self) -> bool:
        """True when prices are shown in the local currency (an FX rate applies)."""
        return self._currency_mode == CURRENCY_MODE_LOCAL and self.fx_rate != 1.0

    @property
    def currency_mode_is_local(self) -> bool:
        """True when the entry is configured for local-currency display."""
        return self._currency_mode == CURRENCY_MODE_LOCAL

    @property
    def fx_rate_date(self) -> str | None:
        """ECB rate date of the active rate (auto mode), None in manual mode."""
        if self.fx_mode == FX_MODE_MANUAL:
            return None
        return (
            self._fx_staged_date
            if self._fx_active_rate == self._fx_staged_rate
            else self._fx_active_date
        )

    @property
    def _unit_pair(self) -> tuple[str | None, str]:
        """(minor, major) unit labels for the active display currency."""
        if self.local_display_active:
            return CURRENCY_UNITS[self.currency]
        return (UNIT_SNTPERKWH, UNIT_EUROKWH)

    @property
    def display_in_major(self) -> bool:
        """True when values are displayed in the major unit (€/kr/zł/…)."""
        minor, _major = self._unit_pair
        return self._display_unit == UNIT_EUROKWH or minor is None

    def format_price(self, price_snt: float | None) -> float | None:
        """Convert internal minor-unit price to the display unit."""
        if price_snt is None:
            return None
        if self.display_in_major:
            return price_snt / 100.0
        return price_snt

    @property
    def display_decimals(self) -> int:
        """Decimal places for displayed prices, in the active display unit."""
        base = 5 if self._high_precision else 2
        return base + (2 if self.display_in_major else 0)

    def display_price(self, price_snt: float | None) -> float | None:
        """Convert an internal minor-unit price and round it for display."""
        converted = self.format_price(price_snt)
        if converted is None:
            return None
        return round(converted, self.display_decimals)

    @property
    def native_unit(self) -> str:
        minor, major = self._unit_pair
        return major if self.display_in_major else minor

    @property
    def currency_symbol(self) -> str:
        """Symbol of the active display currency, without the per-kWh part."""
        _minor, major = self._unit_pair
        return major.split("/")[0]

    @property
    def minor_unit(self) -> str:
        """Unit of the minor-scale values used by storage and service calls.

        Currencies whose minor unit is out of use have no label of their own,
        so those are described relative to the major unit.
        """
        minor, major = self._unit_pair
        return minor or f"1/100 {major}"

    @property
    def price_source_name(self) -> str:
        """Source currently serving the price data."""
        return self._active_source_name

    @property
    def last_failover_utc(self) -> datetime | None:
        """UTC time of the most recent change of serving source, if any."""
        return self._last_failover_utc

    # ------------------------------------------------------------------
    # Today / tomorrow statistics
    # ------------------------------------------------------------------

    def _effective_prices_for_slots(self, slots: list[PriceSlot]) -> list[float]:
        return calc.effective_prices(
            slots, self._vat_rate, self._spot_commission, rate=self.fx_rate
        )

    def _energy_prices_for_slots(self, slots: list[PriceSlot]) -> list[float]:
        return [self._energy_price_for_slot(s) for s in slots]

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

    def today_avg(self) -> float | None:
        if not self._today_slots:
            return None
        prices = self._energy_prices_for_slots(self._today_slots)
        return sum(prices) / len(prices)

    def today_min(self) -> float | None:
        if not self._today_slots:
            return None
        return min(self._energy_prices_for_slots(self._today_slots))

    def today_max(self) -> float | None:
        if not self._today_slots:
            return None
        return max(self._energy_prices_for_slots(self._today_slots))

    def _tomorrow_energy_slots(self) -> list[PriceSlot] | None:
        tomorrow = (self._now_local() + timedelta(days=1)).date()
        if self.fixed_period_for_date(tomorrow) is not None:
            return self._synthetic_slots_for_date(tomorrow)
        return self._tomorrow_slots or None

    def tomorrow_avg(self) -> float | None:
        slots = self._tomorrow_energy_slots()
        if not slots:
            return None
        prices = self._energy_prices_for_slots(slots)
        return sum(prices) / len(prices)

    def tomorrow_min(self) -> float | None:
        slots = self._tomorrow_energy_slots()
        if not slots:
            return None
        return min(self._energy_prices_for_slots(slots))

    def tomorrow_max(self) -> float | None:
        slots = self._tomorrow_energy_slots()
        if not slots:
            return None
        return max(self._energy_prices_for_slots(slots))

    def next_hours_avg(self) -> float | None:
        now = self._now_local()
        end = now + timedelta(hours=self._forward_avg_hours)
        slots = self.slots_in_range(now, end)
        if not slots:
            return None
        prices = self._energy_prices_for_slots(slots)
        return sum(prices) / len(prices)

    def spot_next_hours_avg(self) -> float | None:
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
        return max(0.0, slot.price_no_tax * self.fx_rate - self._export_commission)

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

    def _spot_price_array(self, slots: list[PriceSlot]) -> list[dict]:
        return [
            {
                "time": dt_util.as_local(s.dt_utc).isoformat(),
                "price": self.display_price(self._spot_effective(s)),
                "rank": s.rank,
            }
            for s in slots
        ]

    def _total_price_array(self, slots: list[PriceSlot]) -> list[dict]:
        """Build total-price entries with the energy/transfer breakdown.

        Ranks come from the same tier normalization as the total_price_rank
        sensor, computed among `slots` only, so each day ranks within itself.
        `price` is the sum of the rounded parts, keeping the breakdown exact.
        """
        totals = {
            s.dt_utc: self._energy_price_for_slot(s) + (self.transfer_price_for_slot(s) or 0.0)
            for s in slots
        }
        entries: list[dict] = []
        for slot in slots:
            energy = self.display_price(self._energy_price_for_slot(slot))
            transfer = self.display_price(self.transfer_price_for_slot(slot) or 0.0)
            entries.append(
                {
                    "time": dt_util.as_local(slot.dt_utc).isoformat(),
                    "energy": energy,
                    "transfer": transfer,
                    "price": round(energy + transfer, self.display_decimals),
                    "rank": calc.normalized_total_price_rank(
                        slot,
                        slots,
                        lambda s: totals[s.dt_utc],
                        self._resolution.slots_per_day,
                    ),
                }
            )
        return entries

    def today_price_array(self) -> list[dict] | None:
        if not self._expose_price_arrays:
            return None
        return self._spot_price_array(self._today_slots)

    def tomorrow_price_array(self) -> list[dict] | None:
        if not self._expose_price_arrays or not self._tomorrow_slots:
            return None
        return self._spot_price_array(self._tomorrow_slots)

    def today_total_price_array(self) -> list[dict] | None:
        if not self._expose_total_price_arrays:
            return None
        return self._total_price_array(self._today_slots)

    def tomorrow_total_price_array(self) -> list[dict] | None:
        if not self._expose_total_price_arrays or not self._tomorrow_slots:
            return None
        return self._total_price_array(self._tomorrow_slots)

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

    def _score_rank_now(self) -> int | None:
        """Rank of the current slot by true total price among today's slots.

        Uses _energy_price_for_slot (fixed-period aware) plus transfer price.
        Normalized: cheapest tier(s) = 1, most expensive = slots_per_day.
        """
        current = self.current_slot()
        if current is None:
            return None

        def _true_total(s: PriceSlot) -> float:
            return self._energy_price_for_slot(s) + (self.transfer_price_for_slot(s) or 0.0)

        return calc.normalized_total_price_rank(
            current, self._today_slots, _true_total, self._resolution.slots_per_day
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

        rank = self._score_rank_now()
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
            if bucket_data:
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

    def get_daily_score(self, profile_id: str) -> float | None:
        """Return the in-progress daily optimization score (0–100), or None if no data yet."""
        bucket_data = self._score_data.get(profile_id, {})
        if not bucket_data:
            quartile = self.total_price_quartile()
            if quartile is None:
                return None
            return _QUARTILE_PLACEHOLDER_SCORES[quartile]
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

    def get_monthly_score(self, profile_id: str) -> float | None:
        """Return average of completed daily scores for the current calendar month.

        Includes today's in-progress score so the value stays live mid-day. Falls back
        to the previous month's finalised score during the brief window after midnight
        on day 1 when neither completed days nor today's accumulator have data yet.
        """
        now_local = self._now_local()
        month_key = f"{now_local.year}-{now_local.month:02d}"

        scores = [
            entry["scores"][profile_id]
            for entry in self._daily_history
            if entry["date"].startswith(month_key) and profile_id in entry.get("scores", {})
        ]

        today_score = self.get_daily_score(profile_id)
        if today_score is not None:
            scores.append(today_score)

        if scores:
            return sum(scores) / len(scores)

        return self.get_previous_monthly_score(profile_id)

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
