"""Tests for Kilowahti config flow and options flow."""

from __future__ import annotations

from aioresponses import aioresponses

from custom_components.kilowahti.const import (
    CONF_CONTROL_FACTOR_FUNCTION,
    CONF_CONTROL_FACTOR_SCALING,
    CONF_DISPLAY_UNIT,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_FORWARD_AVG_HOURS,
    CONF_HIGH_PRECISION,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    CONF_REGION,
    CONF_VAT_RATE,
    DEFAULT_CONTROL_FACTOR_FUNCTION,
    DEFAULT_CONTROL_FACTOR_SCALING,
    DEFAULT_EXPOSE_PRICE_ARRAYS,
    DEFAULT_FORWARD_AVG_HOURS,
    DEFAULT_HIGH_PRECISION,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    DOMAIN,
    UNIT_SNTPERKWH,
)
from homeassistant.data_entry_flow import FlowResultType

from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE

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
    assert result["step_id"] == "sensor_display"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_EXPOSE_PRICE_ARRAYS: DEFAULT_EXPOSE_PRICE_ARRAYS,
            CONF_HIGH_PRECISION: DEFAULT_HIGH_PRECISION,
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
