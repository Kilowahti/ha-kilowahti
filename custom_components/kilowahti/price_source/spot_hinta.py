"""spot-hinta.fi price source for the Kilowahti integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import API_BASE_URL, API_ENDPOINT_TODAY, API_ENDPOINT_TOMORROW
from ..models import PriceResolution, PriceSlot
from . import PriceSource

_LOGGER = logging.getLogger(__name__)

# spot-hinta.fi returns prices in €/kWh; we store internally as c/kWh.
_EUR_TO_SNT = 100.0


class SpotHintaRateLimitError(aiohttp.ClientResponseError):
    """Raised when spot-hinta.fi returns HTTP 429, carrying the Retry-After delay."""

    def __init__(self, request_info, history, retry_after: int) -> None:
        super().__init__(request_info, history, status=429)
        self.retry_after = retry_after


class SpotHintaSource(PriceSource):
    """Fetch prices from the spot-hinta.fi REST API."""

    async def fetch_today(
        self,
        hass: HomeAssistant,
        region: str,
        resolution: PriceResolution,
    ) -> list[PriceSlot]:
        return await self._fetch(hass, API_ENDPOINT_TODAY, region, resolution)

    async def fetch_tomorrow(
        self,
        hass: HomeAssistant,
        region: str,
        resolution: PriceResolution,
    ) -> list[PriceSlot] | None:
        """Return slots, or None on 404 (tomorrow not yet published)."""
        try:
            return await self._fetch(hass, API_ENDPOINT_TOMORROW, region, resolution)
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch(
        self,
        hass: HomeAssistant,
        endpoint: str,
        region: str,
        resolution: PriceResolution,
    ) -> list[PriceSlot]:
        session = async_get_clientsession(hass)
        url = f"{API_BASE_URL}{endpoint}"
        params = {
            "region": region,
            "priceResolution": resolution.value,
        }

        _LOGGER.debug("Fetching %s (region=%s, resolution=%s)", endpoint, region, resolution.value)

        async with session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                _LOGGER.warning("Rate-limited by spot-hinta.fi; Retry-After=%ds", retry_after)
                raise SpotHintaRateLimitError(
                    response.request_info,
                    response.history,
                    retry_after=retry_after,
                )

            response.raise_for_status()
            data: list[dict] = await response.json()

        return self._parse(data)

    @staticmethod
    def _parse(data: list[dict]) -> list[PriceSlot]:
        slots: list[PriceSlot] = []
        for item in data:
            dt = datetime.fromisoformat(item["DateTime"]).astimezone(timezone.utc)
            slots.append(
                PriceSlot(
                    dt_utc=dt,
                    price_no_tax=item["PriceNoTax"] * _EUR_TO_SNT,
                    rank=item["Rank"],
                )
            )
        slots.sort(key=lambda s: s.dt_utc)
        return slots
