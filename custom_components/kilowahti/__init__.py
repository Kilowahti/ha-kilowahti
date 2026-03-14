"""Kilowahti — Finnish/Nordic electricity spot price integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
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
    DOMAIN,
)
from .coordinator import KilowahtiCoordinator
from .services import async_register_services, async_unregister_services
from .storage import KilowahtiStorage

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SENSOR]

# Options that require a full integration reload when changed (entity count or timer setup changes).
# All other options are read dynamically from entry.options and only need async_update_listeners().
_RELOAD_REQUIRED_KEYS = frozenset(
    {
        CONF_EAGER_END_HOUR,
        CONF_EAGER_START_HOUR,
        CONF_GENERATION_ENABLED,  # gates E1-E4 sensors
        CONF_PRICE_RESOLUTION,
        CONF_REGION,
        CONF_SCORE_PROFILES,  # each profile adds entities
        CONF_SHOW_ROLLING_AVERAGES,  # gates rolling avg sensors
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kilowahti from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    storage = KilowahtiStorage(hass, entry.entry_id)
    await storage.async_load()

    coordinator = KilowahtiCoordinator(hass, entry, storage)

    # Fetch / restore initial data. Raises ConfigEntryNotReady on failure.
    await coordinator.async_config_entry_first_refresh()

    # Set up time-based tracking (slot updates, midnight rollover, eager fetch).
    await coordinator.async_setup_timers()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload on options change
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    # Register services once (idempotent)
    async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: KilowahtiCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_unload()

        # Unregister services only when no entries remain
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)

    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change.

    Only reloads for structural changes (entity count, timer setup). For all other
    option changes (thresholds, VAT, commission, etc.) we sync instance vars and
    notify listeners in-place, avoiding a disruptive teardown/setup cycle.
    """
    coordinator: KilowahtiCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
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
