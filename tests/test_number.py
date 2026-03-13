"""Tests for Kilowahti number entities (price_threshold, rank_threshold)."""

from __future__ import annotations

import pytest

from custom_components.kilowahti.const import (
    CONF_MAX_PRICE,
    CONF_PRICE_RESOLUTION,
    DOMAIN,
    NUMBER_PRICE_THRESHOLD,
    NUMBER_RANK_THRESHOLD,
    UNIT_EUROKWH,
)
from homeassistant.helpers import entity_registry as er


def _entity_id(hass, entry_id: str, key: str) -> str | None:
    ent_reg = er.async_get(hass)
    return ent_reg.async_get_entity_id("number", DOMAIN, f"{entry_id}_number_{key}")


async def test_price_threshold_set_value_persists(hass, setup_integration, mock_utcnow):
    """Setting price_threshold number entity writes through to entry.options."""
    entry = setup_integration
    entity_id = _entity_id(hass, entry.entry_id, NUMBER_PRICE_THRESHOLD)
    assert entity_id is not None

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 7.5},
        blocking=True,
    )

    coord = hass.data[DOMAIN][entry.entry_id]
    assert coord._max_price_value == pytest.approx(7.5)
    assert entry.options[CONF_MAX_PRICE] == pytest.approx(7.5)


async def test_rank_threshold_max_value_hourly(hass, setup_integration, mock_utcnow):
    """rank_threshold max_value is 24 for HOUR (60-min) resolution."""
    entry = setup_integration
    assert entry.options[CONF_PRICE_RESOLUTION] == 60

    entity_id = _entity_id(hass, entry.entry_id, NUMBER_RANK_THRESHOLD)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.attributes["max"]) == 24.0


async def test_rank_threshold_max_value_15min(hass, options, mock_utcnow):
    """rank_threshold max_value is 96 for 15-min resolution."""
    from aioresponses import aioresponses
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    await hass.config.async_set_time_zone("UTC")
    options_15 = dict(options)
    options_15[CONF_PRICE_RESOLUTION] = 15
    entry = MockConfigEntry(domain=DOMAIN, title="Test 15min", options=options_15)

    # Need a 96-slot fixture; reuse the 3-slot fixture (coord will just have 3 slots, not 96)
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, entry.entry_id, NUMBER_RANK_THRESHOLD)
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.attributes["max"]) == 96.0


async def test_price_threshold_euro_unit_conversion(hass, options, mock_utcnow):
    """price_threshold set_value converts from €/kWh input to c/kWh internally."""
    from aioresponses import aioresponses
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    await hass.config.async_set_time_zone("UTC")
    options_eur = dict(options)
    options_eur["display_unit"] = UNIT_EUROKWH
    entry = MockConfigEntry(domain=DOMAIN, title="Test EUR", options=options_eur)

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, entry.entry_id, NUMBER_PRICE_THRESHOLD)
    assert entity_id is not None

    # Set 0.075 €/kWh → should be stored as 7.5 c/kWh internally.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 0.075},
        blocking=True,
    )

    coord = hass.data[DOMAIN][entry.entry_id]
    assert coord._max_price_value == pytest.approx(7.5)
