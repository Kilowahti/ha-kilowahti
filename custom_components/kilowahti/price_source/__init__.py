"""Abstract base class for Kilowahti price sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant.core import HomeAssistant

from ..models import PriceResolution, PriceSlot


class PriceSource(ABC):
    @abstractmethod
    async def fetch_today(
        self,
        hass: HomeAssistant,
        region: str,
        resolution: PriceResolution,
    ) -> list[PriceSlot]:
        """Return today's price slots, sorted by dt_utc."""

    @abstractmethod
    async def fetch_tomorrow(
        self,
        hass: HomeAssistant,
        region: str,
        resolution: PriceResolution,
    ) -> list[PriceSlot] | None:
        """Return tomorrow's price slots, or None if not yet available."""
