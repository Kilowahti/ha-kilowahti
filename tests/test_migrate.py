"""Entities registered under a pre-2026.9.0 key are adopted, not duplicated."""

from __future__ import annotations

from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import DOMAIN
from homeassistant.helpers import entity_registry as er

from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

_OLD_TO_NEW = {
    "effective_price": "active_price",
    "today_avg": "today_average",
    "setting_max_price": "setting_price_threshold",
}


async def test_renamed_keys_keep_their_entity_id(hass, options, mock_utcnow):
    """The pre-rename entity survives with its ID, history and settings."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    for old_key in _OLD_TO_NEW:
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_{old_key}",
            config_entry=entry,
            suggested_object_id=f"kilowahti_test_home_{old_key}",
        )
    pre_existing = {
        old: registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{old}")
        for old in _OLD_TO_NEW
    }

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for old_key, new_key in _OLD_TO_NEW.items():
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{old_key}") is None
        migrated = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{new_key}")
        assert migrated == pre_existing[old_key], f"{old_key} did not keep its entity ID"


async def test_migration_leaves_a_finished_registry_alone(hass, options, mock_utcnow):
    """Running against already-migrated entities is a no-op, not a duplication."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    before = {e.entity_id: e.unique_id for e in registry.entities.values()}

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    after = {e.entity_id: e.unique_id for e in registry.entities.values()}
    assert after == before
