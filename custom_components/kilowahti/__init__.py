"""Kilowahti — Finnish/Nordic electricity spot price integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import KilowahtiCoordinator
from .services import async_register_services, async_unregister_services
from .storage import KilowahtiStorage

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


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
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
