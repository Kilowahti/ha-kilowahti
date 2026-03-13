"""Tests for Kilowahti service handlers."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import voluptuous as vol
from kilowahti.models import FixedPeriod

from custom_components.kilowahti.const import DOMAIN

# All slots in the fixture span 2026-03-13 00:00–02:00 UTC.
_T0 = datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc)
_T3 = datetime(2026, 3, 13, 3, 0, tzinfo=timezone.utc)  # after all slots
_T1 = datetime(2026, 3, 13, 1, 0, tzinfo=timezone.utc)  # cuts off slot at 01:00+


# ---------------------------------------------------------------------------
# get_prices
# ---------------------------------------------------------------------------


async def test_get_prices_returns_all_slots_in_range(hass, setup_integration, mock_utcnow):
    """get_prices returns every slot whose start time falls in [start, end)."""
    result = await hass.services.async_call(
        DOMAIN,
        "get_prices",
        {"start": _T0.isoformat(), "end": _T3.isoformat(), "formatted": True},
        blocking=True,
        return_response=True,
    )

    assert "unit" in result
    periods = result["price_periods"]
    assert len(periods) == 3
    # Prices should be in ascending order (slots are sorted by time and fixture ranks are 1,2,3).
    prices = [p["price"] for p in periods]
    assert prices == sorted(prices)


# ---------------------------------------------------------------------------
# cheapest_hours
# ---------------------------------------------------------------------------


async def test_cheapest_hours_returns_correct_window(hass, setup_integration, mock_utcnow):
    """cheapest_hours selects the 1-hour window with the lowest average price."""
    result = await hass.services.async_call(
        DOMAIN,
        "cheapest_hours",
        {"start": _T0.isoformat(), "end": _T3.isoformat(), "hours": 1, "formatted": True},
        blocking=True,
        return_response=True,
    )

    periods = result["price_periods"]
    assert len(periods) == 1
    # The cheapest slot is at 00:00 UTC (PriceNoTax=0.03).
    assert periods[0]["time"].startswith("2026-03-13T00:00:00")


# ---------------------------------------------------------------------------
# average_price
# ---------------------------------------------------------------------------


async def test_average_price_returns_correct_stats(hass, setup_integration, mock_utcnow):
    """average_price returns correct avg/min/max/slot_count over the range."""
    result = await hass.services.async_call(
        DOMAIN,
        "average_price",
        {"start": _T0.isoformat(), "end": _T3.isoformat(), "formatted": True},
        blocking=True,
        return_response=True,
    )

    # Effective prices (VAT 25.5%, no commission):
    #   3.0 * 1.255 = 3.765   (slot 1)
    #   5.0 * 1.255 = 6.275   (slot 2)
    #  10.0 * 1.255 = 12.55   (slot 3)
    assert result["slot_count"] == 3
    assert result["min_price"] == pytest.approx(3.765, abs=0.01)
    assert result["max_price"] == pytest.approx(12.55, abs=0.01)
    avg = (3.765 + 6.275 + 12.55) / 3
    assert result["average_price"] == pytest.approx(avg, abs=0.05)


# ---------------------------------------------------------------------------
# add_fixed_period
# ---------------------------------------------------------------------------


async def test_add_fixed_period_rejects_overlap(hass, setup_integration, mock_utcnow):
    """add_fixed_period raises when the new period overlaps an existing one."""
    # Add a first period.
    await hass.services.async_call(
        DOMAIN,
        "add_fixed_period",
        {
            "label": "Winter flat",
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            "price": 8.0,
        },
        blocking=True,
    )

    # Attempt to add an overlapping period.
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "add_fixed_period",
            {
                "label": "Overlap",
                "start_date": "2026-03-15",
                "end_date": "2026-04-15",
                "price": 9.0,
            },
            blocking=True,
        )


# ---------------------------------------------------------------------------
# get_active_prices — fixed period
# ---------------------------------------------------------------------------


async def test_get_active_prices_uses_fixed_price(hass, setup_integration, mock_utcnow):
    """get_active_prices substitutes fixed period price for spot when a period is active."""
    coord = hass.data[DOMAIN][setup_integration.entry_id]
    storage = coord._storage

    # Inject a fixed period covering the fixture date.
    period = FixedPeriod(
        id="fp1",
        label="Fixed",
        start_date=date(2026, 3, 13),
        end_date=date(2026, 3, 13),
        price=5.0,  # c/kWh
    )
    storage._periods = [period]

    result = await hass.services.async_call(
        DOMAIN,
        "get_active_prices",
        {"formatted": False},  # raw c/kWh
        blocking=True,
        return_response=True,
    )

    periods = result["price_periods"]
    assert len(periods) > 0
    # Every slot within the fixed period must report price == 5.0.
    fixed_day_slots = [p for p in periods if p["time"].startswith("2026-03-13")]
    for slot in fixed_day_slots:
        assert slot["price"] == pytest.approx(5.0)
        assert slot["is_fixed"] is True
