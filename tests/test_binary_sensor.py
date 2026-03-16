"""Tests for Kilowahti binary sensor entities."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from aioresponses import aioresponses

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    BINARY_SENSOR_CHARGE_FROM_GRID_RECOMMENDED,
    BINARY_SENSOR_DISCHARGE_TO_GRID_RECOMMENDED,
    BINARY_SENSOR_EXPORT_PRICE_ACCEPTABLE,
    BINARY_SENSOR_PRICE_ACCEPTABLE,
    BINARY_SENSOR_RANK_ACCEPTABLE,
    BINARY_SENSOR_TOMORROW_AVAILABLE,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_GENERATION_ENABLED,
    DOMAIN,
)
from homeassistant.helpers import entity_registry as er

from .conftest import TODAY_URL_RE, TOMORROW_PAYLOAD, TOMORROW_URL_RE


def _entity_id(hass, entry_id: str, key: str) -> str | None:
    ent_reg = er.async_get(hass)
    return ent_reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry_id}_{key}")


async def test_price_acceptable_true_when_below_threshold(hass, setup_integration, mock_utcnow):
    """price_acceptable is on when effective price <= max_price threshold.

    Slot at 00:00: spot_effective = 3.765 c/kWh; default max_price = 20 c/kWh.
    """
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, BINARY_SENSOR_PRICE_ACCEPTABLE)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


async def test_price_acceptable_false_when_above_threshold(hass, setup_integration, mock_utcnow):
    """price_acceptable is off when effective price > max_price threshold."""
    entry = setup_integration
    coord = hass.data[DOMAIN][entry.entry_id]

    # Lower the threshold below the current spot price (3.765 c/kWh).
    coord.set_price_threshold(2.0)
    coord.async_update_listeners()
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, entry.entry_id, BINARY_SENSOR_PRICE_ACCEPTABLE)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"


async def test_rank_acceptable_true_when_below_max_rank(hass, setup_integration, mock_utcnow):
    """rank_acceptable is on when current rank <= max_rank.

    Slot at 00:00 has rank 1; default max_rank = 24.
    """
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, BINARY_SENSOR_RANK_ACCEPTABLE)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


async def test_tomorrow_available_transitions_on_eager_fetch(hass, setup_integration):
    """tomorrow_available transitions False → True when tomorrow slots are fetched."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, BINARY_SENSOR_TOMORROW_AVAILABLE)
    assert entity_id is not None

    # Initially False — DayForward returned 404 during setup.
    state = hass.states.get(entity_id)
    assert state.state == "off"

    # Simulate successful eager poll.
    coord = hass.data[DOMAIN][entry.entry_id]
    eager_time = datetime(2026, 3, 13, 15, 0, 0, tzinfo=timezone.utc)
    with patch("homeassistant.util.dt.utcnow", return_value=eager_time):
        with aioresponses() as m:
            m.get(TOMORROW_URL_RE, payload=TOMORROW_PAYLOAD)
            await coord._async_eager_poll()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "on"


# ---------------------------------------------------------------------------
# Generation / battery sensor gating
# ---------------------------------------------------------------------------


async def test_export_price_acceptable_absent_when_generation_disabled(
    hass, options, mock_utcnow
):
    """export_price_acceptable binary sensor is not registered when generation_enabled=False."""
    from .conftest import TODAY_PAYLOAD

    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, entry.entry_id, BINARY_SENSOR_EXPORT_PRICE_ACCEPTABLE)
    assert entity_id is None


async def test_charge_discharge_sensors_absent_when_no_battery(
    hass, options, mock_utcnow
):
    """charge/discharge binary sensors are not registered when generation_enabled=True but battery_capacity=0."""
    gen_options = {**options, CONF_GENERATION_ENABLED: True}
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=gen_options)

    from .conftest import TODAY_PAYLOAD

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # export_price_acceptable SHOULD be present (generation enabled).
    assert _entity_id(hass, entry.entry_id, BINARY_SENSOR_EXPORT_PRICE_ACCEPTABLE) is not None
    # Charge/discharge should NOT be present (battery_capacity_kwh = 0.0).
    assert _entity_id(hass, entry.entry_id, BINARY_SENSOR_CHARGE_FROM_GRID_RECOMMENDED) is None
    assert _entity_id(hass, entry.entry_id, BINARY_SENSOR_DISCHARGE_TO_GRID_RECOMMENDED) is None


async def test_charge_discharge_sensors_present_with_battery(
    hass, options, mock_utcnow
):
    """charge/discharge binary sensors are registered when generation_enabled=True and battery_capacity>0."""
    gen_options = {
        **options,
        CONF_GENERATION_ENABLED: True,
        CONF_BATTERY_CAPACITY_KWH: 10.0,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=gen_options)

    from .conftest import TODAY_PAYLOAD

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _entity_id(hass, entry.entry_id, BINARY_SENSOR_EXPORT_PRICE_ACCEPTABLE) is not None
    assert _entity_id(hass, entry.entry_id, BINARY_SENSOR_CHARGE_FROM_GRID_RECOMMENDED) is not None
    assert _entity_id(hass, entry.entry_id, BINARY_SENSOR_DISCHARGE_TO_GRID_RECOMMENDED) is not None
