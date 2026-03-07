"""Binary sensor platform for the Kilowahti integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BINARY_SENSOR_FIXED_PERIOD_ACTIVE,
    BINARY_SENSOR_PRICE_ACCEPTABLE,
    BINARY_SENSOR_PRICE_OR_RANK_ACCEPTABLE,
    BINARY_SENSOR_RANK_ACCEPTABLE,
    BINARY_SENSOR_TOMORROW_AVAILABLE,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
    DOMAIN,
)
from .coordinator import KilowahtiCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: KilowahtiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            KilowahtiBinarySensor(coordinator, entry, BINARY_SENSOR_PRICE_ACCEPTABLE),
            KilowahtiBinarySensor(coordinator, entry, BINARY_SENSOR_RANK_ACCEPTABLE),
            KilowahtiBinarySensor(coordinator, entry, BINARY_SENSOR_PRICE_OR_RANK_ACCEPTABLE),
            KilowahtiBinarySensor(coordinator, entry, BINARY_SENSOR_FIXED_PERIOD_ACTIVE),
            KilowahtiBinarySensor(coordinator, entry, BINARY_SENSOR_TOMORROW_AVAILABLE),
        ]
    )


# ---------------------------------------------------------------------------
# Binary sensor entity
# ---------------------------------------------------------------------------


class KilowahtiBinarySensor(CoordinatorEntity[KilowahtiCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KilowahtiCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        c = self.coordinator
        key = self._key

        if key == BINARY_SENSOR_PRICE_ACCEPTABLE:
            price = c._price_for_comparison()
            if price is None:
                return None
            return price <= c._max_price

        if key == BINARY_SENSOR_RANK_ACCEPTABLE:
            rank = c.current_rank()
            if rank is None:
                return None
            return rank <= c._max_rank

        if key == BINARY_SENSOR_PRICE_OR_RANK_ACCEPTABLE:
            price = c._price_for_comparison()
            rank = c.current_rank()
            price_ok = None if price is None else price <= c._max_price
            rank_ok = None if rank is None else rank <= c._max_rank
            if price_ok is None and rank_ok is None:
                return None
            return bool(price_ok) or bool(rank_ok)

        if key == BINARY_SENSOR_FIXED_PERIOD_ACTIVE:
            return c.fixed_period_active_now() is not None

        if key == BINARY_SENSOR_TOMORROW_AVAILABLE:
            return c.tomorrow_slots() is not None

        return None
