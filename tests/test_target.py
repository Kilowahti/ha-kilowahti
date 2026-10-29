"""Tests for resolving trigger and condition targets to a Kilowahti entry."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti._target import resolve_coordinator, resolve_entry_id
from custom_components.kilowahti.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState


def _add_failed_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="broken home", options={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    return entry


async def test_sole_loaded_entry_resolves_without_target(hass, setup_integration):
    assert resolve_entry_id(hass, None) == setup_integration.entry_id
    assert resolve_coordinator(hass, None) is setup_integration.runtime_data


async def test_entry_that_failed_setup_is_ignored(hass, setup_integration):
    _add_failed_entry(hass)

    assert resolve_entry_id(hass, None) == setup_integration.entry_id
    assert resolve_coordinator(hass, None) is setup_integration.runtime_data


async def test_no_loaded_entry_resolves_to_none(hass):
    _add_failed_entry(hass)

    assert resolve_entry_id(hass, None) is None
    assert resolve_coordinator(hass, None) is None
