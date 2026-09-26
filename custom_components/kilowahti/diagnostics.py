"""Diagnostics support for the Kilowahti integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import KilowahtiCoordinator

TO_REDACT = {"name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: KilowahtiCoordinator = hass.data[DOMAIN][entry.entry_id]

    return {
        "entry": {
            "title": "**REDACTED**",
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "price_source_name": coordinator.price_source_name,
            "last_failover_utc": coordinator.last_failover_utc,
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "today_slots": len(coordinator._today_slots),
            "tomorrow_slots": len(coordinator._tomorrow_slots or []),
            "currency": coordinator.currency,
            "currency_mode_is_local": coordinator.currency_mode_is_local,
            "fx_mode": coordinator.fx_mode,
            "generation_enabled": coordinator.generation_enabled,
            "battery_sensors_enabled": coordinator.battery_sensors_enabled,
            "show_rolling_averages": coordinator.show_rolling_averages,
            "active_transfer_group_label": coordinator.active_transfer_group_label,
            "active_transfer_tier_label": coordinator.active_transfer_tier_label,
            "fixed_period_active_now": coordinator.fixed_period_active_now,
            "score_profile_count": len(coordinator.score_profiles),
        },
    }
