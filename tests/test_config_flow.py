"""Tests for Kilowahti config flow and options flow."""

from __future__ import annotations

import re

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    CONF_CONTROL_FACTOR_FUNCTION,
    CONF_CONTROL_FACTOR_SCALING,
    CONF_CURRENCY_MODE,
    CONF_DISPLAY_UNIT,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_FORWARD_AVG_HOURS,
    CONF_FX_MODE,
    CONF_FX_RATE,
    CONF_GENERATION_ENABLED,
    CONF_HIGH_PRECISION,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    CONF_REGION,
    CONF_SHOW_ROLLING_AVERAGES,
    CONF_VAT_RATE,
    CURRENCY_MODE_LOCAL,
    DEFAULT_CONTROL_FACTOR_FUNCTION,
    DEFAULT_CONTROL_FACTOR_SCALING,
    DEFAULT_EXPOSE_PRICE_ARRAYS,
    DEFAULT_FORWARD_AVG_HOURS,
    DEFAULT_GENERATION_ENABLED,
    DEFAULT_HIGH_PRECISION,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    DEFAULT_SHOW_ROLLING_AVERAGES,
    DOMAIN,
    FX_MODE_MANUAL,
    UNIT_SNTPERKWH,
)
from homeassistant.data_entry_flow import FlowResultType

from .conftest import CDN_PAYLOAD, TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

# ---------------------------------------------------------------------------
# Helpers to walk the multi-step config flow
# ---------------------------------------------------------------------------


async def _complete_config_flow(hass) -> dict:
    """Walk through all config flow steps with minimal valid input."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Test Home",
            CONF_REGION: "FI",
            CONF_PRICE_RESOLUTION: "60",
            CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
        },
    )
    assert result["step_id"] == "vat_and_tax"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "vat_rate_pct": 25.5,
            "electricity_tax": 2.253,
            "spot_commission": 0.0,
        },
    )
    assert result["step_id"] == "transfer_groups"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"action": "continue"},
    )
    assert result["step_id"] == "thresholds"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_MAX_PRICE: DEFAULT_MAX_PRICE,
            CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER: DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
            CONF_MAX_RANK: DEFAULT_MAX_RANK,
            CONF_FORWARD_AVG_HOURS: DEFAULT_FORWARD_AVG_HOURS,
            CONF_CONTROL_FACTOR_FUNCTION: DEFAULT_CONTROL_FACTOR_FUNCTION,
            CONF_CONTROL_FACTOR_SCALING: DEFAULT_CONTROL_FACTOR_SCALING,
        },
    )
    assert result["step_id"] == "score_profiles"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={},
    )
    assert result["step_id"] == "advanced_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_EXPOSE_PRICE_ARRAYS: DEFAULT_EXPOSE_PRICE_ARRAYS,
            CONF_GENERATION_ENABLED: DEFAULT_GENERATION_ENABLED,
            CONF_HIGH_PRECISION: DEFAULT_HIGH_PRECISION,
            CONF_SHOW_ROLLING_AVERAGES: DEFAULT_SHOW_ROLLING_AVERAGES,
        },
    )
    return result


async def test_config_flow_creates_entry_with_correct_options(hass, mock_utcnow):
    """Completing the config flow creates an entry with expected options."""
    await hass.config.async_set_time_zone("UTC")

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await _complete_config_flow(hass)
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    opts = result["options"]
    assert opts[CONF_REGION] == "FI"
    assert opts[CONF_PRICE_RESOLUTION] == 60
    assert opts[CONF_DISPLAY_UNIT] == UNIT_SNTPERKWH
    assert opts[CONF_VAT_RATE] == 0.255


# ---------------------------------------------------------------------------
# Region expansion (43 CDN zones)
# ---------------------------------------------------------------------------


def _region_selector_options(result) -> list[dict]:
    for key, sel in result["data_schema"].schema.items():
        if getattr(key, "schema", None) == CONF_REGION:
            return sel.config["options"]
    raise AssertionError("region selector not found in schema")


async def test_config_flow_offers_all_43_zones(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    options = _region_selector_options(result)

    assert len(options) == 43
    by_value = {o["value"]: o["label"] for o in options}
    assert by_value["FI"] == "FI — Finland"
    assert by_value["IT-NORD"] == "IT-NORD — Italy (North)"
    assert by_value["IE-SEM"] == "IE-SEM — Ireland (SEM)"


async def test_config_flow_cdn_only_zone_completes(hass, mock_utcnow):
    """A zone outside spot-hinta coverage completes the flow (CDN serves it)."""
    await hass.config.async_set_time_zone("UTC")

    pt_url = re.compile(r"https://cdn\.kilowahti\.fi/v1/pt/latest\.json")
    with aioresponses() as m:
        m.get(pt_url, payload=CDN_PAYLOAD, repeat=True)

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Test Home",
                CONF_REGION: "PT",
                CONF_PRICE_RESOLUTION: "60",
                CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
            },
        )
        assert result["step_id"] == "vat_and_tax"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"vat_rate_pct": 23.0, "electricity_tax": 0.001, "spot_commission": 0.0},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"action": "continue"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_MAX_PRICE: DEFAULT_MAX_PRICE,
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER: DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
                CONF_MAX_RANK: DEFAULT_MAX_RANK,
                CONF_FORWARD_AVG_HOURS: DEFAULT_FORWARD_AVG_HOURS,
                CONF_CONTROL_FACTOR_FUNCTION: DEFAULT_CONTROL_FACTOR_FUNCTION,
                CONF_CONTROL_FACTOR_SCALING: DEFAULT_CONTROL_FACTOR_SCALING,
            },
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_EXPOSE_PRICE_ARRAYS: DEFAULT_EXPOSE_PRICE_ARRAYS,
                CONF_GENERATION_ENABLED: DEFAULT_GENERATION_ENABLED,
                CONF_HIGH_PRECISION: DEFAULT_HIGH_PRECISION,
                CONF_SHOW_ROLLING_AVERAGES: DEFAULT_SHOW_ROLLING_AVERAGES,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_REGION] == "PT"


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_vat_change_no_reload(hass, setup_integration, mock_utcnow):
    """Changing VAT via options flow basic step does not reload the integration."""
    entry = setup_integration
    coord_before = hass.data[DOMAIN][entry.entry_id]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "basic"}
    )
    assert result["step_id"] == "basic"

    new_input = {
        "name": "Test Home",
        CONF_REGION: "FI",
        CONF_PRICE_RESOLUTION: "60",
        CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
        "vat_rate_pct": 10.0,  # Changed from 25.5%
        "electricity_tax": 2.253,
        "spot_commission": 0.0,
    }
    with aioresponses() as m:
        m.get(TODAY_URL_RE, status=404, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=new_input
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Same coordinator instance → no reload happened.
    coord_after = hass.data[DOMAIN].get(entry.entry_id)
    assert coord_after is coord_before
    assert entry.options[CONF_VAT_RATE] == 0.10


async def test_options_flow_region_change_triggers_reload(hass, setup_integration, mock_utcnow):
    """Changing region via options flow basic step triggers a full reload."""
    entry = setup_integration
    coord_before = hass.data[DOMAIN][entry.entry_id]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "basic"}
    )

    new_input = {
        "name": "Test Home",
        CONF_REGION: "EE",  # Changed from FI
        CONF_PRICE_RESOLUTION: "60",
        CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
        "vat_rate_pct": 25.5,
        "electricity_tax": 2.253,
        "spot_commission": 0.0,
    }
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=new_input
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # A new coordinator instance means the integration was reloaded.
    coord_after = hass.data[DOMAIN].get(entry.entry_id)
    assert coord_after is not None
    assert coord_after is not coord_before


# ---------------------------------------------------------------------------
# Currency step (CUR2)
# ---------------------------------------------------------------------------

SE1_CDN_URL = re.compile(r"https://cdn\.kilowahti\.fi/v1/se1/latest\.json")


async def test_config_flow_currency_step_for_non_eur_zone(hass, mock_utcnow):
    """SE1 shows the currency step; EUR zones skip it (covered by the FI flow test)."""
    await hass.config.async_set_time_zone("UTC")

    with aioresponses() as m:
        m.get(SE1_CDN_URL, payload=CDN_PAYLOAD, repeat=True)

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Test Home",
                CONF_REGION: "SE1",
                CONF_PRICE_RESOLUTION: "60",
                CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
            },
        )
        assert result["step_id"] == "currency"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CURRENCY_MODE: CURRENCY_MODE_LOCAL,
                CONF_FX_MODE: FX_MODE_MANUAL,
                CONF_FX_RATE: 11.0,
            },
        )
        assert result["step_id"] == "vat_and_tax"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"vat_rate_pct": 25.0, "electricity_tax": 0.439, "spot_commission": 0.0},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"action": "continue"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_MAX_PRICE: DEFAULT_MAX_PRICE,
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER: DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
                CONF_MAX_RANK: DEFAULT_MAX_RANK,
                CONF_FORWARD_AVG_HOURS: DEFAULT_FORWARD_AVG_HOURS,
                CONF_CONTROL_FACTOR_FUNCTION: DEFAULT_CONTROL_FACTOR_FUNCTION,
                CONF_CONTROL_FACTOR_SCALING: DEFAULT_CONTROL_FACTOR_SCALING,
            },
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_EXPOSE_PRICE_ARRAYS: DEFAULT_EXPOSE_PRICE_ARRAYS,
                CONF_GENERATION_ENABLED: DEFAULT_GENERATION_ENABLED,
                CONF_HIGH_PRECISION: DEFAULT_HIGH_PRECISION,
                CONF_SHOW_ROLLING_AVERAGES: DEFAULT_SHOW_ROLLING_AVERAGES,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    opts = result["options"]
    assert opts[CONF_CURRENCY_MODE] == CURRENCY_MODE_LOCAL
    assert opts[CONF_FX_MODE] == FX_MODE_MANUAL
    assert opts[CONF_FX_RATE] == 11.0


async def test_options_flow_currency_flip_converts_values(hass, options, mock_utcnow):
    """Flipping EUR → local multiplies stored currency-typed values by the rate."""
    await hass.config.async_set_time_zone("UTC")
    options = {
        **options,
        CONF_REGION: "SE1",
        CONF_MAX_PRICE: 5.0,
        "spot_commission": 0.5,
        "transfer_groups": [
            {
                "id": "g1",
                "label": "General",
                "active": True,
                "monthly_fixed_cost": 4.0,
                "tiers": [
                    {
                        "label": "Base",
                        "price": 3.0,
                        "months": list(range(1, 13)),
                        "weekdays": list(range(7)),
                        "hour_start": 0,
                        "hour_end": 24,
                        "priority": 100,
                    }
                ],
            }
        ],
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=options)
    with aioresponses() as m:
        m.get(SE1_CDN_URL, payload=CDN_PAYLOAD, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "basic"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "name": "Test Home",
                CONF_REGION: "SE1",
                CONF_PRICE_RESOLUTION: "60",
                CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
                "vat_rate_pct": 25.0,
                "electricity_tax": 0.439,
                "spot_commission": 0.5,
                CONF_CURRENCY_MODE: CURRENCY_MODE_LOCAL,
                CONF_FX_MODE: FX_MODE_MANUAL,
                CONF_FX_RATE: 10.0,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MAX_PRICE] == 50.0
    assert entry.options["spot_commission"] == 5.0
    group = entry.options["transfer_groups"][0]
    assert group["monthly_fixed_cost"] == 40.0
    assert group["tiers"][0]["price"] == 30.0


async def test_options_flow_enables_total_price_arrays(hass, setup_integration, mock_utcnow):
    """The advanced options step stores the total-price array toggle."""
    from custom_components.kilowahti.const import CONF_EXPOSE_TOTAL_PRICE_ARRAYS

    entry = setup_integration

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_options"}
    )
    assert result["step_id"] == "advanced_options"

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_EXPOSE_PRICE_ARRAYS: False,
                CONF_EXPOSE_TOTAL_PRICE_ARRAYS: True,
                CONF_GENERATION_ENABLED: DEFAULT_GENERATION_ENABLED,
                CONF_HIGH_PRECISION: DEFAULT_HIGH_PRECISION,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_EXPOSE_TOTAL_PRICE_ARRAYS] is True

    coord = hass.data[DOMAIN][entry.entry_id]
    assert coord.today_total_price_array() is not None
    assert coord.today_price_array() is None


# ---------------------------------------------------------------------------
# Transfer tier editing
# ---------------------------------------------------------------------------

_TIER = {
    "label": "Winter weekday",
    "price": 5.2,
    "months": [12, 1, 2],
    "weekdays": [0, 1, 2, 3, 4],
    "hour_start": 7,
    "hour_end": 22,
    "priority": 10,
}

_GROUP = {
    "id": "g1",
    "label": "Time-of-use",
    "active": True,
    "tiers": [_TIER],
    "monthly_fixed_cost": 0.0,
}


async def _open_group_detail(hass, entry):
    """Walk the options flow to the detail step of the first transfer group."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "transfer_groups"}
    )
    assert result["step_id"] == "transfer_groups"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "manage_0"}
    )
    assert result["step_id"] == "transfer_group_detail"
    assert CONF_TRANSFER_GROUPS  # imported for readability of the caller's asserts
    return result


@pytest.fixture
async def entry_with_tier(hass, options, mock_utcnow):
    """An entry whose active transfer group already holds one tier."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options={**options, CONF_TRANSFER_GROUPS: [{**_GROUP, "tiers": [dict(_TIER)]}]},
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_group_detail_lists_tier_settings(hass, entry_with_tier):
    """The detail step shows each tier's price and schedule, not just its name."""
    result = await _open_group_detail(hass, entry_with_tier)

    tier_list = result["description_placeholders"]["tier_list"]
    assert "Winter weekday" in tier_list
    assert "5.2" in tier_list
    assert "07:00" in tier_list
    assert "22:00" in tier_list


async def test_edit_tier_form_is_prefilled(hass, entry_with_tier):
    """Opening a tier for editing pre-fills every field from the stored tier."""
    result = await _open_group_detail(hass, entry_with_tier)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "edit_tier_0"}
    )
    assert result["step_id"] == "edit_transfer_tier"

    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["label"] == "Winter weekday"
    assert defaults["price"] == 5.2
    assert defaults["hour_start"] == 7
    assert defaults["hour_end"] == 22
    assert defaults["priority"] == 10
    # Multi-selects round-trip as strings
    assert defaults["months"] == ["12", "1", "2"]
    assert defaults["weekdays"] == ["0", "1", "2", "3", "4"]


async def test_edit_tier_saves_changes(hass, entry_with_tier):
    """Editing a tier updates it in place rather than appending a new one."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    entry = entry_with_tier
    result = await _open_group_detail(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "edit_tier_0"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "label": "Winter weekday",
            "price": 6.5,
            "months": ["12", "1", "2"],
            "weekdays": ["0", "1", "2", "3", "4"],
            "hour_start": 7,
            "hour_end": 21,
            "priority": 10,
            "delete": False,
        },
    )
    assert result["step_id"] == "transfer_group_detail"

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "back"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "save"}
        )
        await hass.async_block_till_done()

    tiers = entry.options[CONF_TRANSFER_GROUPS][0]["tiers"]
    assert len(tiers) == 1
    assert tiers[0]["price"] == 6.5
    assert tiers[0]["hour_end"] == 21
    assert tiers[0]["months"] == [12, 1, 2]


async def test_edit_tier_delete_removes_it(hass, entry_with_tier):
    """Ticking delete in the edit form removes the tier."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    entry = entry_with_tier
    result = await _open_group_detail(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "edit_tier_0"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "label": "Winter weekday",
            "price": 5.2,
            "months": ["12", "1", "2"],
            "weekdays": ["0", "1", "2", "3", "4"],
            "hour_start": 7,
            "hour_end": 22,
            "priority": 10,
            "delete": True,
        },
    )
    assert result["step_id"] == "transfer_group_detail"
    assert result["description_placeholders"]["tier_list"] != ""

    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "back"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "save"}
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_TRANSFER_GROUPS][0]["tiers"] == []


async def test_edit_tier_rejects_invalid_hours(hass, entry_with_tier):
    """The edit form validates the hour range like the add form does."""
    result = await _open_group_detail(hass, entry_with_tier)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "edit_tier_0"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "label": "Winter weekday",
            "price": 5.2,
            "months": ["12"],
            "weekdays": ["0"],
            "hour_start": 20,
            "hour_end": 20,
            "priority": 10,
            "delete": False,
        },
    )
    assert result["step_id"] == "edit_transfer_tier"
    assert result["errors"]["base"] == "tier_hour_range"


# ---------------------------------------------------------------------------
# Currency-aware entry units
# ---------------------------------------------------------------------------


def _field(schema, name):
    for key, validator in schema.schema.items():
        if str(key) == name:
            return key, validator
    raise AssertionError(f"{name} not in schema")


def _unit_of(schema, name):
    return _field(schema, name)[1].config.get("unit_of_measurement")


def _default_of(schema, name):
    return _field(schema, name)[0].default()


def test_units_follow_configured_currency():
    """Entry units come from the region's currency when local mode is on."""
    from custom_components.kilowahti.config_flow import _units_for

    eur = _units_for({CONF_REGION: "FI"})
    assert (eur.per_kwh, eur.per_month, eur.major_scale) == ("c/kWh", "€/month", False)

    sek = _units_for({CONF_REGION: "SE1", CONF_CURRENCY_MODE: "local"})
    assert (sek.per_kwh, sek.per_month, sek.major_scale) == ("öre/kWh", "kr/month", False)

    # EUR mode on a non-EUR zone keeps euro units
    sek_as_eur = _units_for({CONF_REGION: "SE1", CONF_CURRENCY_MODE: "eur"})
    assert sek_as_eur.per_kwh == "c/kWh"


def test_units_for_currency_without_minor_unit():
    """CZK has no minor unit, so entry switches to the major scale."""
    from custom_components.kilowahti.config_flow import _units_for

    czk = _units_for({CONF_REGION: "CZ", CONF_CURRENCY_MODE: "local"})
    assert (czk.per_kwh, czk.per_month, czk.major_scale) == ("Kč/kWh", "Kč/month", True)


def test_tier_schema_scales_to_major_unit():
    """A tier stored on the minor scale is shown in the major unit."""
    from custom_components.kilowahti.config_flow import _add_tier_schema, _units_for

    czk = _units_for({CONF_REGION: "CZ", CONF_CURRENCY_MODE: "local"})
    schema = _add_tier_schema({"price": 450.0}, units=czk)
    assert _unit_of(schema, "price") == "Kč/kWh"
    assert _default_of(schema, "price") == 4.5

    eur = _units_for({CONF_REGION: "FI"})
    schema = _add_tier_schema({"price": 5.2}, units=eur)
    assert _unit_of(schema, "price") == "c/kWh"
    assert _default_of(schema, "price") == 5.2


def test_monthly_cost_is_never_scaled():
    """Monthly fixed cost is stored in major units already."""
    from custom_components.kilowahti.config_flow import _group_settings_schema, _units_for

    czk = _units_for({CONF_REGION: "CZ", CONF_CURRENCY_MODE: "local"})
    schema = _group_settings_schema({"monthly_fixed_cost": 300.0}, units=czk)
    assert _unit_of(schema, "monthly_fixed_cost") == "Kč/month"
    assert _default_of(schema, "monthly_fixed_cost") == 300.0


async def test_threshold_field_carries_currency_unit(hass, setup_integration):
    """The thresholds step labels max price with the configured unit."""
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "thresholds"}
    )
    assert _unit_of(result["data_schema"], CONF_MAX_PRICE) == "c/kWh"


async def test_tier_edit_round_trips_major_scale(hass, options, mock_utcnow):
    """On a Kč zone the tier form works in Kč and stores hundredths."""
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS

    await hass.config.async_set_time_zone("UTC")
    tier = {**_TIER, "price": 450.0}
    opts = {
        **options,
        CONF_REGION: "CZ",
        CONF_CURRENCY_MODE: "local",
        CONF_FX_MODE: "manual",
        CONF_FX_RATE: 24.7,
        CONF_TRANSFER_GROUPS: [{**_GROUP, "tiers": [tier]}],
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test Home", options=opts)
    cdn_re = re.compile(r"https://cdn\.kilowahti\.fi/v1/cz/latest\.json")
    with aioresponses() as m:
        m.get(cdn_re, payload=CDN_PAYLOAD, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await _open_group_detail(hass, entry)
    assert "4.5 Kč/kWh" in result["description_placeholders"]["tier_list"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "edit_tier_0"}
    )
    assert _unit_of(result["data_schema"], "price") == "Kč/kWh"
    assert _default_of(result["data_schema"], "price") == 4.5

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "label": "Winter weekday",
            "price": 5.0,
            "months": ["12", "1", "2"],
            "weekdays": ["0", "1", "2", "3", "4"],
            "hour_start": 7,
            "hour_end": 22,
            "priority": 10,
            "delete": False,
        },
    )
    with aioresponses() as m:
        m.get(cdn_re, payload=CDN_PAYLOAD, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "back"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "save"}
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_TRANSFER_GROUPS][0]["tiers"][0]["price"] == 500.0


async def test_editing_a_tier_updates_sensors_without_reload(hass, options, mock_utcnow):
    """Editing a tier price refreshes entities immediately.

    The options flow must hand Home Assistant genuinely new option data;
    mutating the stored group dicts in place leaves entry.options comparing
    equal, so no update listener fires and entities keep the old price until
    some unrelated event refreshes them.
    """
    from custom_components.kilowahti.const import CONF_TRANSFER_GROUPS, SENSOR_TRANSFER_PRICE
    from homeassistant.helpers import entity_registry as er

    flat_tier = {
        "label": "Flat",
        "price": 3.0,
        "months": list(range(1, 13)),
        "weekdays": list(range(7)),
        "hour_start": 0,
        "hour_end": 24,
        "priority": 10,
    }
    await hass.config.async_set_time_zone("UTC")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options={**options, CONF_TRANSFER_GROUPS: [{**_GROUP, "tiers": [flat_tier]}]},
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{SENSOR_TRANSFER_PRICE}"
    )
    assert float(hass.states.get(entity_id).state) == 3.0

    result = await _open_group_detail(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"action": "edit_tier_0"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "label": "Flat",
            "price": 6.0,
            "months": [str(m2) for m2 in range(1, 13)],
            "weekdays": [str(w) for w in range(7)],
            "hour_start": 0,
            "hour_end": 24,
            "priority": 10,
            "delete": False,
        },
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "back"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"action": "save"}
        )
        await hass.async_block_till_done()

    assert float(hass.states.get(entity_id).state) == 6.0
