"""Tests for KilowahtiCoordinator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from kilowahti import calc
from kilowahti.models import PriceSlot
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_POWER_KW,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_MONTHLY_FIXED_COST,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_TRANSFER_GROUPS,
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


@pytest.mark.asyncio
async def test_score_rank_now_uses_fixed_period_price_not_spot_rank(hass, options, mock_utcnow):
    """During a fixed-price period all slots have the same energy price.

    Current slot (00:00) has the highest spot price (rank=3). Once a fixed period is
    active all slots are tied at the fixed rate → competition rank = 1 for all.
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

    # Replace today_slots so the current slot (00:00) has the highest spot price.
    coord._today_slots = [
        PriceSlot(dt_utc=datetime(2026, 3, 13, 0, 0, tzinfo=tz.utc), price_no_tax=10.0, rank=3),
        PriceSlot(dt_utc=datetime(2026, 3, 13, 1, 0, tzinfo=tz.utc), price_no_tax=5.0, rank=2),
        PriceSlot(dt_utc=datetime(2026, 3, 13, 2, 0, tzinfo=tz.utc), price_no_tax=3.0, rank=1),
    ]

    # Without fixed period: current slot is most expensive → score rank = 3.
    assert coord._score_rank_now() == 3

    # Activate a fixed period covering the test date.
    coord._storage._periods = [
        FixedPeriod(
            id="fp1",
            label="Fixed",
            start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 13),
            price=5.0,
        )
    ]

    # All slots now have the same energy price (fixed 5.0 c/kWh, no transfer).
    # All are tied → competition rank = 1 for every slot.
    assert coord._score_rank_now() == 1


@pytest.mark.asyncio
async def test_score_rank_now_accounts_for_transfer_pricing(hass, options, mock_utcnow):
    """Transfer pricing flips the ranking vs spot alone.

    Current slot (00:00) is cheapest by spot (rank=1) but falls in a peak transfer
    tier (20 c/kWh). Totals: 00:00=23.765, 01:00=8.275, 02:00=14.55 → rank 3.
    """

    await hass.config.async_set_time_zone("UTC")

    opts = dict(options)
    opts[CONF_TRANSFER_GROUPS] = [
        {
            "id": "g1",
            "label": "Test Grid",
            "active": True,
            "monthly_fixed_cost": 0.0,
            "tiers": [
                {
                    "label": "Peak",
                    "price": 20.0,
                    "months": list(range(1, 13)),
                    "weekdays": list(range(7)),
                    "hour_start": 0,
                    "hour_end": 1,
                    "priority": 0,
                },
                {
                    "label": "Off-peak",
                    "price": 2.0,
                    "months": list(range(1, 13)),
                    "weekdays": list(range(7)),
                    "hour_start": 1,
                    "hour_end": 24,
                    "priority": 1,
                },
            ],
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

    # Current slot 00:00 has cheapest spot (rank=1) but peak transfer (20.0).
    # Its total price is the highest of the three slots → score rank = 3.
    assert coord._score_rank_now() == 3


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
# get_daily_score — regression: must return None when no meter data
# ---------------------------------------------------------------------------


async def test_get_daily_score_returns_none_when_no_meter_data(
    hass, setup_integration, mock_utcnow
):
    """get_daily_score returns None (not 0.0) when no consumption has been recorded.

    Regression: previously compute_score({}) returned 0.0, which was indistinguishable
    from a real score of zero. Unknown is the correct state when there is no data.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    # No meter events fired → _score_data is empty for any profile id.
    assert coord.get_daily_score("nonexistent_profile") is None


# ---------------------------------------------------------------------------
# get_monthly_score
# ---------------------------------------------------------------------------


async def test_get_monthly_score_returns_none_when_no_history(hass, setup_integration, mock_utcnow):
    """get_monthly_score returns None when no daily history exists for this month."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    assert coord.get_monthly_score("p1") is None


async def test_get_monthly_score_returns_average_of_completed_daily_scores(
    hass, setup_integration, mock_utcnow
):
    """get_monthly_score returns the average of all daily scores for the current month.

    FROZEN_DATE = 2026-03-13 → month_key = '2026-03'.
    Two injected days (scores 80 and 60) → average = 70.
    """
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    coord._daily_history = [
        {"date": "2026-03-01", "scores": {"p1": 80.0}},
        {"date": "2026-03-02", "scores": {"p1": 60.0}},
        {"date": "2026-02-28", "scores": {"p1": 50.0}},  # previous month — must be excluded
    ]
    assert coord.get_monthly_score("p1") == pytest.approx(70.0)


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
