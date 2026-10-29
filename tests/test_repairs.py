"""Tests for the stale price-source failover repair issue."""

from __future__ import annotations

from datetime import timedelta

from custom_components.kilowahti.const import DOMAIN
from custom_components.kilowahti.repairs import async_update_failover_issue
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util


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
