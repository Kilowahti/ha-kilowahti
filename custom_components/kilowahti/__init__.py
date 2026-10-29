"""Kilowahti — Finnish/Nordic electricity spot price integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from ._migrate import async_migrate_unique_ids
from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_CURRENCY_MODE,
    CONF_EAGER_END_HOUR,
    CONF_EAGER_START_HOUR,
    CONF_GENERATION_ENABLED,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_PRICE_RESOLUTION,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_SHOW_ROLLING_AVERAGES,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
)
from .coordinator import KilowahtiCoordinator
from .repairs import async_check_stable_entity_ids
from .services import async_register_services
from .storage import KilowahtiStorage

_LOGGER = logging.getLogger(__name__)

type KilowahtiConfigEntry = ConfigEntry[KilowahtiCoordinator]

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]

# Options that require a full integration reload when changed (entity count or timer setup changes).
# All other options are read dynamically from entry.options and only need async_update_listeners().
_RELOAD_REQUIRED_KEYS = frozenset(
    {
        CONF_BATTERY_CAPACITY_KWH,  # gates battery sensors (zero vs non-zero)
        CONF_CURRENCY_MODE,  # unit strings change on every entity; gates exchange_rate sensor
        CONF_EAGER_END_HOUR,
        CONF_EAGER_START_HOUR,
        CONF_GENERATION_ENABLED,  # gates E1-E4 sensors
        CONF_PRICE_RESOLUTION,
        CONF_REGION,
        CONF_SCORE_PROFILES,  # each profile adds entities
        CONF_SHOW_ROLLING_AVERAGES,  # gates rolling avg sensors
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once, before any config entry is set up."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KilowahtiConfigEntry) -> bool:
    """Set up Kilowahti from a config entry."""
    async_migrate_unique_ids(hass, entry)
    async_check_stable_entity_ids(hass, entry)

    storage = KilowahtiStorage(hass, entry.entry_id)
    await storage.async_load()

    coordinator = KilowahtiCoordinator(hass, entry, storage)

    # Fetch / restore initial data. Raises ConfigEntryNotReady on failure.
    await coordinator.async_config_entry_first_refresh()

    # Set up time-based tracking (slot updates, midnight rollover, eager fetch).
    await coordinator.async_setup_timers()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload on options change
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: KilowahtiConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry.runtime_data.async_unload()

    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: KilowahtiConfigEntry) -> None:
    """Reload the entry when options change.

    Only reloads for structural changes (entity count, timer setup). For all other
    option changes (thresholds, VAT, commission, etc.) we sync instance vars and
    notify listeners in-place, avoiding a disruptive teardown/setup cycle.
    """
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        old = coordinator._last_known_options
        new = entry.options
        if not any(old.get(k) != new.get(k) for k in _RELOAD_REQUIRED_KEYS):
            coordinator._last_known_options = dict(new)
            # Sync threshold instance vars in case the options flow changed them
            coordinator._max_price_value = new.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE)
            coordinator._max_rank_value = new.get(CONF_MAX_RANK, DEFAULT_MAX_RANK)
            coordinator.async_update_listeners()
            return
    await hass.config_entries.async_reload(entry.entry_id)
