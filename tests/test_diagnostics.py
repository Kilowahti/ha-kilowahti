"""Tests for Kilowahti diagnostics."""

from __future__ import annotations

from custom_components.kilowahti.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_name_and_reports_coordinator_state(hass, setup_integration):
    """Diagnostics redact the entry name and expose coordinator state."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diagnostics["entry"]["title"] == "**REDACTED**"
    assert diagnostics["entry"]["options"]["name"] == "**REDACTED**"
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["today_slots"] > 0
