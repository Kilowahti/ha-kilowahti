"""Tests for Kilowahti sensor entities."""

from __future__ import annotations

from datetime import date

import pytest
from aioresponses import aioresponses
from kilowahti.models import FixedPeriod
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


async def test_price_data_source_sensor(hass, setup_integration, mock_utcnow):
    """Diagnostic sensor reports the serving source and the failover timestamp.

    setup_integration mocks only spot-hinta, so the CDN primary fails and the
    chain records a failover to spot_hinta.
    """
    entry = setup_integration
    entity_id = _entity_id(hass, "sensor", entry.entry_id, "price_data_source")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "spot_hinta"
    assert state.attributes["last_failover"] is not None


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


async def test_exchange_rate_sensor_only_in_local_mode(hass, options, mock_utcnow):
    """exchange_rate diagnostic sensor exists only for local-currency entries."""
    import re as _re

    from aioresponses import aioresponses
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.kilowahti.const import (
        CONF_CURRENCY_MODE,
        CONF_FX_MODE,
        CONF_FX_RATE,
        CONF_REGION,
    )

    from .conftest import CDN_PAYLOAD

    await hass.config.async_set_time_zone("UTC")
    opts = {
        **options,
        CONF_REGION: "SE1",
        CONF_CURRENCY_MODE: "local",
        CONF_FX_MODE: "manual",
        CONF_FX_RATE: 11.0,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=opts)
    with aioresponses() as m:
        m.get(
            _re.compile(r"https://cdn\.kilowahti\.fi/v1/se1/latest\.json"),
            payload=CDN_PAYLOAD,
            repeat=True,
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", entry.entry_id, "exchange_rate")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert float(state.state) == 11.0
    assert state.attributes["fx_mode"] == "manual"
    assert state.attributes["unit_of_measurement"] == "SEK/EUR"


async def test_total_price_sensor_exposes_arrays_when_enabled(hass, options, mock_utcnow):
    """With the option on, total_price carries today_prices with the breakdown."""
    from custom_components.kilowahti.const import (
        CONF_EXPOSE_TOTAL_PRICE_ARRAYS,
        SENSOR_TOTAL_PRICE,
    )

    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options={**options, CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True},
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_TOTAL_PRICE)
    state = hass.states.get(entity_id)
    assert "today_prices" in state.attributes
    assert set(state.attributes["today_prices"][0]) == {
        "time",
        "energy",
        "transfer",
        "price",
        "rank",
    }
    # Tomorrow has not been fetched yet
    assert "tomorrow_prices" not in state.attributes


async def test_price_arrays_are_excluded_from_recording(hass, setup_integration, mock_utcnow):
    """Both array-carrying sensors keep their arrays out of the recorder."""
    from custom_components.kilowahti.const import SENSOR_TOTAL_PRICE

    for key in (SENSOR_SPOT_PRICE, SENSOR_TOTAL_PRICE):
        entity_id = _entity_id(hass, "sensor", setup_integration.entry_id, key)
        state = hass.states.get(entity_id)
        assert {"today_prices", "tomorrow_prices"} <= state.state_info["unrecorded_attributes"]


async def test_total_price_sensor_has_no_arrays_by_default(hass, setup_integration, mock_utcnow):
    """The option is off by default, so total_price carries no array attributes."""
    from custom_components.kilowahti.const import SENSOR_TOTAL_PRICE

    entity_id = _entity_id(hass, "sensor", setup_integration.entry_id, SENSOR_TOTAL_PRICE)
    state = hass.states.get(entity_id)
    assert "today_prices" not in state.attributes
    assert "tomorrow_prices" not in state.attributes


async def test_monthly_fixed_cost_unit_follows_currency(hass, options, mock_utcnow):
    """The monthly fixed cost sensor reports the configured currency, not €."""
    import re as _re2

    from custom_components.kilowahti.const import (
        CONF_CURRENCY_MODE,
        CONF_FX_MODE,
        CONF_FX_RATE,
        CONF_MONTHLY_FIXED_COST,
        CONF_REGION,
        SENSOR_MONTHLY_FIXED_COST_TODAY,
    )

    from .conftest import CDN_PAYLOAD

    await hass.config.async_set_time_zone("UTC")
    opts = {
        **options,
        CONF_REGION: "SE1",
        CONF_CURRENCY_MODE: "local",
        CONF_FX_MODE: "manual",
        CONF_FX_RATE: 11.0,
        CONF_MONTHLY_FIXED_COST: 250.0,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=opts)
    with aioresponses() as m:
        m.get(
            _re2.compile(r"https://cdn\.kilowahti\.fi/v1/se1/latest\.json"),
            payload=CDN_PAYLOAD,
            repeat=True,
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_MONTHLY_FIXED_COST_TODAY)
    state = hass.states.get(entity_id)
    assert state.attributes["unit_of_measurement"] == "kr"


async def test_transfer_price_sensor_labels_group_and_tier(hass, options, mock_utcnow):
    """The transfer price sensor names the group and tier it is currently priced from."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    group = {
        "id": "g1",
        "label": "Kausisiirto",
        "active": True,
        "tiers": [
            {
                "label": "Talviarkipäivä",
                "price": 7.0,
                "months": [12, 1, 2],
                "weekdays": [0, 1, 2, 3, 4],
                "hour_start": 7,
                "hour_end": 22,
                "priority": 1,
            },
            {
                "label": "Muu aika",
                "price": 3.0,
                "months": list(range(1, 13)),
                "weekdays": list(range(7)),
                "hour_start": 0,
                "hour_end": 24,
                "priority": 2,
            },
        ],
        "monthly_fixed_cost": 0.0,
    }
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test Home", options={**options, CONF_TRANSFER_GROUPS: [group]}
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_TRANSFER_PRICE)
    state = hass.states.get(entity_id)
    # Frozen at 00:30 on a Friday in March — the catch-all tier is the match
    assert float(state.state) == 3.0
    assert state.attributes["tariff"] == "Kausisiirto, Muu aika"
    assert state.attributes["group"] == "Kausisiirto"
    assert state.attributes["tier"] == "Muu aika"


async def test_transfer_price_sensor_label_without_group(hass, setup_integration, mock_utcnow):
    """With no transfer group configured every naming attribute is None."""
    entity_id = _entity_id(hass, "sensor", setup_integration.entry_id, SENSOR_TRANSFER_PRICE)
    state = hass.states.get(entity_id)
    assert state.attributes["tariff"] is None
    assert state.attributes["group"] is None
    assert state.attributes["tier"] is None


async def test_control_factor_sensors_cover_price_total_and_transfer(hass, options, mock_utcnow):
    """All three control factors exist in unipolar and bipolar form."""
    from custom_components.kilowahti.const import (
        CONF_TRANSFER_GROUPS,
        SENSOR_CONTROL_FACTOR_PRICE,
        SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR,
        SENSOR_CONTROL_FACTOR_TOTAL,
        SENSOR_CONTROL_FACTOR_TOTAL_BIPOLAR,
        SENSOR_CONTROL_FACTOR_TRANSFER_BIPOLAR,
    )

    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    group = {
        "id": "g1",
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

    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options={**options, CONF_TRANSFER_GROUPS: [group]},
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Frozen time is 00:30 — cheapest spot slot and the cheaper transfer tier
    for key in (
        SENSOR_CONTROL_FACTOR_PRICE,
        SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR,
        SENSOR_CONTROL_FACTOR_TOTAL,
        SENSOR_CONTROL_FACTOR_TOTAL_BIPOLAR,
        SENSOR_CONTROL_FACTOR_TRANSFER,
        SENSOR_CONTROL_FACTOR_TRANSFER_BIPOLAR,
    ):
        entity_id = _entity_id(hass, "sensor", entry.entry_id, key)
        assert entity_id is not None, f"missing entity for {key}"
        state = hass.states.get(entity_id)
        assert float(state.state) == 1.0, f"{key} should read 1.0, got {state.state!r}"


async def test_control_factor_transfer_keeps_the_tier_count_attribute(hass, options, mock_utcnow):
    """The transfer factor still reports how many distinct tiers occur today."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

    group = {
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

    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options={**options, CONF_TRANSFER_GROUPS: [group]},
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", entry.entry_id, SENSOR_CONTROL_FACTOR_TRANSFER)
    state = hass.states.get(entity_id)
    assert state.attributes["tier_count"] == 1
    assert float(state.state) == 1.0
