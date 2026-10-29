"""Tests for the stale price-source failover repair issue."""

from __future__ import annotations

from datetime import timedelta

from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import DOMAIN
from custom_components.kilowahti.repairs import async_create_fix_flow, async_update_failover_issue
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE


async def test_stale_failover_creates_and_clears_issue(hass):
    entry_id = "test_entry"
    issue_id = f"stale_failover_{entry_id}"
    stale_since = dt_util.utcnow() - timedelta(hours=25)

    async_update_failover_issue(
        hass,
        entry_id=entry_id,
        region="FI",
        active_name="spot_hinta",
        primary_name="kilowahti_cdn",
        last_failover_utc=stale_since,
    )
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    async_update_failover_issue(
        hass,
        entry_id=entry_id,
        region="FI",
        active_name="kilowahti_cdn",
        primary_name="kilowahti_cdn",
        last_failover_utc=stale_since,
    )
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_recent_failover_does_not_create_issue(hass):
    entry_id = "test_entry_2"
    issue_id = f"stale_failover_{entry_id}"

    async_update_failover_issue(
        hass,
        entry_id=entry_id,
        region="FI",
        active_name="spot_hinta",
        primary_name="kilowahti_cdn",
        last_failover_utc=dt_util.utcnow(),
    )
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


# ---------------------------------------------------------------------------
# Stable entity IDs
# ---------------------------------------------------------------------------

_DEVICE_NAME = "Kilowahti Test Home"


def _register(hass, entry, key, original_name, object_id):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=_DEVICE_NAME,
    )
    return er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_{key}",
        config_entry=entry,
        device_id=device.id,
        original_name=original_name,
        suggested_object_id=object_id,
    )


async def _setup(hass, entry):
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _fix(hass, issue):
    flow = await async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"


async def test_generated_ids_are_offered_for_renaming(hass, options, mock_utcnow):
    """Display-name IDs, including keys renamed in 2026.9.0, are moved to the stable ID."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)
    localized = _register(hass, entry, "spot_price", "Spot-hinta", "kilowahti_test_home_spot_hinta")
    renamed_key = _register(
        hass,
        entry,
        "effective_price",
        "Effective price",
        "kilowahti_test_home_effective_price",
    )

    await _setup(hass, entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"stable_entity_ids_{entry.entry_id}")
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["count"] == "2"

    await _fix(hass, issue)

    registry = er.async_get(hass)
    assert registry.async_get(localized.entity_id) is None
    assert registry.async_get("sensor.kilowahti_test_home_spot_price").id == localized.id
    assert registry.async_get("sensor.kilowahti_test_home_active_price").id == renamed_key.id


async def test_user_renamed_entity_is_left_alone(hass, options, mock_utcnow):
    """An ID that does not match the generated one was chosen by the user."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)
    custom = _register(hass, entry, "price_rank", "Hintaranking", "my_rank")

    await _setup(hass, entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stable_entity_ids_{entry.entry_id}") is None
    assert er.async_get(hass).async_get(custom.entity_id).entity_id == "sensor.my_rank"


async def test_rename_skips_an_id_already_in_use(hass, options, mock_utcnow):
    """A taken stable ID is never overwritten."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)
    squatter = er.async_get(hass).async_get_or_create(
        "sensor", "other", "x", suggested_object_id="kilowahti_test_home_spot_price"
    )
    localized = _register(hass, entry, "spot_price", "Spot-hinta", "kilowahti_test_home_spot_hinta")

    await _setup(hass, entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"stable_entity_ids_{entry.entry_id}")
    await _fix(hass, issue)

    registry = er.async_get(hass)
    assert registry.async_get(squatter.entity_id).id == squatter.id
    assert registry.async_get(localized.entity_id).id == localized.id


async def test_no_issue_once_ids_are_stable(hass, options, mock_utcnow):
    """A fresh install, or one already fixed, raises nothing, and a reload stays quiet."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)
    _register(hass, entry, "spot_price", "Spot-hinta", "kilowahti_test_home_spot_hinta")
    await _setup(hass, entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"stable_entity_ids_{entry.entry_id}")
    await _fix(hass, issue)
    ir.async_delete_issue(hass, DOMAIN, issue.issue_id)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue.issue_id) is None


async def test_confirm_step_lists_automations_using_the_entities(hass, options, mock_utcnow):
    """The user sees which automations reference the entities before confirming."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)
    _register(hass, entry, "spot_price", "Spot-hinta", "kilowahti_test_home_spot_hinta")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Heat when cheap",
                "triggers": {
                    "trigger": "state",
                    "entity_id": "sensor.kilowahti_test_home_spot_hinta",
                },
                "actions": [],
            }
        },
    )

    await _setup(hass, entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"stable_entity_ids_{entry.entry_id}")
    flow = await async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass, flow.handler, flow.issue_id = hass, DOMAIN, issue.issue_id

    result = await flow.async_step_confirm()

    placeholders = result["description_placeholders"]
    assert placeholders["count"] == "1"
    assert result["step_id"] == "confirm"
    assert "Heat when cheap" in placeholders["usage"]


async def test_confirm_step_without_references_uses_its_own_text(hass, options, mock_utcnow):
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    entry.add_to_hass(hass)
    _register(hass, entry, "spot_price", "Spot-hinta", "kilowahti_test_home_spot_hinta")

    await _setup(hass, entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"stable_entity_ids_{entry.entry_id}")
    flow = await async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass, flow.handler, flow.issue_id = hass, DOMAIN, issue.issue_id

    result = await flow.async_step_confirm()

    assert result["step_id"] == "confirm_no_references"

    done = await flow.async_step_confirm_no_references({})
    assert done["type"] == "create_entry"
