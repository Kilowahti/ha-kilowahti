"""Tests for KilowahtiCoordinator."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from kilowahti import calc
from kilowahti.models import PriceSlot
from kilowahti.sources.kilowahti_cdn import KilowahtiCdnSource, KilowahtiCdnZoneNotFoundError
from kilowahti.sources.spot_hinta import SpotHintaSource
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_POWER_KW,
    CONF_CURRENCY_MODE,
    CONF_DISPLAY_UNIT,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_EXPOSE_TOTAL_PRICE_ARRAYS,
    CONF_FX_MODE,
    CONF_FX_RATE,
    CONF_HIGH_PRECISION,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_MONTHLY_FIXED_COST,
    CONF_PRICE_RESOLUTION,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_TRANSFER_GROUPS,
    CONF_VAT_RATE,
    DOMAIN,
    PRICE_SOURCE_KILOWAHTI_CDN,
    PRICE_SOURCE_SPOT_HINTA,
    UNIT_EUROKWH,
)
from homeassistant.config_entries import ConfigEntryState

from .conftest import (
    CDN_PAYLOAD,
    CDN_URL_RE,
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

    # Pretend we're at 15:00 UTC — inside the eager window (13–21).
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


async def test_eager_poll_window_close_uses_cet_not_ha_local(hass, setup_integration):
    """The eager-end cutoff is evaluated in CET/CEST, not HA local time.

    HA is UTC in this fixture. At 20:15 UTC the HA-local hour (20) is still
    inside the default 13-21 window, but Europe/Berlin is UTC+1 in March
    (pre-DST), so it's already 21:15 CET — past the cutoff. No request
    should be attempted; an unmatched request would raise via aioresponses.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._tomorrow_slots = None

    past_cutoff_in_cet = datetime(2026, 3, 13, 20, 15, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=past_cutoff_in_cet):
        with aioresponses():
            await coord._async_eager_poll()

    assert coord._tomorrow_slots is None
    assert coord._eager_poll_unsub is None  # no retry scheduled — window already closed


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


@pytest.mark.asyncio
async def test_total_price_rank_now_uses_fixed_period_price(hass, options, mock_utcnow):
    """total_price_rank_now uses fixed-period price, matching total_price sensor.

    Current slot (00:00) has highest spot price (normalized rank=24). With a fixed period
    active all slots share the same energy price → all tied → rank=1 for all.
    """
    from datetime import date, timezone as tz

    from kilowahti.models import FixedPeriod

    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]

    coord._today_slots = [
        PriceSlot(dt_utc=datetime(2026, 3, 13, 0, 0, tzinfo=tz.utc), price_no_tax=10.0, rank=3),
        PriceSlot(dt_utc=datetime(2026, 3, 13, 1, 0, tzinfo=tz.utc), price_no_tax=5.0, rank=2),
        PriceSlot(dt_utc=datetime(2026, 3, 13, 2, 0, tzinfo=tz.utc), price_no_tax=3.0, rank=1),
    ]

    assert coord.total_price_rank_now() == 24

    coord._storage._periods = [
        FixedPeriod(
            id="fp1",
            label="Fixed",
            start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 13),
            price=5.0,
        )
    ]

    assert coord.total_price_rank_now() == 1


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
    """10 kWh consumed in the cheapest slot accumulates in q1 and scores 100."""
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


# ---------------------------------------------------------------------------
# monthly_fixed_cost_today
# ---------------------------------------------------------------------------


async def test_monthly_fixed_cost_today_returns_none_when_zero(
    hass, setup_integration, mock_utcnow
):
    """monthly_fixed_cost_today returns None when monthly cost is 0.0 (the default)."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.monthly_fixed_cost_today() is None


async def test_monthly_fixed_cost_today_returns_daily_share(hass, setup_integration, mock_utcnow):
    """monthly_fixed_cost_today returns monthly_cost / days_in_month.

    FROZEN_DATE is 2026-03-13; March has 31 days.
    Setting cost to 31.0 € → daily share = 1.0 €/day.
    """
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        new_opts = {**entry.options, CONF_MONTHLY_FIXED_COST: 31.0}
        hass.config_entries.async_update_entry(entry, options=new_opts)
        await hass.async_block_till_done()

    assert coord.monthly_fixed_cost_today() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# charge_opportunity_factor
# ---------------------------------------------------------------------------


async def test_charge_opportunity_factor_returns_1_for_cheapest(
    hass, setup_integration, mock_utcnow
):
    """charge_opportunity_factor returns 1.0 when the current slot is cheapest today.

    FROZEN_UTC = 00:30 UTC → current slot is at 00:00 (PriceNoTax=0.03, rank 1).
    With no transfer price: total = spot_effective = cheapest slot → factor = 1.0.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.charge_opportunity_factor() == pytest.approx(1.0)


async def test_charge_opportunity_factor_returns_none_when_no_today_slots(
    hass, setup_integration, mock_utcnow
):
    """charge_opportunity_factor returns None when no today slots are loaded."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._today_slots = []
    assert coord.charge_opportunity_factor() is None


# ---------------------------------------------------------------------------
# battery_charge_recommendation
# ---------------------------------------------------------------------------


async def test_battery_charge_recommendation_none_when_no_battery(
    hass, setup_integration, mock_utcnow
):
    """battery_charge_recommendation returns None when battery capacity is 0 (default)."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    # Default battery_capacity_kwh = 0.0 → no battery configured.
    assert coord.battery_charge_recommendation() is None


async def test_battery_charge_recommendation_charge_from_grid_when_cheapest(
    hass, setup_integration, mock_utcnow
):
    """battery_charge_recommendation returns 'charge_from_grid' when current slot is cheapest.

    FROZEN_UTC = 00:30 → slot at 00:00 (rank 1 of 3 = cheapest).
    position = (3.765 - 3.765) / (12.55 - 3.765) = 0.0 ≤ 0.25 → charge_from_grid.
    """
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        new_opts = {**entry.options, CONF_BATTERY_CAPACITY_KWH: 10.0}
        hass.config_entries.async_update_entry(entry, options=new_opts)
        await hass.async_block_till_done()

    assert coord.battery_charge_recommendation() == "charge_from_grid"


# ---------------------------------------------------------------------------
# export_price_now
# ---------------------------------------------------------------------------


async def test_export_price_now_spot_linked_no_commission(hass, setup_integration, mock_utcnow):
    """export_price_now returns slot.price_no_tax when spot-linked with zero commission.

    PriceSlot.price_no_tax is stored in c/kWh (source converts from €/kWh).
    FROZEN_UTC = 00:30 → current slot has price_no_tax=3.0 c/kWh (0.03 €/kWh * 100).
    export = max(0.0, 3.0 - 0.0) = 3.0 c/kWh.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    export = coord.export_price_now()
    assert export == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# get_daily_score — quartile-midpoint placeholder when no meter data
# ---------------------------------------------------------------------------


async def test_get_daily_score_uses_quartile_midpoint_when_no_meter_data(
    hass, setup_integration, mock_utcnow
):
    """With no consumption recorded, the score reflects the current quartile midpoint.

    FROZEN_UTC = 2026-03-13T00:30Z → current slot is the cheapest (Q1) → 87.5.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.total_price_quartile() == 1
    assert coord.get_daily_score("nonexistent_profile") == 87.5


async def test_get_daily_score_returns_none_when_no_price_data(
    hass, setup_integration, mock_utcnow
):
    """When neither consumption nor price data exist, score is unknown."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._today_slots = []
    assert coord.get_daily_score("p1") is None


# ---------------------------------------------------------------------------
# get_monthly_score
# ---------------------------------------------------------------------------


async def test_get_monthly_score_falls_back_when_no_data_anywhere(
    hass, setup_integration, mock_utcnow
):
    """Monthly score returns None when nothing is available — no current-month days,
    no in-progress today, no previous month."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._today_slots = []  # also kills today's quartile-midpoint fallback
    assert coord.get_monthly_score("p1") is None


async def test_get_monthly_score_includes_todays_in_progress(hass, setup_integration, mock_utcnow):
    """Monthly score averages completed days plus today's in-progress score.

    FROZEN_DATE = 2026-03-13 → month_key = '2026-03'.
    Two completed days (80, 60) plus today's quartile-midpoint placeholder (Q1 → 87.5)
    → average = (80 + 60 + 87.5) / 3 = 75.833…
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._daily_history = [
        {"date": "2026-03-01", "scores": {"p1": 80.0}},
        {"date": "2026-03-02", "scores": {"p1": 60.0}},
        {"date": "2026-02-28", "scores": {"p1": 50.0}},  # previous month — must be excluded
    ]
    assert coord.get_monthly_score("p1") == pytest.approx((80.0 + 60.0 + 87.5) / 3)


async def test_get_monthly_score_falls_back_to_previous_month(hass, setup_integration, mock_utcnow):
    """When neither completed days nor today's score are available for the current
    month, fall back to the previous month's finalised score."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._today_slots = []  # disables today's placeholder
    coord._month_scores = [{"month": "2026-02", "scores": {"p1": 73.0}}]
    assert coord.get_monthly_score("p1") == pytest.approx(73.0)


# ---------------------------------------------------------------------------
# _async_finalise_daily_scores — skip profiles with no data
# ---------------------------------------------------------------------------


async def test_finalise_daily_scores_skips_profiles_with_no_data(hass, options, mock_utcnow):
    """Profiles with empty bucket_data are excluded from the daily history entry.

    Two profiles configured: 'p1' has consumed 10 kWh in q1; 'p2' has no data.
    After finalisation, history should contain only 'p1'.
    """
    opts = dict(options)
    opts[CONF_SCORE_PROFILES] = [
        {"id": "p1", "label": "Profile 1", "meters": [], "formula": "default"},
        {"id": "p2", "label": "Profile 2", "meters": [], "formula": "default"},
    ]
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=opts)

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]
    # p1 has data; p2 has nothing.
    coord._score_data = {"p1": {"q1": 10.0}}

    # Record history length before our call (startup may have already added an entry).
    history_before = len(coord._daily_history)
    await coord._async_finalise_daily_scores()

    assert len(coord._daily_history) == history_before + 1
    day_entry = coord._daily_history[-1]
    assert "p1" in day_entry["scores"]
    assert "p2" not in day_entry["scores"]


# ---------------------------------------------------------------------------
# charge_from_grid_recommended / discharge_to_grid_recommended
# ---------------------------------------------------------------------------


async def test_charge_from_grid_recommended_true_when_cheapest(
    hass, setup_integration, mock_utcnow
):
    """charge_from_grid_recommended is True when current slot is cheapest and more expensive slots follow.

    FROZEN_UTC = 00:30 → slot 0 (cheapest). Future slots at 01:00 and 02:00 are pricier.
    """
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        new_opts = {**entry.options, CONF_BATTERY_CAPACITY_KWH: 10.0}
        hass.config_entries.async_update_entry(entry, options=new_opts)
        await hass.async_block_till_done()

    assert coord.charge_from_grid_recommended() is True


async def test_discharge_to_grid_recommended_false_when_cheapest(
    hass, setup_integration, mock_utcnow
):
    """discharge_to_grid_recommended is False when export price is not in top quartile.

    FROZEN_UTC = 00:30 → slot 0 export price = 3.0 c/kWh.
    Top-quartile threshold = 10.0 c/kWh (slot 2). 3.0 < 10.0 → False.
    """
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        new_opts = {**entry.options, CONF_BATTERY_CAPACITY_KWH: 10.0}
        hass.config_entries.async_update_entry(entry, options=new_opts)
        await hass.async_block_till_done()

    assert coord.discharge_to_grid_recommended() is False


# ---------------------------------------------------------------------------
# import_export_spread_now / self_consumption_value_now
# ---------------------------------------------------------------------------


async def test_import_export_spread_now(hass, setup_integration, mock_utcnow):
    """import_export_spread_now = total_price_now - export_price_now.

    Slot 0: spot_effective = 3.765 c/kWh, export = 3.0 c/kWh → spread = 0.765.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    spread = coord.import_export_spread_now()
    assert spread == pytest.approx(0.765, rel=1e-3)


async def test_self_consumption_value_now_equals_total_price(hass, setup_integration, mock_utcnow):
    """self_consumption_value_now equals total_price_now (avoided import cost per kWh)."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.self_consumption_value_now() == pytest.approx(coord.total_price_now(), rel=1e-6)


# ---------------------------------------------------------------------------
# optimal_charge_window
# ---------------------------------------------------------------------------


async def test_optimal_charge_window_none_when_no_battery(hass, setup_integration, mock_utcnow):
    """optimal_charge_window returns None when battery is not configured."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.optimal_charge_window() is None


async def test_optimal_charge_window_selects_cheapest_2h_window(
    hass, setup_integration, mock_utcnow
):
    """optimal_charge_window picks the 2-slot window with the lowest average total price.

    Battery: 10 kWh capacity, 5 kW charge power → charge_hours=2h → 2 slots needed.
    Fixture slots (total price, no transfer):
      00:00 → 3.765 c/kWh
      01:00 → 6.275 c/kWh
      02:00 → 12.55 c/kWh
    Window [00:00, 01:00] avg=5.02 < [01:00, 02:00] avg=9.41 → start at 00:00, end at 02:00.
    """
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        new_opts = {
            **entry.options,
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_CHARGE_POWER_KW: 5.0,
        }
        hass.config_entries.async_update_entry(entry, options=new_opts)
        await hass.async_block_till_done()

    result = coord.optimal_charge_window()
    assert result is not None
    start_dt, end_dt = result
    assert start_dt.hour == 0 and start_dt.minute == 0
    assert end_dt.hour == 2 and end_dt.minute == 0


# ---------------------------------------------------------------------------
# Price source chain
# ---------------------------------------------------------------------------


async def test_chain_composition_nordic_vs_cdn_only(hass, options, mock_utcnow):
    """FI gets CDN + spot-hinta; a zone outside spot-hinta coverage gets CDN only."""
    await hass.config.async_set_time_zone("UTC")
    options[CONF_PRICE_RESOLUTION] = 15
    options[CONF_REGION] = "PT"
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)

    pt_payload = {**CDN_PAYLOAD, "days": {"2026-03-13": CDN_PAYLOAD["days"]["2026-03-13"]}}
    with aioresponses() as m:
        m.get(
            re.compile(r"https://cdn\.kilowahti\.fi/v1/pt/latest\.json"),
            payload=pt_payload,
            repeat=True,
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]
    assert [name for name, _ in coord._sources] == [PRICE_SOURCE_KILOWAHTI_CDN]
    assert coord.price_source_name == PRICE_SOURCE_KILOWAHTI_CDN
    assert coord.last_failover_utc is None
    assert len(coord.today_slots()) == 96


async def test_chain_cdn_primary_serves_nordic_region(hass, options, mock_utcnow):
    """With the CDN healthy, FI is served by the CDN and spot-hinta is never called."""
    await hass.config.async_set_time_zone("UTC")
    options[CONF_PRICE_RESOLUTION] = 15
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)

    payload = {**CDN_PAYLOAD, "days": {"2026-03-13": CDN_PAYLOAD["days"]["2026-03-13"]}}
    with aioresponses() as m:
        m.get(CDN_URL_RE, payload=payload, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]
    assert [name for name, _ in coord._sources] == [
        PRICE_SOURCE_KILOWAHTI_CDN,
        PRICE_SOURCE_SPOT_HINTA,
    ]
    assert isinstance(coord._sources[0][1], KilowahtiCdnSource)
    assert isinstance(coord._sources[1][1], SpotHintaSource)
    assert coord.price_source_name == PRICE_SOURCE_KILOWAHTI_CDN
    assert coord.last_failover_utc is None
    # First slot of the FI local day: 10.0 EUR/MWh → 1.0 c/kWh
    assert abs(coord.today_slots()[0].price_no_tax - 1.0) < 1e-9


async def test_chain_falls_back_to_spot_hinta_on_cdn_failure(hass, setup_integration):
    """setup_integration mocks only spot-hinta, so the CDN fetch fails and the
    chain serves today's prices from spot-hinta, recording the failover."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.price_source_name == PRICE_SOURCE_SPOT_HINTA
    assert coord.last_failover_utc is not None
    assert len(coord.today_slots()) == len(TODAY_PAYLOAD)


async def test_eager_poll_no_fallback_before_deadline(hass, setup_integration):
    """Primary returning None before the CET deadline only reschedules; fallback untouched."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._tomorrow_slots = None
    if coord._eager_poll_unsub is not None:
        coord._eager_poll_unsub()
        coord._eager_poll_unsub = None

    # 13:30 CET (12:30 UTC) — inside the eager window, before the 15:00 deadline
    poll_time = datetime(2026, 3, 13, 12, 30, 0, tzinfo=timezone.utc)
    cdn_mock = AsyncMock(return_value=None)
    fallback_mock = AsyncMock()
    with patch("homeassistant.util.dt.utcnow", return_value=poll_time):
        with (
            patch.object(coord._sources[0][1], "fetch_tomorrow", cdn_mock),
            patch.object(coord._sources[1][1], "fetch_tomorrow", fallback_mock),
        ):
            await coord._async_eager_poll()

    assert coord._tomorrow_slots is None
    assert coord._eager_poll_unsub is not None  # retry scheduled
    fallback_mock.assert_not_called()
    coord._eager_poll_unsub()
    coord._eager_poll_unsub = None


async def test_eager_poll_falls_back_after_deadline(hass, setup_integration):
    """Primary silent past 15:00 CET → next source is tried and its tomorrow accepted."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._tomorrow_slots = None
    coord._last_failover_utc = None
    coord._active_source_name = PRICE_SOURCE_KILOWAHTI_CDN
    if coord._eager_poll_unsub is not None:
        coord._eager_poll_unsub()
        coord._eager_poll_unsub = None

    fallback_slots = list(coord.today_slots())
    # 15:30 CET (14:30 UTC) — past the fallback deadline
    poll_time = datetime(2026, 3, 13, 14, 30, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=poll_time):
        with (
            patch.object(coord._sources[0][1], "fetch_tomorrow", AsyncMock(return_value=None)),
            patch.object(
                coord._sources[1][1], "fetch_tomorrow", AsyncMock(return_value=fallback_slots)
            ),
        ):
            await coord._async_eager_poll()

    assert coord._tomorrow_slots == fallback_slots
    assert coord.price_source_name == PRICE_SOURCE_SPOT_HINTA
    assert coord.last_failover_utc is not None
    assert coord._eager_poll_unsub is None  # tomorrow stored — no further polling


async def test_eager_poll_does_not_retry_on_cdn_zone_not_found(hass, options, mock_utcnow):
    """All sources failing permanently (zone-not-found) must not schedule a retry."""
    await hass.config.async_set_time_zone("UTC")
    options[CONF_PRICE_RESOLUTION] = 15
    options[CONF_REGION] = "PT"  # CDN-only chain
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)

    pt_payload = {**CDN_PAYLOAD, "days": {"2026-03-13": CDN_PAYLOAD["days"]["2026-03-13"]}}
    with aioresponses() as m:
        m.get(
            re.compile(r"https://cdn\.kilowahti\.fi/v1/pt/latest\.json"),
            payload=pt_payload,
            repeat=True,
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]
    coord._tomorrow_slots = None
    if coord._eager_poll_unsub is not None:
        coord._eager_poll_unsub()
        coord._eager_poll_unsub = None

    eager_time = datetime(2026, 3, 13, 15, 0, 0, tzinfo=timezone.utc)
    not_found = KilowahtiCdnZoneNotFoundError(None, (), status=404)
    with patch("homeassistant.util.dt.utcnow", return_value=eager_time):
        with patch.object(coord._sources[0][1], "fetch_tomorrow", AsyncMock(side_effect=not_found)):
            await coord._async_eager_poll()

    assert coord._tomorrow_slots is None
    assert coord._eager_poll_unsub is None  # no retry scheduled — permanent error


# ---------------------------------------------------------------------------
# Currency / FX
# ---------------------------------------------------------------------------

SE1_CDN_URL = re.compile(r"https://cdn\.kilowahti\.fi/v1/se1/latest\.json")
ECB_URL = re.compile(r"https://www\.ecb\.europa\.eu/stats/eurofxref/eurofxref-daily\.xml")

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-03-12">
    <Cube currency="SEK" rate="11.5000"/>
  </Cube></Cube>
</gesmes:Envelope>
"""


async def _setup_se1(hass, options, currency_opts: dict, mock_ecb: bool = False):
    await hass.config.async_set_time_zone("UTC")
    options = {
        **options,
        CONF_REGION: "SE1",
        CONF_PRICE_RESOLUTION: 15,
        **currency_opts,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    payload = {**CDN_PAYLOAD, "days": {"2026-03-13": CDN_PAYLOAD["days"]["2026-03-13"]}}
    with aioresponses() as m:
        m.get(SE1_CDN_URL, payload=payload, repeat=True)
        if mock_ecb:
            m.get(ECB_URL, body=ECB_XML, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_fx_manual_rate_applied_to_prices(hass, options, mock_utcnow):
    """Local mode with a manual rate converts spot prices and unit labels."""
    coord = await _setup_se1(
        hass,
        options,
        {CONF_CURRENCY_MODE: "local", CONF_FX_MODE: "manual", CONF_FX_RATE: 11.0},
    )

    assert coord.fx_rate == 11.0
    assert coord.local_display_active is True
    assert coord.native_unit == "öre/kWh"
    # First FI-fixture slot: 10.0 EUR/MWh → 1.0 c/kWh → × 11 × (1 + VAT)
    slot = coord.today_slots()[0]
    expected = 1.0 * 11.0 * (1 + coord._vat_rate) + coord._spot_commission
    assert coord._spot_effective(slot) == pytest.approx(expected)


async def test_fx_absent_currency_mode_defaults_to_eur(hass, options, mock_utcnow):
    """Entries without the currency_mode option keep EUR display unchanged."""
    coord = await _setup_se1(hass, options, {})

    assert coord.fx_rate == 1.0
    assert coord.local_display_active is False
    assert coord.native_unit == "c/kWh"


async def test_fx_auto_first_start_fetches_ecb_rate(hass, options, mock_utcnow):
    """Auto mode without a persisted rate fetches ECB at startup and applies it."""
    coord = await _setup_se1(
        hass,
        options,
        {CONF_CURRENCY_MODE: "local", CONF_FX_MODE: "auto"},
        mock_ecb=True,
    )

    assert coord.fx_rate == 11.5
    assert coord._fx_active_rate == 11.5
    assert coord.fx_rate_date == "2026-03-12"


async def test_fx_staged_rate_promoted_at_rollover(hass, options, mock_utcnow):
    """The staged rate only becomes active via rollover promotion."""
    coord = await _setup_se1(
        hass,
        options,
        {CONF_CURRENCY_MODE: "local", CONF_FX_MODE: "auto"},
        mock_ecb=True,
    )
    assert coord.fx_rate == 11.5

    coord._fx_staged_rate = 12.0
    coord._fx_staged_date = "2026-03-13"
    assert coord.fx_rate == 11.5  # staged rate does not apply mid-day

    await coord._async_promote_staged_fx()
    assert coord.fx_rate == 12.0


async def test_fx_major_only_currency_forces_major_unit(hass, options, mock_utcnow):
    """CZK has no minor unit in use — display collapses to Kč/kWh."""
    await hass.config.async_set_time_zone("UTC")
    options = {
        **options,
        CONF_REGION: "CZ",
        CONF_PRICE_RESOLUTION: 15,
        CONF_CURRENCY_MODE: "local",
        CONF_FX_MODE: "manual",
        CONF_FX_RATE: 24.7,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    payload = {**CDN_PAYLOAD, "days": {"2026-03-13": CDN_PAYLOAD["days"]["2026-03-13"]}}
    with aioresponses() as m:
        m.get(
            re.compile(r"https://cdn\.kilowahti\.fi/v1/cz/latest\.json"),
            payload=payload,
            repeat=True,
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = hass.data[DOMAIN][entry.entry_id]
    assert coord.native_unit == "Kč/kWh"
    assert coord.display_in_major is True
    # format_price converts internal minor scale to major
    assert coord.format_price(100.0) == 1.0


# ---------------------------------------------------------------------------
# Price array attributes
# ---------------------------------------------------------------------------

_TRANSFER_GROUP = {
    "id": "g1",
    "label": "Flat",
    "active": True,
    "tiers": [
        {
            "label": "All hours",
            "price": 3.0,
            "months": list(range(1, 13)),
            "weekdays": list(range(0, 7)),
            "hour_start": 0,
            "hour_end": 24,
            "priority": 1,
        }
    ],
    "monthly_fixed_cost": 0.0,
}


async def _setup_with(hass, options, extra):
    """Set up an entry with `extra` merged into `options` and return its coordinator."""
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options={**options, **extra})
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_price_arrays_are_none_when_options_disabled(hass, options, mock_utcnow):
    """Both array pairs stay unset while their options are off."""
    coord = await _setup_with(hass, options, {})

    assert coord.today_price_array() is None
    assert coord.tomorrow_price_array() is None
    assert coord.today_total_price_array() is None
    assert coord.tomorrow_total_price_array() is None


async def test_options_gate_the_two_array_pairs_independently(hass, options, mock_utcnow):
    """The total-price option does not switch on the spot arrays, or the reverse."""
    coord = await _setup_with(hass, options, {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True})
    assert coord.today_price_array() is None
    assert coord.today_total_price_array() is not None

    coord = await _setup_with(hass, options, {CONF_EXPOSE_PRICE_ARRAYS: True})
    assert coord.today_price_array() is not None
    assert coord.today_total_price_array() is None


async def test_total_price_array_entry_shape(hass, options, mock_utcnow):
    """Entries carry time, the energy/transfer breakdown, their sum, and a rank."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True, CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]},
    )

    arr = coord.today_total_price_array()
    assert len(arr) == 3
    assert set(arr[0]) == {"time", "energy", "transfer", "price", "rank"}
    assert arr[0]["time"] == "2026-03-13T00:00:00+00:00"
    # Cheapest slot: 0.03 €/kWh = 3.0 c/kWh, +25.5% VAT = 3.765, transfer 3.0
    assert arr[0]["energy"] == pytest.approx(3.77, abs=0.011)
    assert arr[0]["transfer"] == 3.0


async def test_total_price_equals_sum_of_rounded_components(hass, options, mock_utcnow):
    """price is derived from the rounded parts, so the breakdown always adds up."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True, CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]},
    )

    for entry in coord.today_total_price_array():
        assert entry["price"] == pytest.approx(entry["energy"] + entry["transfer"])


async def test_total_price_array_transfer_is_zero_without_group(hass, options, mock_utcnow):
    """With no transfer group configured the field reports 0.0, never None."""
    coord = await _setup_with(hass, options, {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True})

    for entry in coord.today_total_price_array():
        assert entry["transfer"] == 0.0
        assert entry["price"] == entry["energy"]


async def test_total_price_array_ranks_by_total_price(hass, options, mock_utcnow):
    """Ranks are tier-normalized across the day: cheapest 1, dearest slots_per_day."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True, CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]},
    )

    ranks = [e["rank"] for e in coord.today_total_price_array()]
    assert ranks[0] == 1
    assert ranks[-1] == 24  # HOUR resolution → 24 slots/day
    assert ranks == sorted(ranks)


async def test_tomorrow_total_price_array_ranks_within_its_own_day(hass, options, mock_utcnow):
    """Tomorrow's entries are ranked among tomorrow's slots, not today's."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True, CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]},
    )

    eager_time = datetime(2026, 3, 13, 15, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=eager_time):
        with aioresponses() as m:
            m.get(TOMORROW_URL_RE, payload=TOMORROW_PAYLOAD)
            await coord._async_eager_poll()

    arr = coord.tomorrow_total_price_array()
    assert arr is not None
    assert min(e["rank"] for e in arr) == 1


async def test_total_price_array_uses_fixed_period_price(hass, options, mock_utcnow):
    """Today's entries price energy from the active fixed period, not spot."""
    from datetime import date

    from kilowahti.models import FixedPeriod

    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True, CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]},
    )
    coord._storage._periods = [
        FixedPeriod(
            id="fp1",
            label="Fixed",
            start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 14),
            price=5.0,
        )
    ]

    for entry in coord.today_total_price_array():
        assert entry["energy"] == 5.0
        assert entry["price"] == 8.0


async def test_tomorrow_total_price_array_covers_fixed_period_without_spot_data(
    hass, options, mock_utcnow
):
    """A fixed period covering tomorrow yields a full array before spot data arrives."""
    from datetime import date

    from kilowahti.models import FixedPeriod

    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True, CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]},
    )
    coord._storage._periods = [
        FixedPeriod(
            id="fp1",
            label="Fixed",
            start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 14),
            price=5.0,
        )
    ]

    arr = coord.tomorrow_total_price_array()
    assert arr is not None
    assert len(arr) == 24
    assert arr[0]["time"] == "2026-03-14T00:00:00+00:00"
    for entry in arr:
        assert entry["energy"] == 5.0
        assert entry["transfer"] == 3.0
        assert entry["price"] == 8.0


async def test_tomorrow_arrays_are_none_before_tomorrow_is_fetched(hass, options, mock_utcnow):
    """Both tomorrow arrays stay unset until tomorrow's prices arrive."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_PRICE_ARRAYS: True, CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True},
    )

    assert coord.tomorrow_price_array() is None
    assert coord.tomorrow_total_price_array() is None


async def test_array_prices_are_rounded_to_display_precision(hass, options, mock_utcnow):
    """Both array types round to 2 decimals in the minor unit."""
    coord = await _setup_with(
        hass,
        options,
        {
            CONF_EXPOSE_PRICE_ARRAYS: True,
            CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True,
            CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP],
        },
    )

    for entry in coord.today_price_array():
        assert entry["price"] == round(entry["price"], 2)
    for entry in coord.today_total_price_array():
        assert entry["energy"] == round(entry["energy"], 2)
        assert entry["transfer"] == round(entry["transfer"], 2)


async def test_array_prices_follow_high_precision_option(hass, options, mock_utcnow):
    """High precision widens array rounding to 5 decimals."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_PRICE_ARRAYS: True, CONF_HIGH_PRECISION: True},
    )

    prices = [e["price"] for e in coord.today_price_array()]
    assert prices[0] == pytest.approx(3.765)


async def test_array_prices_gain_two_decimals_in_major_unit(hass, options, mock_utcnow):
    """€/kWh display shifts the scale, so arrays round to 4 decimals instead of 2."""
    coord = await _setup_with(
        hass,
        options,
        {CONF_EXPOSE_PRICE_ARRAYS: True, CONF_DISPLAY_UNIT: UNIT_EUROKWH},
    )

    prices = [e["price"] for e in coord.today_price_array()]
    assert prices[0] == pytest.approx(0.0377, abs=0.00011)
    assert prices[0] == round(prices[0], 4)


# ---------------------------------------------------------------------------
# Control factors
# ---------------------------------------------------------------------------

_TIERED_TRANSFER_GROUP = {
    "id": "g2",
    "label": "Day/night",
    "active": True,
    "tiers": [
        {
            "label": "Night",
            "price": 2.0,
            "months": list(range(1, 13)),
            "weekdays": list(range(0, 7)),
            "hour_start": 0,
            "hour_end": 7,
            "priority": 1,
        },
        {
            "label": "Day",
            "price": 5.0,
            "months": list(range(1, 13)),
            "weekdays": list(range(0, 7)),
            "hour_start": 7,
            "hour_end": 24,
            "priority": 2,
        },
    ],
    "monthly_fixed_cost": 0.0,
}


def _fixed_period_today():
    from datetime import date

    from kilowahti.models import FixedPeriod

    return FixedPeriod(
        id="fp1",
        label="Fixed",
        start_date=date(2026, 3, 13),
        end_date=date(2026, 3, 13),
        price=5.0,
    )


async def test_current_rank_uses_fixed_period_price(hass, options, mock_utcnow):
    """The price rank follows the energy price actually paid, not the spot order."""
    coord = await _setup_with(hass, options, {})

    coord._today_slots = [
        PriceSlot(
            dt_utc=datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc), price_no_tax=10.0, rank=3
        ),
        PriceSlot(
            dt_utc=datetime(2026, 3, 13, 1, 0, tzinfo=timezone.utc), price_no_tax=5.0, rank=2
        ),
        PriceSlot(
            dt_utc=datetime(2026, 3, 13, 2, 0, tzinfo=timezone.utc), price_no_tax=3.0, rank=1
        ),
    ]

    # Current slot (00:00) is the most expensive of the three by spot price
    assert coord.current_rank() == 24

    coord._storage._periods = [_fixed_period_today()]

    # A fixed period flattens the day: every slot ties at the cheapest tier
    assert coord.current_rank() == 1


async def test_control_factor_is_one_during_a_fixed_period(hass, options, mock_utcnow):
    """A flat energy price gives no reason to prefer any hour."""
    coord = await _setup_with(hass, options, {})
    coord._storage._periods = [_fixed_period_today()]

    assert coord.control_factor() == 1.0
    assert coord.control_factor_bipolar() == 1.0


async def test_control_factor_total_includes_transfer(hass, options, mock_utcnow):
    """The total control factor ranks by energy plus transfer, fixed periods included."""
    coord = await _setup_with(hass, options, {CONF_TRANSFER_GROUPS: [_TIERED_TRANSFER_GROUP]})
    coord._storage._periods = [_fixed_period_today()]

    # Energy is flat, so only the transfer tier separates the slots. The current
    # slot (00:00) falls in the cheap night tier.
    assert coord.control_factor_total() == 1.0
    assert coord.control_factor_total_bipolar() == 1.0

    # Without the fixed period the current slot is the cheapest by spot too
    coord._storage._periods = []
    assert coord.control_factor_total() == 1.0


async def test_control_factor_transfer_is_one_for_the_cheapest_tier(hass, options, mock_utcnow):
    """Transfer control factor uses the same polarity as the price one: 1.0 = cheapest."""
    coord = await _setup_with(hass, options, {CONF_TRANSFER_GROUPS: [_TIERED_TRANSFER_GROUP]})

    # Frozen time is 00:30 → night tier, the cheaper of the two
    assert coord.control_factor_transfer() == 1.0
    assert coord.control_factor_transfer_bipolar() == 1.0


async def test_control_factor_transfer_is_zero_for_the_dearest_tier(hass, options, mock_utcnow):
    """The expensive tier sits at the other end of the range."""
    coord = await _setup_with(hass, options, {CONF_TRANSFER_GROUPS: [_TIERED_TRANSFER_GROUP]})

    day_time = datetime(2026, 3, 13, 12, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=day_time):
        assert coord.control_factor_transfer() == 0.0
        assert coord.control_factor_transfer_bipolar() == -1.0


async def test_control_factor_transfer_is_one_for_a_flat_group(hass, options, mock_utcnow):
    """A single tier means no hour is dearer than another."""
    coord = await _setup_with(hass, options, {CONF_TRANSFER_GROUPS: [_TRANSFER_GROUP]})

    assert coord.control_factor_transfer() == 1.0


async def test_control_factor_transfer_is_none_without_a_group(hass, options, mock_utcnow):
    """No transfer group configured means no transfer control factor."""
    coord = await _setup_with(hass, options, {})

    assert coord.control_factor_transfer() is None
    assert coord.control_factor_transfer_bipolar() is None


async def test_control_factor_transfer_follows_the_curve_settings(hass, options, mock_utcnow):
    """Scaling and curve function apply to the transfer factor as they do to price."""
    from custom_components.kilowahti.const import (
        CONF_CONTROL_FACTOR_FUNCTION,
        CONF_CONTROL_FACTOR_SCALING,
        CONTROL_FACTOR_SINUSOIDAL,
    )

    three_tier_group = {
        **_TIERED_TRANSFER_GROUP,
        "tiers": [
            {**_TIERED_TRANSFER_GROUP["tiers"][0], "hour_start": 0, "hour_end": 7},
            {**_TIERED_TRANSFER_GROUP["tiers"][1], "hour_start": 7, "hour_end": 12},
            {
                "label": "Peak",
                "price": 9.0,
                "months": list(range(1, 13)),
                "weekdays": list(range(0, 7)),
                "hour_start": 12,
                "hour_end": 24,
                "priority": 3,
            },
        ],
    }
    coord = await _setup_with(
        hass,
        options,
        {
            CONF_TRANSFER_GROUPS: [three_tier_group],
            CONF_CONTROL_FACTOR_FUNCTION: CONTROL_FACTOR_SINUSOIDAL,
            CONF_CONTROL_FACTOR_SCALING: 2.0,
        },
    )

    mid_tier_time = datetime(2026, 3, 13, 9, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=mid_tier_time):
        # Middle of three tiers: sinusoidal gives 0.5, squared by the scaling
        assert coord.control_factor_transfer() == pytest.approx(0.25)
