"""Constants for the Kilowahti integration."""

from __future__ import annotations

DOMAIN = "kilowahti"

# API
API_BASE_URL = "https://api.spot-hinta.fi"
API_ENDPOINT_TODAY = "/Today"
API_ENDPOINT_TOMORROW = "/DayForward"
API_ENDPOINT_BOTH = "/TodayAndDayForward"

# Config / options entry keys
CONF_REGION = "region"
CONF_PRICE_RESOLUTION = "price_resolution"
CONF_DISPLAY_UNIT = "display_unit"
CONF_VAT_RATE = "vat_rate"
CONF_ELECTRICITY_TAX = "electricity_tax"
CONF_TRANSFER_GROUPS = "transfer_groups"
CONF_MAX_PRICE = "max_price"
CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER = "price_threshold_includes_transfer"
CONF_MAX_RANK = "max_rank"
CONF_FORWARD_AVG_HOURS = "forward_avg_hours"
CONF_CONTROL_FACTOR_FUNCTION = "control_factor_function"
CONF_CONTROL_FACTOR_SCALING = "control_factor_scaling"
CONF_SCORE_PROFILES = "score_profiles"
CONF_EXPOSE_PRICE_ARRAYS = "expose_price_arrays"
CONF_HIGH_PRECISION = "high_precision"
CONF_EAGER_START_HOUR = "eager_start_hour"
CONF_EAGER_END_HOUR = "eager_end_hour"

# Defaults
DEFAULT_PRICE_RESOLUTION = 15
DEFAULT_VAT_RATE = 0.255  # FI
DEFAULT_ELECTRICITY_TAX = 2.253  # FI, c/kWh class I
DEFAULT_MAX_PRICE = 20.0  # c/kWh
DEFAULT_MAX_RANK = 24
DEFAULT_FORWARD_AVG_HOURS = 4.0
DEFAULT_CONTROL_FACTOR_FUNCTION = "linear"
DEFAULT_CONTROL_FACTOR_SCALING = 1.0
DEFAULT_EAGER_START_HOUR = 13
DEFAULT_EAGER_END_HOUR = 21
DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER = True
DEFAULT_EXPOSE_PRICE_ARRAYS = False
DEFAULT_HIGH_PRECISION = False

# Display units
UNIT_SNTPERKWH = "c/kWh"
UNIT_EUROKWH = "€/kWh"

# Control factor functions
CONTROL_FACTOR_LINEAR = "linear"
CONTROL_FACTOR_SINUSOIDAL = "sinusoidal"

# Storage
STORAGE_VERSION = 1

# Country presets: {code: (vat_rate, electricity_tax_snt_per_kwh)}
COUNTRY_PRESETS: dict[str, tuple[float, float]] = {
    "FI": (0.255, 2.253),
    "SE": (0.25, 0.439),
    "NO": (0.25, 0.0713),
    "DK": (0.25, 0.008),
    "EE": (0.22, 0.001),
    "LV": (0.22, 0.0),
    "LT": (0.22, 0.001),
    "DE": (0.19, 2.05),
    "NL": (0.21, 12.28),
    "FR": (0.20, 2.57),
    "AT": (0.20, 0.001),
    "BE": (0.21, 0.001),
    "PT": (0.23, 0.001),
    "HR": (0.25, 0.001),
    "IE": (0.23, 0.001),
    "LU": (0.17, 0.001),
    "Custom": (0.0, 0.0),
}

# Available API regions
API_REGIONS = [
    "FI",
    "EE",
    "LT",
    "LV",
    "DK1",
    "DK2",
    "NO1",
    "NO2",
    "NO3",
    "NO4",
    "NO5",
    "SE1",
    "SE2",
    "SE3",
    "SE4",
]

# Sensor suffixes
SENSOR_SPOT_PRICE = "spot_price"
SENSOR_EFFECTIVE_PRICE = "effective_price"
SENSOR_TRANSFER_PRICE = "transfer_price"
SENSOR_TOTAL_PRICE = "total_price"
SENSOR_PRICE_RANK = "price_rank"
SENSOR_TODAY_AVG = "today_avg"
SENSOR_TODAY_MIN = "today_min"
SENSOR_TODAY_MAX = "today_max"
SENSOR_TOMORROW_AVG = "tomorrow_avg"
SENSOR_TOMORROW_MIN = "tomorrow_min"
SENSOR_TOMORROW_MAX = "tomorrow_max"
SENSOR_NEXT_HOURS_AVG = "next_hours_avg"
SENSOR_CONTROL_FACTOR = "control_factor"
SENSOR_CONTROL_FACTOR_BIPOLAR = "control_factor_bipolar"
SENSOR_TRANSFER_RANK = "transfer_rank"

BINARY_SENSOR_PRICE_ACCEPTABLE = "price_acceptable"
BINARY_SENSOR_RANK_ACCEPTABLE = "rank_acceptable"
BINARY_SENSOR_PRICE_OR_RANK_ACCEPTABLE = "price_or_rank_acceptable"
BINARY_SENSOR_FIXED_PERIOD_ACTIVE = "fixed_period_active"
BINARY_SENSOR_TOMORROW_AVAILABLE = "tomorrow_available"

# Default score profile
DEFAULT_SCORE_PROFILE_ID = "total"
DEFAULT_SCORE_PROFILE_LABEL = "Total"
