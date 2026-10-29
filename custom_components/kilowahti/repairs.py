"""Repair issues for the Kilowahti integration."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STALE_FAILOVER_THRESHOLD = timedelta(hours=24)


def failover_issue_id(entry_id: str) -> str:
    return f"stale_failover_{entry_id}"


def async_update_failover_issue(
    hass: HomeAssistant,
    *,
    entry_id: str,
    region: str,
    active_name: str,
    primary_name: str,
    last_failover_utc: datetime | None,
) -> None:
    """Create or clear the stale-failover repair issue for a config entry.

    Called on every successful price fetch. Creates the issue once the active
    source has differed from the primary for longer than STALE_FAILOVER_THRESHOLD;
    clears it as soon as the primary source is active again.
    """
    issue_id = failover_issue_id(entry_id)

    if active_name == primary_name:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    if last_failover_utc is None:
        return

    if dt_util.utcnow() - last_failover_utc < STALE_FAILOVER_THRESHOLD:
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="stale_failover",
        translation_placeholders={
            "region": region,
            "fallback_source": active_name,
            "primary_source": primary_name,
        },
        learn_more_url="https://docs.kilowahti.fi/faq/",
    )
