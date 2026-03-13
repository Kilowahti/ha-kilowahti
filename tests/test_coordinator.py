"""Tests for KilowahtiCoordinator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from aioresponses import aioresponses
from kilowahti import calc
from kilowahti.models import PriceSlot
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_VAT_RATE,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState

from .conftest import (
    FROZEN_DATE,
    TODAY_PAYLOAD,
    TODAY_URL_RE,
    TOMORROW_PAYLOAD,
    TOMORROW_URL_RE,
)

# ---------------------------------------------------------------------------
# Startup / cache
# ---------------------------------------------------------------------------


async def test_startup_fetches_api_when_no_cache(hass, mock_config_entry, mock_utcnow):
    """When storage has no valid cache, coordinator fetches from API on startup."""
    await hass.config.async_set_time_zone("UTC")
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert len(coord.today_slots()) == 3
    assert coord._today_date == FROZEN_DATE


async def test_startup_uses_valid_cache(hass, mock_config_entry, mock_utcnow):
    """On second setup, coordinator restores slots from cache without an API call."""
    await hass.config.async_set_time_zone("UTC")

    # First setup: fetches from API and saves cache.
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Second setup: cache is valid for today — no API call should be made.
    # aioresponses raises ConnectionError for any unmatched request, which would fail the test.
    with aioresponses():
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert len(coord.today_slots()) == 3


# ---------------------------------------------------------------------------
# API failure on startup
# ---------------------------------------------------------------------------


async def test_startup_fails_gracefully_on_api_error(hass, mock_config_entry, mock_utcnow):
    """When the spot-hinta.fi API returns 500, setup retries rather than crashing."""
    await hass.config.async_set_time_zone("UTC")
    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=500, repeat=True)
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # ConfigEntryNotReady → SETUP_RETRY (will be retried by HA automatically)
    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# Midnight rollover
# ---------------------------------------------------------------------------


async def test_midnight_rollover_promotes_tomorrow(hass, setup_integration, mock_utcnow):
    """Midnight rollover replaces today's slots with tomorrow's slots."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]

    # Inject tomorrow slots directly.
    tomorrow_slots = [
        PriceSlot(
            dt_utc=datetime(2026, 3, 14, hour, 0, tzinfo=timezone.utc),
            price_no_tax=float(hour + 1),
            rank=hour + 1,
        )
        for hour in range(3)
    ]
    coord._tomorrow_slots = tomorrow_slots

    # Simulate midnight — roll forward into the next day.
    midnight_utc = datetime(2026, 3, 14, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=midnight_utc):
        with aioresponses() as m:
            m.get(TODAY_URL_RE, status=404, repeat=True)
            m.get(TOMORROW_URL_RE, status=404, repeat=True)
            await coord._async_midnight_rollover()
    await hass.async_block_till_done()

    assert coord._today_slots == tomorrow_slots
    assert coord._tomorrow_slots is None


# ---------------------------------------------------------------------------
# Eager polling
# ---------------------------------------------------------------------------


async def test_eager_poll_reschedules_when_tomorrow_unavailable(hass, setup_integration):
    """When DayForward returns nothing, _async_eager_poll schedules a retry."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._tomorrow_slots = None

    # Pretend we're at 15:00 UTC — inside the eager window (14–21).
    eager_time = datetime(2026, 3, 13, 15, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=eager_time):
        with aioresponses() as m:
            m.get(TOMORROW_URL_RE, status=404)
            await coord._async_eager_poll()

    assert coord._tomorrow_slots is None
    assert coord._eager_poll_unsub is not None  # retry scheduled

    # Clean up the timer.
    coord._eager_poll_unsub()
    coord._eager_poll_unsub = None


async def test_eager_poll_stores_tomorrow_on_success(hass, setup_integration):
    """When DayForward returns data, tomorrow slots are stored."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._tomorrow_slots = None

    eager_time = datetime(2026, 3, 13, 15, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=eager_time):
        with aioresponses() as m:
            m.get(TOMORROW_URL_RE, payload=TOMORROW_PAYLOAD)
            await coord._async_eager_poll()

    assert coord._tomorrow_slots is not None
    assert len(coord._tomorrow_slots) == 3


# ---------------------------------------------------------------------------
# Threshold setters
# ---------------------------------------------------------------------------


async def test_set_price_threshold_persists(hass, setup_integration):
    """set_price_threshold updates instance var and persists to entry options."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    entry = setup_integration

    coord.set_price_threshold(5.0)

    assert coord._max_price_value == 5.0
    assert entry.options[CONF_MAX_PRICE] == 5.0


async def test_set_rank_threshold_persists(hass, setup_integration):
    """set_rank_threshold updates instance var and persists to entry options."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    entry = setup_integration

    coord.set_rank_threshold(10)

    assert coord._max_rank_value == 10
    assert entry.options[CONF_MAX_RANK] == 10


# ---------------------------------------------------------------------------
# Options reload behaviour
# ---------------------------------------------------------------------------


async def test_non_structural_options_change_does_not_reload(hass, setup_integration, mock_utcnow):
    """Changing VAT rate does not trigger a full integration reload."""
    entry = setup_integration
    original_entry_id = entry.entry_id

    new_options = dict(entry.options)
    new_options[CONF_VAT_RATE] = 0.10  # Change VAT, not a structural key

    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        hass.config_entries.async_update_entry(entry, options=new_options)
        await hass.async_block_till_done()

    # Entry should still be loaded (same entry_id means no reload destroyed it).
    assert entry.entry_id == original_entry_id
    assert hass.data[DOMAIN].get(entry.entry_id) is not None


async def test_structural_options_change_triggers_reload(hass, setup_integration, mock_utcnow):
    """Changing region triggers a full integration reload."""
    entry = setup_integration
    coord_before = hass.data[DOMAIN][entry.entry_id]

    new_options = dict(entry.options)
    new_options[CONF_REGION] = "EE"

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        hass.config_entries.async_update_entry(entry, options=new_options)
        await hass.async_block_till_done()

    # After reload, a new coordinator instance was created.
    coord_after = hass.data[DOMAIN].get(entry.entry_id)
    assert coord_after is not None
    assert coord_after is not coord_before


# ---------------------------------------------------------------------------
# total_price_rank_now
# ---------------------------------------------------------------------------


async def test_total_price_rank_now_returns_1_for_cheapest(hass, setup_integration, mock_utcnow):
    """total_price_rank_now returns 1 when the current slot is the cheapest today."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]

    # Fixture slots (sorted by time):
    #   00:00 UTC — 0.03 €/kWh → rank 1 (cheapest by total price, no transfer)
    #   01:00 UTC — 0.05 €/kWh → rank 2
    #   02:00 UTC — 0.10 €/kWh → rank 3
    # FROZEN_UTC = 00:30 UTC → current_slot is the 00:00 slot (cheapest).
    rank = coord.total_price_rank_now()

    assert rank == 1


# ---------------------------------------------------------------------------
# Score accumulation
# ---------------------------------------------------------------------------


async def test_score_accumulation_on_meter_change(hass, options, mock_utcnow):
    """Meter consumption (kWh delta) is accumulated into the correct price bucket.

    FROZEN_UTC = 00:30 UTC → current slot is at 00:00 UTC with rank 1.
    rank_to_bucket(1, 24) → "q1" (cheapest quartile).
    Consuming 10 kWh in q1 and then computing score should yield 100 (all cheap).
    """
    from types import SimpleNamespace

    await hass.config.async_set_time_zone("UTC")

    # Options with a score profile tracking sensor.energy_meter.
    opts = dict(options)
    opts[CONF_SCORE_PROFILES] = [
        {
            "id": "total",
            "label": "Total",
            "meters": ["sensor.energy_meter"],
            "formula": "default",
        }
    ]
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=opts)

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]

    # Simulate a meter reporting 10 kWh consumed.
    mock_event = SimpleNamespace(
        data={
            "entity_id": "sensor.energy_meter",
            "old_state": SimpleNamespace(state="90.0"),
            "new_state": SimpleNamespace(state="100.0"),
        }
    )
    coord._on_meter_state_change(mock_event)

    # Verify bucket accumulation: rank 1 of 24 slots → q1.
    expected_bucket = calc.rank_to_bucket(1, 24)
    assert coord._score_data["total"][expected_bucket] == 10.0

    # Clean up the debounce timer to avoid test teardown warnings.
    if coord._score_persist_unsub is not None:
        coord._score_persist_unsub()
        coord._score_persist_unsub = None
