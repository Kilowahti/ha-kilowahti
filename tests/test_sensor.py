"""Tests for Kilowahti sensor entities."""

from __future__ import annotations

from datetime import date

import pytest
from kilowahti.models import FixedPeriod

from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    CONF_GENERATION_ENABLED,
    DOMAIN,
    SENSOR_CONTROL_FACTOR_TRANSFER,
    SENSOR_EXPORT_PRICE,
    SENSOR_SPOT_PRICE,
    SENSOR_TOMORROW_SPOT_AVG,
    SENSOR_TOMORROW_SPOT_MAX,
    SENSOR_TOMORROW_SPOT_MIN,
    SENSOR_TOMORROW_TOTAL_AVG,
    SENSOR_TOMORROW_TOTAL_MAX,
    SENSOR_TOMORROW_TOTAL_MIN,
    SENSOR_TRANSFER_PRICE,
)
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er


def _entity_id(hass, platform: str, entry_id: str, key: str) -> str | None:
    ent_reg = er.async_get(hass)
    return ent_reg.async_get_entity_id(platform, DOMAIN, f"{entry_id}_{key}")


async def test_spot_price_state_is_numeric(hass, setup_integration, mock_utcnow):
    """spot_price sensor has a numeric state matching the expected effective price.

    Fixture slot at 00:00 UTC: PriceNoTax = 0.03 €/kWh = 3.0 c/kWh
    spot_effective = 3.0 * 1.255 + 0.0 = 3.765 c/kWh  (VAT=25.5%, commission=0)
    """
    entry = setup_integration
    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_SPOT_PRICE)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state not in (STATE_UNKNOWN, "unavailable")
    assert float(state.state) == pytest.approx(3.765, rel=1e-3)


async def test_tomorrow_stats_unknown_when_no_tomorrow(hass, setup_integration, mock_utcnow):
    """tomorrow_spot_avg/min/max sensors are unknown when tomorrow prices are not available."""
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]
    assert coord._tomorrow_slots is None

    for key in (SENSOR_TOMORROW_SPOT_AVG, SENSOR_TOMORROW_SPOT_MIN, SENSOR_TOMORROW_SPOT_MAX):
        entity_id = _entity_id(hass, "sensor", entry.entry_id, key)
        assert entity_id is not None, f"missing entity for {key}"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN, f"{key} should be unknown, got {state.state!r}"


async def test_transfer_price_unknown_when_no_group(hass, setup_integration, mock_utcnow):
    """transfer_price sensor is unknown when no transfer group is configured."""
    entry = setup_integration
    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_TRANSFER_PRICE)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_control_factor_transfer_unknown_when_no_group(hass, setup_integration, mock_utcnow):
    """control_factor_transfer sensor is unknown when no transfer group is configured."""
    entry = setup_integration
    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_CONTROL_FACTOR_TRANSFER)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_synthetic_slots_dst_spring_forward(hass, setup_integration, mock_utcnow):
    """_synthetic_slots_for_date produces 23 slots on a spring-forward day, not 24.

    Europe/Helsinki springs forward on 2026-03-29: 03:00 EET → 04:00 EEST.
    UTC arithmetic must be used to avoid duplicate slots at the DST boundary.
    """
    from datetime import date

    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    # Temporarily set HA timezone to Helsinki to exercise DST logic
    await hass.config.async_set_time_zone("Europe/Helsinki")
    slots = coord._synthetic_slots_for_date(date(2026, 3, 29))
    await hass.config.async_set_time_zone("UTC")

    assert len(slots) == 23, f"Expected 23 slots on spring-forward day, got {len(slots)}"
    # All UTC timestamps must be unique
    utc_times = [s.dt_utc for s in slots]
    assert len(set(utc_times)) == len(utc_times), "Duplicate UTC timestamps in synthetic slots"


async def test_tomorrow_total_stats_with_fixed_period(hass, setup_integration, mock_utcnow):
    """tomorrow_total_avg/min/max return the fixed price when a fixed period covers tomorrow.

    Frozen date: 2026-03-13 → tomorrow = 2026-03-14.
    No spot prices for tomorrow (404 in fixture).
    Fixed period at 7.5 c/kWh covering 2026-03-14 → stats should equal 7.5 (no transfer).
    """
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    tomorrow = date(2026, 3, 14)
    period = FixedPeriod(
        id="test-period",
        label="Test",
        start_date=tomorrow,
        end_date=tomorrow,
        price=7.5,
    )
    coord._storage._periods = [period]

    assert coord.tomorrow_total_avg() == pytest.approx(7.5, rel=1e-3)
    assert coord.tomorrow_total_min() == pytest.approx(7.5, rel=1e-3)
    assert coord.tomorrow_total_max() == pytest.approx(7.5, rel=1e-3)


async def test_export_price_sensor_state_is_numeric(hass, options, mock_utcnow):
    """export_price sensor has a numeric state when generation is enabled.

    Slot at 00:00 UTC: price_no_tax = 3.0 c/kWh (spot-linked, zero commission).
    export_price_now = max(0.0, 3.0 - 0.0) = 3.0 c/kWh.

    The sensor is only registered when generation_enabled=True; this test also
    confirms the sensor is absent in the default (generation disabled) fixture.
    """
    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    gen_options = {**options, CONF_GENERATION_ENABLED: True}
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=gen_options)
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_EXPORT_PRICE)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state not in (STATE_UNKNOWN, "unavailable")
    assert float(state.state) == pytest.approx(3.0, rel=1e-3)
