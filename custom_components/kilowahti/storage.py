"""Storage helpers for the Kilowahti integration."""

from __future__ import annotations

import logging
from datetime import date

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION
from .models import FixedPeriod, PriceSlot

_LOGGER = logging.getLogger(__name__)


class KilowahtiStorage:
    """Wraps HA Store for fixed-price periods, price cache, and score accumulators."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store_periods: Store = Store(hass, STORAGE_VERSION, f"kilowahti_{entry_id}_periods")
        self._store_cache: Store = Store(hass, STORAGE_VERSION, f"kilowahti_{entry_id}_cache")
        self._store_scores: Store = Store(hass, STORAGE_VERSION, f"kilowahti_{entry_id}_scores")

        self._periods: list[FixedPeriod] = []
        self._cache: dict = {}
        self._scores: dict = {}

    # ------------------------------------------------------------------
    # Load all stores on startup
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store_periods.async_load()
        if data:
            self._periods = [FixedPeriod.from_dict(p) for p in data.get("periods", [])]

        data = await self._store_cache.async_load()
        if data:
            self._cache = data

        data = await self._store_scores.async_load()
        if data:
            self._scores = data

    # ------------------------------------------------------------------
    # Fixed-price periods
    # ------------------------------------------------------------------

    @property
    def periods(self) -> list[FixedPeriod]:
        return list(self._periods)

    def get_period(self, period_id: str) -> FixedPeriod | None:
        return next((p for p in self._periods if p.id == period_id), None)

    async def async_add_period(self, period: FixedPeriod) -> None:
        self._periods.append(period)
        await self._save_periods()

    async def async_remove_period(self, period_id: str) -> bool:
        before = len(self._periods)
        self._periods = [p for p in self._periods if p.id != period_id]
        if len(self._periods) < before:
            await self._save_periods()
            return True
        return False

    async def _save_periods(self) -> None:
        await self._store_periods.async_save({"periods": [p.to_dict() for p in self._periods]})

    # ------------------------------------------------------------------
    # Price cache (today + tomorrow slots, keyed by date string)
    # ------------------------------------------------------------------

    def get_cache(self) -> tuple[list[dict] | None, list[dict] | None, str | None]:
        """Return (today_slots_raw, tomorrow_slots_raw, cache_date_str)."""
        return (
            self._cache.get("today"),
            self._cache.get("tomorrow"),
            self._cache.get("date"),
        )

    def is_cache_valid_for(self, today: date) -> bool:
        return self._cache.get("date") == str(today) and bool(self._cache.get("today"))

    async def async_save_cache(
        self,
        today_slots: list[PriceSlot],
        tomorrow_slots: list[PriceSlot] | None,
        cache_date: date,
    ) -> None:
        self._cache = {
            "date": str(cache_date),
            "today": [s.to_dict() for s in today_slots],
            "tomorrow": [s.to_dict() for s in tomorrow_slots] if tomorrow_slots else None,
        }
        await self._store_cache.async_save(self._cache)

    # ------------------------------------------------------------------
    # Score accumulators
    # ------------------------------------------------------------------

    def get_score_data(self) -> dict:
        """Return the persisted daily-score accumulator dict."""
        return dict(self._scores.get("today_accumulators", {}))

    def get_daily_history(self) -> list[dict]:
        """Return list of completed-day score dicts."""
        return list(self._scores.get("daily_history", []))

    def get_month_scores(self) -> list[dict]:
        """Return list of last two completed calendar-month scores."""
        return list(self._scores.get("month_scores", []))

    async def async_save_score_data(
        self,
        today_accumulators: dict,
        daily_history: list[dict],
        last_meter_values: dict,
        month_scores: list[dict],
    ) -> None:
        self._scores = {
            "today_accumulators": today_accumulators,
            "daily_history": daily_history,
            "last_meter_values": last_meter_values,
            "month_scores": month_scores,
        }
        await self._store_scores.async_save(self._scores)

    def get_last_meter_values(self) -> dict:
        return dict(self._scores.get("last_meter_values", {}))
