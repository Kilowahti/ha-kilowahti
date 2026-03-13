"""Shared fixtures for Kilowahti integration tests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import (
    CONF_CONTROL_FACTOR_FUNCTION,
    CONF_CONTROL_FACTOR_SCALING,
    CONF_DISPLAY_UNIT,
    CONF_EAGER_END_HOUR,
    CONF_EAGER_START_HOUR,
    CONF_ELECTRICITY_TAX,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_FORWARD_AVG_HOURS,
    CONF_HIGH_PRECISION,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_SPOT_COMMISSION,
    CONF_TRANSFER_GROUPS,
    CONF_VAT_RATE,
    DEFAULT_CONTROL_FACTOR_FUNCTION,
    DEFAULT_CONTROL_FACTOR_SCALING,
    DEFAULT_EAGER_END_HOUR,
    DEFAULT_EAGER_START_HOUR,
    DEFAULT_ELECTRICITY_TAX,
    DEFAULT_EXPOSE_PRICE_ARRAYS,
    DEFAULT_FORWARD_AVG_HOURS,
    DEFAULT_HIGH_PRECISION,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    DEFAULT_SPOT_COMMISSION,
    DEFAULT_VAT_RATE,
    DOMAIN,
    UNIT_SNTPERKWH,
)

# ---------------------------------------------------------------------------
# Constants used across tests
# ---------------------------------------------------------------------------

#: Frozen moment: 2026-03-13 00:30 UTC — 30 min into the cheapest slot (rank 1)
FROZEN_UTC = datetime(2026, 3, 13, 0, 30, 0, tzinfo=timezone.utc)
FROZEN_DATE = FROZEN_UTC.date()

TODAY_URL_RE = re.compile(r"https://api\.spot-hinta\.fi/Today")
TOMORROW_URL_RE = re.compile(r"https://api\.spot-hinta\.fi/DayForward")

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    return json.loads((_FIXTURE_DIR / name).read_text())


TODAY_PAYLOAD = load_fixture("spot_hinta_today_fi.json")
TOMORROW_PAYLOAD = load_fixture("spot_hinta_tomorrow_fi.json")


# ---------------------------------------------------------------------------
# Enable custom integrations
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading of the kilowahti custom component in tests."""
    yield


# ---------------------------------------------------------------------------
# Options / config entry fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def options():
    """Full options dict using HOUR resolution and FI region defaults."""
    return {
        "name": "Test Home",
        CONF_REGION: "FI",
        CONF_PRICE_RESOLUTION: 60,  # HOUR — 24 slots/day
        CONF_DISPLAY_UNIT: UNIT_SNTPERKWH,
        CONF_VAT_RATE: DEFAULT_VAT_RATE,
        CONF_ELECTRICITY_TAX: DEFAULT_ELECTRICITY_TAX,
        CONF_SPOT_COMMISSION: DEFAULT_SPOT_COMMISSION,
        CONF_MAX_PRICE: DEFAULT_MAX_PRICE,
        CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER: DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
        CONF_MAX_RANK: DEFAULT_MAX_RANK,
        CONF_FORWARD_AVG_HOURS: DEFAULT_FORWARD_AVG_HOURS,
        CONF_CONTROL_FACTOR_FUNCTION: DEFAULT_CONTROL_FACTOR_FUNCTION,
        CONF_CONTROL_FACTOR_SCALING: DEFAULT_CONTROL_FACTOR_SCALING,
        CONF_EXPOSE_PRICE_ARRAYS: DEFAULT_EXPOSE_PRICE_ARRAYS,
        CONF_HIGH_PRECISION: DEFAULT_HIGH_PRECISION,
        CONF_EAGER_START_HOUR: DEFAULT_EAGER_START_HOUR,
        CONF_EAGER_END_HOUR: DEFAULT_EAGER_END_HOUR,
        CONF_SCORE_PROFILES: [],
        CONF_TRANSFER_GROUPS: [],
    }


@pytest.fixture
def mock_config_entry(options):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options=options,
    )


# ---------------------------------------------------------------------------
# Time-freeze fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_utcnow():
    """Freeze homeassistant.util.dt.utcnow() to FROZEN_UTC (2026-03-13 00:30 UTC)."""
    with patch("homeassistant.util.dt.utcnow", return_value=FROZEN_UTC):
        yield FROZEN_UTC


# ---------------------------------------------------------------------------
# Full integration setup
# ---------------------------------------------------------------------------


@pytest.fixture
async def setup_integration(hass, mock_config_entry, mock_utcnow):
    """Set up the Kilowahti integration with a mocked API and frozen time.

    The hass timezone is set to UTC so that as_local() is identity and
    FROZEN_UTC maps cleanly to FROZEN_DATE = 2026-03-13.
    """
    await hass.config.async_set_time_zone("UTC")
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return mock_config_entry
