"""Constants for the Kilowahti integration."""

from __future__ import annotations

from kilowahti.const import (
    API_BASE_URL,
    API_ENDPOINT_BOTH,
    API_ENDPOINT_TODAY,
    API_ENDPOINT_TOMORROW,
    API_REGIONS,
    CONTROL_FACTOR_LINEAR,
    CONTROL_FACTOR_SINUSOIDAL,
    COUNTRY_PRESETS,
    SCORE_FORMULA_DEFAULT,
    SCORE_FORMULA_RAW,
    UNIT_EUROKWH,
    UNIT_SNTPERKWH,
)

DOMAIN = "kilowahti"

# Config / options entry keys
CONF_REGION = "region"
CONF_PRICE_RESOLUTION = "price_resolution"
CONF_DISPLAY_UNIT = "display_unit"
CONF_VAT_RATE = "vat_rate"
CONF_ELECTRICITY_TAX = "electricity_tax"
CONF_SPOT_COMMISSION = "spot_commission"
CONF_TRANSFER_GROUPS = "transfer_groups"
CONF_MAX_PRICE = "max_price"
CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER = "price_threshold_includes_transfer"
CONF_MAX_RANK = "max_rank"
CONF_FORWARD_AVG_HOURS = "forward_avg_hours"
CONF_CONTROL_FACTOR_FUNCTION = "control_factor_function"
CONF_CONTROL_FACTOR_SCALING = "control_factor_scaling"
CONF_SCORE_PROFILES = "score_profiles"
CONF_EXPOSE_PRICE_ARRAYS = "expose_price_arrays"
CONF_GENERATION_ENABLED = "generation_enabled"
CONF_HIGH_PRECISION = "high_precision"
CONF_SHOW_ROLLING_AVERAGES = "show_rolling_averages"
CONF_EAGER_START_HOUR = "eager_start_hour"
CONF_EAGER_END_HOUR = "eager_end_hour"
# E1 — Export
CONF_EXPORT_PRICING_MODE = "export_pricing_mode"
CONF_EXPORT_COMMISSION = "export_commission"
CONF_FIXED_EXPORT_RATE = "fixed_export_rate"
CONF_EXPORT_PRICE_THRESHOLD = "export_price_threshold"
CONF_SOLAR_WINDOW_START = "solar_window_start"
CONF_SOLAR_WINDOW_END = "solar_window_end"
# E2 — Battery
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_BATTERY_CHARGE_POWER_KW = "battery_charge_power_kw"
# E3 — Fixed costs
CONF_MONTHLY_FIXED_COST = "monthly_fixed_cost"

# Export pricing modes
EXPORT_PRICING_SPOT_LINKED = "spot_linked"
EXPORT_PRICING_FIXED = "fixed"

# Defaults
DEFAULT_PRICE_RESOLUTION = 15
DEFAULT_VAT_RATE = 0.255  # FI
DEFAULT_ELECTRICITY_TAX = 2.253  # FI, c/kWh class I
DEFAULT_SPOT_COMMISSION = 0.0  # c/kWh, gross (VAT included)
DEFAULT_MAX_PRICE = 20.0  # c/kWh
DEFAULT_MAX_RANK = 24
DEFAULT_FORWARD_AVG_HOURS = 4.0
DEFAULT_CONTROL_FACTOR_FUNCTION = "linear"
DEFAULT_CONTROL_FACTOR_SCALING = 1.0
DEFAULT_EAGER_START_HOUR = 14
DEFAULT_EAGER_END_HOUR = 21
DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER = True
DEFAULT_EXPOSE_PRICE_ARRAYS = False
DEFAULT_GENERATION_ENABLED = False
DEFAULT_HIGH_PRECISION = False
DEFAULT_SHOW_ROLLING_AVERAGES = False
# E1 defaults
DEFAULT_EXPORT_PRICING_MODE = EXPORT_PRICING_SPOT_LINKED
DEFAULT_EXPORT_COMMISSION = 0.0  # c/kWh deducted from spot
DEFAULT_FIXED_EXPORT_RATE = 0.0  # c/kWh, used when mode = fixed
DEFAULT_EXPORT_PRICE_THRESHOLD = 5.0  # c/kWh threshold for export_price_acceptable
DEFAULT_SOLAR_WINDOW_START = 9  # hour (local)
DEFAULT_SOLAR_WINDOW_END = 14  # hour (local), exclusive
# E2 defaults
DEFAULT_BATTERY_CAPACITY_KWH = 0.0
DEFAULT_BATTERY_CHARGE_POWER_KW = 0.0
# E3 defaults
DEFAULT_MONTHLY_FIXED_COST = 0.0  # €/month

# Storage
STORAGE_VERSION = 1

# Score formula default (re-exported for convenience)
DEFAULT_SCORE_FORMULA = SCORE_FORMULA_DEFAULT

# Sensor suffixes
SENSOR_SPOT_PRICE = "spot_price"
SENSOR_EFFECTIVE_PRICE = "effective_price"
SENSOR_TRANSFER_PRICE = "transfer_price"
SENSOR_TOTAL_PRICE = "total_price"
SENSOR_PRICE_RANK = "price_rank"
SENSOR_TOTAL_PRICE_RANK = "total_price_rank"
SENSOR_TODAY_AVG = "today_avg"
SENSOR_TODAY_MIN = "today_min"
SENSOR_TODAY_MAX = "today_max"
SENSOR_TOMORROW_AVG = "tomorrow_avg"
SENSOR_TOMORROW_MIN = "tomorrow_min"
SENSOR_TOMORROW_MAX = "tomorrow_max"
SENSOR_TODAY_TOTAL_AVG = "today_total_avg"
SENSOR_TODAY_TOTAL_MIN = "today_total_min"
SENSOR_TODAY_TOTAL_MAX = "today_total_max"
SENSOR_TOMORROW_TOTAL_AVG = "tomorrow_total_avg"
SENSOR_TOMORROW_TOTAL_MIN = "tomorrow_total_min"
SENSOR_TOMORROW_TOTAL_MAX = "tomorrow_total_max"
SENSOR_NEXT_HOURS_AVG = "next_hours_avg"
# E1 — Export & generation sensors
SENSOR_EXPORT_PRICE = "export_price"
SENSOR_EXPORT_TODAY_AVG = "export_today_avg"
SENSOR_EXPORT_TODAY_MIN = "export_today_min"
SENSOR_EXPORT_TODAY_MAX = "export_today_max"
SENSOR_EXPORT_TOMORROW_AVG = "export_tomorrow_avg"
SENSOR_EXPORT_TOMORROW_MIN = "export_tomorrow_min"
SENSOR_EXPORT_TOMORROW_MAX = "export_tomorrow_max"
SENSOR_IMPORT_EXPORT_SPREAD = "import_export_spread"
SENSOR_SELF_CONSUMPTION_VALUE = "self_consumption_value"
SENSOR_CURRENT_30MIN_AVG = "avg_price_30min"
SENSOR_CURRENT_60MIN_AVG = "avg_price_60min"
SENSOR_CURRENT_120MIN_AVG = "avg_price_120min"
SENSOR_NEXT_SOLAR_WINDOW_AVG = "next_solar_window_avg"
# E2 — Battery sensors
SENSOR_ARBITRAGE_SPREAD_TODAY = "arbitrage_spread_today"
SENSOR_CHARGE_OPPORTUNITY_FACTOR = "charge_opportunity_factor"
SENSOR_OPTIMAL_CHARGE_WINDOW_START = "optimal_charge_window_start"
SENSOR_OPTIMAL_CHARGE_WINDOW_END = "optimal_charge_window_end"
SENSOR_BATTERY_CHARGE_RECOMMENDATION = "battery_charge_recommendation"
# E3 — Fixed cost sensors
SENSOR_MONTHLY_FIXED_COST_TODAY = "monthly_fixed_cost_today"
SENSOR_CONTROL_FACTOR_PRICE = "control_factor_price"
SENSOR_CONTROL_FACTOR_PRICE_BIPOLAR = "control_factor_price_bipolar"
SENSOR_CONTROL_FACTOR_TRANSFER = "control_factor_transfer"
SENSOR_PRICE_QUARTILE = "price_quartile"
SENSOR_SETTING_MAX_PRICE = "setting_max_price"
SENSOR_SETTING_PRICE_THRESHOLD_INCLUDES_TRANSFER = "setting_price_threshold_includes_transfer"
SENSOR_SETTING_CONTROL_FACTOR_FUNCTION = "setting_control_factor_function"
SENSOR_SETTING_ACCEPTABLE_RANK = "setting_acceptable_rank"
SENSOR_SETTING_FORWARD_WINDOW = "setting_forward_window"
SENSOR_SETTING_ACTIVE_TRANSFER_GROUP = "setting_active_transfer_group"
SENSOR_SETTING_ACTIVE_TRANSFER_TIER = "setting_active_transfer_tier"
SENSOR_SETTING_ACTIVE_FIXED_PERIOD = "setting_active_fixed_period"

NUMBER_PRICE_THRESHOLD = "price_threshold"
NUMBER_RANK_THRESHOLD = "rank_threshold"

BINARY_SENSOR_PRICE_ACCEPTABLE = "price_acceptable"
BINARY_SENSOR_RANK_ACCEPTABLE = "rank_acceptable"
BINARY_SENSOR_PRICE_OR_RANK_ACCEPTABLE = "price_or_rank_acceptable"
BINARY_SENSOR_FIXED_PERIOD_ACTIVE = "fixed_period_active"
BINARY_SENSOR_TOMORROW_AVAILABLE = "tomorrow_available"
# E1/E2 binary sensors
BINARY_SENSOR_EXPORT_PRICE_ACCEPTABLE = "export_price_acceptable"
BINARY_SENSOR_CHARGE_FROM_GRID_RECOMMENDED = "charge_from_grid_recommended"
BINARY_SENSOR_DISCHARGE_TO_GRID_RECOMMENDED = "discharge_to_grid_recommended"

# Default score profile
DEFAULT_SCORE_PROFILE_ID = "total"
DEFAULT_SCORE_PROFILE_LABEL = "Total"

__all__ = [
    # re-exported from kilowahti.const
    "API_BASE_URL",
    "API_ENDPOINT_BOTH",
    "API_ENDPOINT_TODAY",
    "API_ENDPOINT_TOMORROW",
    "API_REGIONS",
    "CONTROL_FACTOR_LINEAR",
    "CONTROL_FACTOR_SINUSOIDAL",
    "COUNTRY_PRESETS",
    "SCORE_FORMULA_DEFAULT",
    "SCORE_FORMULA_RAW",
    "UNIT_EUROKWH",
    "UNIT_SNTPERKWH",
]
