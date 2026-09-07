"""Config flow and options flow for the Kilowahti integration."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, NamedTuple

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from kilowahti.sources.ecb import fetch_ecb_rate

from .const import (
    CDN_ZONES,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_POWER_KW,
    CONF_CONTROL_FACTOR_FUNCTION,
    CONF_CONTROL_FACTOR_SCALING,
    CONF_CURRENCY_MODE,
    CONF_DISPLAY_UNIT,
    CONF_EAGER_END_HOUR,
    CONF_EAGER_START_HOUR,
    CONF_ELECTRICITY_TAX,
    CONF_EXPORT_COMMISSION,
    CONF_EXPORT_PRICE_THRESHOLD,
    CONF_EXPORT_PRICING_MODE,
    CONF_EXPOSE_PRICE_ARRAYS,
    CONF_EXPOSE_TOTAL_PRICE_ARRAYS,
    CONF_FIXED_EXPORT_RATE,
    CONF_FORWARD_AVG_HOURS,
    CONF_FX_MODE,
    CONF_FX_RATE,
    CONF_GENERATION_ENABLED,
    CONF_HIGH_PRECISION,
    CONF_MAX_PRICE,
    CONF_MAX_RANK,
    CONF_MONTHLY_FIXED_COST,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    CONF_REGION,
    CONF_SCORE_PROFILES,
    CONF_SHOW_ROLLING_AVERAGES,
    CONF_SOLAR_WINDOW_END,
    CONF_SOLAR_WINDOW_START,
    CONF_SPOT_COMMISSION,
    CONF_TRANSFER_GROUPS,
    CONF_VAT_RATE,
    CONTROL_FACTOR_LINEAR,
    CONTROL_FACTOR_SINUSOIDAL,
    COUNTRY_PRESETS,
    CURRENCY_FOR_REGION,
    CURRENCY_MODE_EUR,
    CURRENCY_MODE_LOCAL,
    CURRENCY_UNITS,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_POWER_KW,
    DEFAULT_CONTROL_FACTOR_FUNCTION,
    DEFAULT_CONTROL_FACTOR_SCALING,
    DEFAULT_EAGER_END_HOUR,
    DEFAULT_EAGER_START_HOUR,
    DEFAULT_ELECTRICITY_TAX,
    DEFAULT_EXPORT_COMMISSION,
    DEFAULT_EXPORT_PRICE_THRESHOLD,
    DEFAULT_EXPORT_PRICING_MODE,
    DEFAULT_EXPOSE_PRICE_ARRAYS,
    DEFAULT_EXPOSE_TOTAL_PRICE_ARRAYS,
    DEFAULT_FIXED_EXPORT_RATE,
    DEFAULT_FORWARD_AVG_HOURS,
    DEFAULT_GENERATION_ENABLED,
    DEFAULT_HIGH_PRECISION,
    DEFAULT_MAX_PRICE,
    DEFAULT_MAX_RANK,
    DEFAULT_MONTHLY_FIXED_COST,
    DEFAULT_PRICE_RESOLUTION,
    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    DEFAULT_SCORE_FORMULA,
    DEFAULT_SCORE_PROFILE_ID,
    DEFAULT_SCORE_PROFILE_LABEL,
    DEFAULT_SHOW_ROLLING_AVERAGES,
    DEFAULT_SOLAR_WINDOW_END,
    DEFAULT_SOLAR_WINDOW_START,
    DEFAULT_SPOT_COMMISSION,
    DEFAULT_VAT_RATE,
    DOMAIN,
    ECB_CURRENCIES,
    EXPORT_PRICING_FIXED,
    EXPORT_PRICING_SPOT_LINKED,
    FX_MODE_AUTO,
    FX_MODE_MANUAL,
    SCORE_FORMULA_DEFAULT,
    SCORE_FORMULA_RAW,
    UNIT_EUROKWH,
    UNIT_SNTPERKWH,
    ZONES,
)

_LOGGER = logging.getLogger(__name__)


def _to_float(value) -> float:
    """Convert user input to float, accepting both '.' and ',' as decimal separator."""
    if isinstance(value, str):
        value = value.replace(",", ".")
    return float(value)


# Region selector options: all CDN zones, labels not translated (zone names
# are mostly proper nouns; 43 keys x 25 languages not worth it).
_REGION_OPTIONS = [{"value": z.code, "label": f"{z.code} — {z.name}"} for z in CDN_ZONES]

_MONTH_OPTIONS = [
    {"value": "1", "label": "January"},
    {"value": "2", "label": "February"},
    {"value": "3", "label": "March"},
    {"value": "4", "label": "April"},
    {"value": "5", "label": "May"},
    {"value": "6", "label": "June"},
    {"value": "7", "label": "July"},
    {"value": "8", "label": "August"},
    {"value": "9", "label": "September"},
    {"value": "10", "label": "October"},
    {"value": "11", "label": "November"},
    {"value": "12", "label": "December"},
]

_WEEKDAY_OPTIONS = [
    {"value": "0", "label": "Monday"},
    {"value": "1", "label": "Tuesday"},
    {"value": "2", "label": "Wednesday"},
    {"value": "3", "label": "Thursday"},
    {"value": "4", "label": "Friday"},
    {"value": "5", "label": "Saturday"},
    {"value": "6", "label": "Sunday"},
]


def _preset_for_region(region: str) -> tuple[float, float]:
    zone = ZONES.get(region)
    country = zone.country if zone is not None else "Custom"
    return COUNTRY_PRESETS.get(country, COUNTRY_PRESETS["Custom"])


def _region_currency(region: str) -> str:
    return CURRENCY_FOR_REGION.get(region, "EUR")


def _currency_schema(region: str, defaults: dict) -> vol.Schema:
    """Currency settings for a non-EUR region. RSD/MKD have no ECB reference
    rate, so the auto FX mode is not offered for them."""
    currency = _region_currency(region)
    fields: dict = {
        vol.Required(
            CONF_CURRENCY_MODE,
            default=defaults.get(CONF_CURRENCY_MODE, CURRENCY_MODE_LOCAL),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": CURRENCY_MODE_LOCAL, "label": f"Local currency ({currency})"},
                    {"value": CURRENCY_MODE_EUR, "label": "Euro (EUR)"},
                ],
            )
        ),
    }
    if currency in ECB_CURRENCIES:
        fields[vol.Required(CONF_FX_MODE, default=defaults.get(CONF_FX_MODE, FX_MODE_AUTO))] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": FX_MODE_AUTO, "label": "Automatic (ECB daily reference rate)"},
                        {"value": FX_MODE_MANUAL, "label": "Manual fixed rate"},
                    ],
                )
            )
        )
    fields[vol.Required(CONF_FX_RATE, default=defaults.get(CONF_FX_RATE, 0.0))] = (
        selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=100000, step=0.001, mode="box")
        )
    )
    return vol.Schema(fields)


class _Units(NamedTuple):
    """Units for money fields in the flows, and the scale they are entered on.

    Per-kWh values are stored on the minor scale (cents of the active
    currency). Currencies whose minor unit is out of use (CZK, HUF, RSD, MKD)
    have no minor label, so those fields are entered in the major unit and
    converted at the flow boundary; storage keeps the minor scale either way.
    Monthly costs are stored in major units and are never converted.
    """

    per_kwh: str
    per_month: str
    major_scale: bool


def _units_for(source: dict) -> _Units:
    """Money units implied by the region and currency mode in `source`."""
    currency = "EUR"
    if source.get(CONF_CURRENCY_MODE, CURRENCY_MODE_EUR) == CURRENCY_MODE_LOCAL:
        currency = _region_currency(source.get(CONF_REGION, "FI"))
    minor, major = CURRENCY_UNITS.get(currency, CURRENCY_UNITS["EUR"])
    per_month = f"{major.split('/')[0]}/month"
    if minor is None:
        return _Units(major, per_month, True)
    return _Units(minor, per_month, False)


def _from_stored(value: float, units: _Units) -> float:
    """Stored minor-scale value → the number shown in the form."""
    return round(value / 100.0, 5) if units.major_scale else value


def _to_stored(value: float, units: _Units) -> float:
    """Number entered in the form → stored minor-scale value."""
    return round(value * 100.0, 5) if units.major_scale else value


def _store_currency_input(target: dict, region: str, user_input: dict) -> None:
    currency = _region_currency(region)
    target[CONF_CURRENCY_MODE] = user_input[CONF_CURRENCY_MODE]
    default_fx = FX_MODE_AUTO if currency in ECB_CURRENCIES else FX_MODE_MANUAL
    target[CONF_FX_MODE] = user_input.get(CONF_FX_MODE, default_fx)
    target[CONF_FX_RATE] = _to_float(user_input.get(CONF_FX_RATE, 0.0) or 0.0)


# ---------------------------------------------------------------------------
# Shared schema builders
# ---------------------------------------------------------------------------


def _user_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("name", "Home")): selector.TextSelector(),
            vol.Required(
                CONF_REGION, default=defaults.get(CONF_REGION, "FI")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_REGION_OPTIONS,
                )
            ),
            vol.Required(
                CONF_PRICE_RESOLUTION,
                default=str(defaults.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION)),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "15", "label": "15 minutes"},
                        {"value": "60", "label": "1 hour"},
                    ],
                )
            ),
            vol.Required(
                CONF_DISPLAY_UNIT, default=defaults.get(CONF_DISPLAY_UNIT, UNIT_SNTPERKWH)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": UNIT_SNTPERKWH, "label": UNIT_SNTPERKWH},
                        {"value": UNIT_EUROKWH, "label": UNIT_EUROKWH},
                    ],
                )
            ),
        }
    )


def _vat_schema(defaults: dict, units: _Units | None = None) -> vol.Schema:
    units = units or _units_for(defaults)
    return vol.Schema(
        {
            vol.Required(
                "vat_rate_pct", default=defaults.get("vat_rate_pct", DEFAULT_VAT_RATE * 100)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=0.1, mode="box")
            ),
            vol.Required(
                CONF_ELECTRICITY_TAX,
                default=_from_stored(
                    defaults.get(CONF_ELECTRICITY_TAX, DEFAULT_ELECTRICITY_TAX), units
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, step=0.001, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                CONF_SPOT_COMMISSION,
                default=_from_stored(
                    defaults.get(CONF_SPOT_COMMISSION, DEFAULT_SPOT_COMMISSION), units
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=20, step=0.01, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                CONF_MONTHLY_FIXED_COST,
                default=defaults.get(CONF_MONTHLY_FIXED_COST, DEFAULT_MONTHLY_FIXED_COST),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=500, step=0.01, mode="box", unit_of_measurement=units.per_month
                )
            ),
        }
    )


def _thresholds_schema(
    defaults: dict,
    resolution: int = DEFAULT_PRICE_RESOLUTION,
    units: _Units | None = None,
) -> vol.Schema:
    max_rank = 24 if resolution == 60 else 96
    units = units or _units_for(defaults)
    return vol.Schema(
        {
            vol.Required(
                CONF_MAX_PRICE,
                default=_from_stored(defaults.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE), units),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=999, step=0.1, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
                default=defaults.get(
                    CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER,
                    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_MAX_RANK, default=defaults.get(CONF_MAX_RANK, DEFAULT_MAX_RANK)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=max_rank, step=1, mode="box")
            ),
            vol.Required(
                CONF_FORWARD_AVG_HOURS,
                default=defaults.get(CONF_FORWARD_AVG_HOURS, DEFAULT_FORWARD_AVG_HOURS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=24, step=0.25, mode="box")
            ),
            vol.Required(
                CONF_CONTROL_FACTOR_FUNCTION,
                default=defaults.get(CONF_CONTROL_FACTOR_FUNCTION, DEFAULT_CONTROL_FACTOR_FUNCTION),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": CONTROL_FACTOR_LINEAR, "label": "Linear"},
                        {"value": CONTROL_FACTOR_SINUSOIDAL, "label": "Sinusoidal"},
                    ]
                )
            ),
            vol.Required(
                CONF_CONTROL_FACTOR_SCALING,
                default=defaults.get(CONF_CONTROL_FACTOR_SCALING, DEFAULT_CONTROL_FACTOR_SCALING),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=3, step=0.1, mode="box")
            ),
        }
    )


def _score_profiles_schema(_defaults: dict) -> vol.Schema:
    return vol.Schema({})


def _advanced_options_schema(defaults: dict) -> vol.Schema:
    is_hourly = int(defaults.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION)) == 60
    fields: dict = {
        vol.Required(
            CONF_EXPOSE_PRICE_ARRAYS,
            default=defaults.get(CONF_EXPOSE_PRICE_ARRAYS, DEFAULT_EXPOSE_PRICE_ARRAYS),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_EXPOSE_TOTAL_PRICE_ARRAYS,
            default=defaults.get(CONF_EXPOSE_TOTAL_PRICE_ARRAYS, DEFAULT_EXPOSE_TOTAL_PRICE_ARRAYS),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_HIGH_PRECISION,
            default=defaults.get(CONF_HIGH_PRECISION, DEFAULT_HIGH_PRECISION),
        ): selector.BooleanSelector(),
    }
    if not is_hourly:
        fields[
            vol.Required(
                CONF_SHOW_ROLLING_AVERAGES,
                default=defaults.get(CONF_SHOW_ROLLING_AVERAGES, DEFAULT_SHOW_ROLLING_AVERAGES),
            )
        ] = selector.BooleanSelector()
    fields[
        vol.Required(
            CONF_GENERATION_ENABLED,
            default=defaults.get(CONF_GENERATION_ENABLED, DEFAULT_GENERATION_ENABLED),
        )
    ] = selector.BooleanSelector()
    return vol.Schema(fields)


def _generation_settings_schema(defaults: dict, units: _Units | None = None) -> vol.Schema:
    units = units or _units_for(defaults)
    return vol.Schema(
        {
            vol.Required(
                CONF_EXPORT_PRICING_MODE,
                default=defaults.get(CONF_EXPORT_PRICING_MODE, DEFAULT_EXPORT_PRICING_MODE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": EXPORT_PRICING_SPOT_LINKED, "label": "Spot-linked"},
                        {"value": EXPORT_PRICING_FIXED, "label": "Fixed rate"},
                    ]
                )
            ),
            vol.Required(
                CONF_EXPORT_COMMISSION,
                default=_from_stored(
                    defaults.get(CONF_EXPORT_COMMISSION, DEFAULT_EXPORT_COMMISSION), units
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=10, step=0.01, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                CONF_FIXED_EXPORT_RATE,
                default=_from_stored(
                    defaults.get(CONF_FIXED_EXPORT_RATE, DEFAULT_FIXED_EXPORT_RATE), units
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, step=0.01, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                CONF_EXPORT_PRICE_THRESHOLD,
                default=_from_stored(
                    defaults.get(CONF_EXPORT_PRICE_THRESHOLD, DEFAULT_EXPORT_PRICE_THRESHOLD), units
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, step=0.1, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                CONF_SOLAR_WINDOW_START,
                default=defaults.get(CONF_SOLAR_WINDOW_START, DEFAULT_SOLAR_WINDOW_START),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=23, step=1, mode="box")
            ),
            vol.Required(
                CONF_SOLAR_WINDOW_END,
                default=defaults.get(CONF_SOLAR_WINDOW_END, DEFAULT_SOLAR_WINDOW_END),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=24, step=1, mode="box")
            ),
            vol.Required(
                CONF_BATTERY_CAPACITY_KWH,
                default=defaults.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=999, step=0.1, mode="box", unit_of_measurement="kWh"
                )
            ),
            vol.Required(
                CONF_BATTERY_CHARGE_POWER_KW,
                default=defaults.get(CONF_BATTERY_CHARGE_POWER_KW, DEFAULT_BATTERY_CHARGE_POWER_KW),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=99, step=0.1, mode="box", unit_of_measurement="kW"
                )
            ),
        }
    )


def _group_settings_schema(defaults: dict | None = None, units: _Units | None = None) -> vol.Schema:
    defaults = defaults or {}
    units = units or _units_for(defaults)
    return vol.Schema(
        {
            vol.Required("label", default=defaults.get("label", "")): selector.TextSelector(),
            vol.Required(
                CONF_MONTHLY_FIXED_COST,
                default=defaults.get(CONF_MONTHLY_FIXED_COST, DEFAULT_MONTHLY_FIXED_COST),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=999, step=0.01, mode="box", unit_of_measurement=units.per_month
                )
            ),
        }
    )


def _add_group_schema(units: _Units | None = None) -> vol.Schema:
    return _group_settings_schema(units=units)


def _add_tier_schema(defaults: dict | None = None, units: _Units | None = None) -> vol.Schema:
    defaults = defaults or {}
    units = units or _units_for(defaults)
    return vol.Schema(
        {
            vol.Required("label", default=defaults.get("label", "")): selector.TextSelector(),
            vol.Required(
                "price", default=_from_stored(defaults.get("price", 5.0), units)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=999, step=0.01, mode="box", unit_of_measurement=units.per_kwh
                )
            ),
            vol.Required(
                "months", default=defaults.get("months", [str(i) for i in range(1, 13)])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_MONTH_OPTIONS, multiple=True)
            ),
            vol.Required(
                "weekdays", default=defaults.get("weekdays", [str(i) for i in range(7)])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_WEEKDAY_OPTIONS, multiple=True)
            ),
            vol.Required(
                "hour_start", default=defaults.get("hour_start", 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=23, step=1, mode="box")
            ),
            vol.Required("hour_end", default=defaults.get("hour_end", 24)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=24, step=1, mode="box")
            ),
            vol.Required("priority", default=defaults.get("priority", 10)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=100, step=1, mode="box")
            ),
        }
    )


def _edit_tier_schema(tier: dict, units: _Units | None = None) -> vol.Schema:
    """Add-tier fields pre-filled from `tier`, plus a removal checkbox."""
    schema = _add_tier_schema(_tier_defaults(tier), units=units).schema
    return vol.Schema({**schema, vol.Required("delete", default=False): selector.BooleanSelector()})


def _tier_defaults(tier: dict) -> dict:
    """Stored tier → schema defaults. The multi-selects work in strings."""
    return {
        **tier,
        "months": [str(m) for m in tier.get("months", [])],
        "weekdays": [str(w) for w in tier.get("weekdays", [])],
    }


def _label_for(value: int, options: list[dict]) -> str:
    for option in options:
        if option["value"] == str(value):
            return option["label"]
    return str(value)


def _range_summary(values: list[int], options: list[dict], all_label: str) -> str:
    """Compact rendering of a month or weekday selection."""
    if not values:
        return "—"
    if len(values) == len(options):
        return all_label
    return ", ".join(_label_for(v, options)[:3] for v in values)


def _tier_summary(tier: dict, units: _Units) -> str:
    hours = f"{int(tier['hour_start']):02d}:00–{int(tier['hour_end']):02d}:00"
    if int(tier["hour_start"]) == 0 and int(tier["hour_end"]) == 24:
        hours = "All day"
    return (
        f"- **{tier['label']}** — {_from_stored(tier['price'], units)} {units.per_kwh} · "
        f"{_range_summary(tier.get('months', []), _MONTH_OPTIONS, 'All year')} · "
        f"{_range_summary(tier.get('weekdays', []), _WEEKDAY_OPTIONS, 'All days')} · "
        f"{hours} · priority {tier['priority']}"
    )


def _tier_list_markdown(tiers: list[dict], units: _Units) -> str:
    """Tier overview for the group detail step, in evaluation order."""
    if not tiers:
        return "_No tiers yet._"
    ordered = sorted(tiers, key=lambda t: t.get("priority", 0))
    return "\n".join(_tier_summary(t, units) for t in ordered)


def _validate_tier(user_input: dict) -> str | None:
    if not user_input.get("months"):
        return "tier_no_months"
    if not user_input.get("weekdays"):
        return "tier_no_weekdays"
    if int(user_input["hour_end"]) <= int(user_input["hour_start"]):
        return "tier_hour_range"
    return None


def _tier_from_input(user_input: dict, units: _Units) -> dict:
    return {
        "label": user_input["label"],
        "price": _to_stored(_to_float(user_input["price"]), units),
        "months": [int(m) for m in user_input["months"]],
        "weekdays": [int(w) for w in user_input["weekdays"]],
        "hour_start": int(user_input["hour_start"]),
        "hour_end": int(user_input["hour_end"]),
        "priority": int(user_input["priority"]),
    }


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


class KilowahtiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._groups: list[dict] = []
        self._current_group_idx: int = 0
        self._current_tier_idx: int = 0

    # ------ Step 1: basic --------------------------------------------------

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            self._data["name"] = user_input["name"]
            self._data[CONF_REGION] = user_input[CONF_REGION]
            self._data[CONF_PRICE_RESOLUTION] = int(user_input[CONF_PRICE_RESOLUTION])
            self._data[CONF_DISPLAY_UNIT] = user_input[CONF_DISPLAY_UNIT]
            if _region_currency(self._data[CONF_REGION]) != "EUR":
                return await self.async_step_currency()
            return await self.async_step_vat_and_tax()

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(self._data),
        )

    # ------ Step 1b: currency (non-EUR regions only) -----------------------

    async def async_step_currency(self, user_input: dict | None = None):
        region = self._data[CONF_REGION]
        if user_input is not None:
            _store_currency_input(self._data, region, user_input)
            return await self.async_step_vat_and_tax()

        return self.async_show_form(
            step_id="currency",
            data_schema=_currency_schema(region, self._data),
            description_placeholders={"currency": _region_currency(region)},
        )

    # ------ Step 2: VAT & tax ---------------------------------------------

    async def async_step_vat_and_tax(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            units = _units_for(self._data)
            self._data[CONF_VAT_RATE] = _to_float(user_input["vat_rate_pct"]) / 100.0
            self._data[CONF_ELECTRICITY_TAX] = _to_stored(
                _to_float(user_input[CONF_ELECTRICITY_TAX]), units
            )
            self._data[CONF_SPOT_COMMISSION] = _to_stored(
                _to_float(user_input.get(CONF_SPOT_COMMISSION, 0.0)), units
            )
            self._data[CONF_MONTHLY_FIXED_COST] = _to_float(
                user_input.get(CONF_MONTHLY_FIXED_COST, 0.0)
            )
            return await self.async_step_transfer_groups()

        # Pre-fill from region preset
        vat_rate, elec_tax = _preset_for_region(self._data.get(CONF_REGION, "FI"))
        defaults = {
            "vat_rate_pct": round(vat_rate * 100, 1),
            CONF_ELECTRICITY_TAX: elec_tax,
            CONF_SPOT_COMMISSION: DEFAULT_SPOT_COMMISSION,
        }

        return self.async_show_form(
            step_id="vat_and_tax",
            data_schema=_vat_schema(defaults),
            errors=errors,
        )

    # ------ Step 3: transfer groups (multi-step loop) ---------------------

    async def async_step_transfer_groups(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "continue")
            if action == "continue":
                self._data[CONF_TRANSFER_GROUPS] = self._groups
                return await self.async_step_thresholds()
            if action == "add_group":
                return await self.async_step_add_transfer_group()
            if action.startswith("manage_"):
                self._current_group_idx = int(action.split("_", 1)[1])
                return await self.async_step_transfer_group_detail()

        group_options: list[dict] = []
        for i, g in enumerate(self._groups):
            active_label = " [active]" if g.get("active") else ""
            tier_count = len(g.get("tiers", []))
            group_options.append(
                {
                    "value": f"manage_{i}",
                    "label": f"⚙ Manage: {g['label']} ({tier_count} tiers){active_label}",
                }
            )
        group_options.append({"value": "add_group", "label": "➕ Add group"})
        group_options.append({"value": "continue", "label": "✓ Continue to thresholds"})

        return self.async_show_form(
            step_id="transfer_groups",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="continue"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=group_options)
                    )
                }
            ),
        )

    async def async_step_add_transfer_group(self, user_input: dict | None = None):
        if user_input is not None:
            new_group = {
                "id": str(uuid.uuid4()),
                "label": user_input["label"],
                "active": len(self._groups) == 0,  # first group is active by default
                "tiers": [],
                CONF_MONTHLY_FIXED_COST: _to_float(user_input[CONF_MONTHLY_FIXED_COST]),
            }
            self._groups.append(new_group)
            self._current_group_idx = len(self._groups) - 1
            return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="add_transfer_group",
            data_schema=_add_group_schema(_units_for(self._data)),
        )

    async def async_step_transfer_group_detail(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "back")
            if action == "back":
                return await self.async_step_transfer_groups()
            if action == "add_tier":
                return await self.async_step_add_transfer_tier()
            if action == "edit_group_settings":
                return await self.async_step_edit_group_settings()
            if action == "set_active":
                for i, g in enumerate(self._groups):
                    g["active"] = i == self._current_group_idx
                return await self.async_step_transfer_group_detail()
            if action == "remove_group":
                self._groups.pop(self._current_group_idx)
                # Ensure at least one group is active
                if self._groups and not any(g["active"] for g in self._groups):
                    self._groups[0]["active"] = True
                return await self.async_step_transfer_groups()
            if action.startswith("edit_tier_"):
                self._current_tier_idx = int(action.split("_", 2)[2])
                return await self.async_step_edit_transfer_tier()

        group = self._groups[self._current_group_idx]
        action_options: list[dict] = [
            {"value": "add_tier", "label": "➕ Add tier"},
            {"value": "edit_group_settings", "label": "⚙ Edit group settings"},
        ]
        if not group.get("active"):
            action_options.append({"value": "set_active", "label": "★ Set as active group"})
        for i, tier in enumerate(group.get("tiers", [])):
            action_options.append(
                {"value": f"edit_tier_{i}", "label": f"✎ Edit tier: {tier['label']}"}
            )
        action_options.append({"value": "remove_group", "label": "✕ Remove this group"})
        action_options.append({"value": "back", "label": "← Back to groups"})

        return self.async_show_form(
            step_id="transfer_group_detail",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="back"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=action_options)
                    )
                }
            ),
            description_placeholders={
                "group_label": group["label"],
                "tier_count": str(len(group.get("tiers", []))),
                "tier_list": _tier_list_markdown(group.get("tiers", []), _units_for(self._data)),
            },
        )

    async def async_step_edit_group_settings(self, user_input: dict | None = None):
        group = self._groups[self._current_group_idx]
        if user_input is not None:
            group["label"] = user_input["label"]
            group[CONF_MONTHLY_FIXED_COST] = _to_float(user_input[CONF_MONTHLY_FIXED_COST])
            return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="edit_group_settings",
            data_schema=_group_settings_schema(group, _units_for(self._data)),
        )

    async def async_step_add_transfer_tier(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        units = _units_for(self._data)

        if user_input is not None:
            err = _validate_tier(user_input)
            if err:
                errors["base"] = err
            else:
                self._groups[self._current_group_idx]["tiers"].append(
                    _tier_from_input(user_input, units)
                )
                return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="add_transfer_tier",
            data_schema=_add_tier_schema(units=_units_for(self._data)),
            errors=errors,
        )

    async def async_step_edit_transfer_tier(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        units = _units_for(self._data)
        tiers = self._groups[self._current_group_idx]["tiers"]

        if user_input is not None:
            if user_input.get("delete"):
                tiers.pop(self._current_tier_idx)
                return await self.async_step_transfer_group_detail()
            err = _validate_tier(user_input)
            if err:
                errors["base"] = err
            else:
                tiers[self._current_tier_idx] = _tier_from_input(user_input, units)
                return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="edit_transfer_tier",
            data_schema=_edit_tier_schema(tiers[self._current_tier_idx], _units_for(self._data)),
            errors=errors,
        )

    # ------ Step 4: thresholds & control ----------------------------------

    async def async_step_thresholds(self, user_input: dict | None = None):
        if user_input is not None:
            self._data[CONF_MAX_PRICE] = _to_stored(
                _to_float(user_input[CONF_MAX_PRICE]), _units_for(self._data)
            )
            self._data[CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER] = user_input[
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER
            ]
            self._data[CONF_MAX_RANK] = int(user_input[CONF_MAX_RANK])
            self._data[CONF_FORWARD_AVG_HOURS] = _to_float(user_input[CONF_FORWARD_AVG_HOURS])
            self._data[CONF_CONTROL_FACTOR_FUNCTION] = user_input[CONF_CONTROL_FACTOR_FUNCTION]
            self._data[CONF_CONTROL_FACTOR_SCALING] = _to_float(
                user_input[CONF_CONTROL_FACTOR_SCALING]
            )
            return await self.async_step_score_profiles()

        return self.async_show_form(
            step_id="thresholds",
            data_schema=_thresholds_schema(
                {}, self._data.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION)
            ),
        )

    # ------ Step 5: score profiles ----------------------------------------

    async def async_step_score_profiles(self, user_input: dict | None = None):
        if user_input is not None:
            self._data[CONF_SCORE_PROFILES] = [
                {"id": DEFAULT_SCORE_PROFILE_ID, "label": DEFAULT_SCORE_PROFILE_LABEL, "meters": []}
            ]
            self._data[CONF_EAGER_START_HOUR] = DEFAULT_EAGER_START_HOUR
            self._data[CONF_EAGER_END_HOUR] = DEFAULT_EAGER_END_HOUR
            return await self.async_step_advanced_options()

        return self.async_show_form(
            step_id="score_profiles",
            data_schema=_score_profiles_schema({}),
        )

    # ------ Step 6: sensor display ----------------------------------------

    async def async_step_advanced_options(self, user_input: dict | None = None):
        if user_input is not None:
            self._data[CONF_EXPOSE_PRICE_ARRAYS] = user_input[CONF_EXPOSE_PRICE_ARRAYS]
            self._data[CONF_EXPOSE_TOTAL_PRICE_ARRAYS] = user_input[CONF_EXPOSE_TOTAL_PRICE_ARRAYS]
            self._data[CONF_HIGH_PRECISION] = user_input[CONF_HIGH_PRECISION]
            self._data[CONF_SHOW_ROLLING_AVERAGES] = user_input.get(
                CONF_SHOW_ROLLING_AVERAGES, False
            )
            self._data[CONF_GENERATION_ENABLED] = user_input[CONF_GENERATION_ENABLED]
            return self.async_create_entry(title=self._data["name"], data={}, options=self._data)

        return self.async_show_form(
            step_id="advanced_options",
            data_schema=_advanced_options_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "KilowahtiOptionsFlow":
        return KilowahtiOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class KilowahtiOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._options: dict[str, Any] = dict(config_entry.options)
        self._groups: list[dict] = list(self._options.get(CONF_TRANSFER_GROUPS, []))
        self._current_group_idx: int = 0
        self._current_tier_idx: int = 0
        self._current_profile_idx: int = 0

    # ------ Top-level menu ------------------------------------------------

    async def async_step_init(self, user_input: dict | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "basic",
                "transfer_groups",
                "thresholds",
                "score_profiles",
                "advanced_options",
                "fixed_periods",
                "generation_settings",
            ],
        )

    # ------ Basic settings ------------------------------------------------

    async def async_step_basic(self, user_input: dict | None = None):
        old_region = self._options.get(CONF_REGION, "FI")
        if user_input is not None:
            old_mode = self._options.get(CONF_CURRENCY_MODE, CURRENCY_MODE_EUR)
            self._options["name"] = user_input["name"]
            self._options[CONF_REGION] = user_input[CONF_REGION]
            self._options[CONF_PRICE_RESOLUTION] = int(user_input[CONF_PRICE_RESOLUTION])
            self._options[CONF_DISPLAY_UNIT] = user_input[CONF_DISPLAY_UNIT]
            old_units = _units_for(self._options)
            self._options[CONF_VAT_RATE] = _to_float(user_input["vat_rate_pct"]) / 100.0
            self._options[CONF_ELECTRICITY_TAX] = _to_stored(
                _to_float(user_input[CONF_ELECTRICITY_TAX]), old_units
            )
            self._options[CONF_SPOT_COMMISSION] = _to_stored(
                _to_float(user_input.get(CONF_SPOT_COMMISSION, 0.0)), old_units
            )
            self._options[CONF_MONTHLY_FIXED_COST] = _to_float(
                user_input.get(CONF_MONTHLY_FIXED_COST, 0.0)
            )
            if CONF_CURRENCY_MODE in user_input:
                _store_currency_input(self._options, old_region, user_input)
                new_mode = self._options[CONF_CURRENCY_MODE]
                if new_mode != old_mode:
                    await self._async_convert_currency_values(old_region, new_mode)
            return self.async_create_entry(data=self._options)

        cur = self._options
        basic_defaults = {
            "name": cur.get("name", "Home"),
            CONF_REGION: cur.get(CONF_REGION, "FI"),
            CONF_PRICE_RESOLUTION: str(cur.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION)),
            CONF_DISPLAY_UNIT: cur.get(CONF_DISPLAY_UNIT, UNIT_SNTPERKWH),
        }
        vat_defaults = {
            "vat_rate_pct": round(cur.get(CONF_VAT_RATE, DEFAULT_VAT_RATE) * 100, 1),
            CONF_ELECTRICITY_TAX: cur.get(CONF_ELECTRICITY_TAX, DEFAULT_ELECTRICITY_TAX),
            CONF_SPOT_COMMISSION: cur.get(CONF_SPOT_COMMISSION, DEFAULT_SPOT_COMMISSION),
            CONF_MONTHLY_FIXED_COST: cur.get(CONF_MONTHLY_FIXED_COST, DEFAULT_MONTHLY_FIXED_COST),
        }
        schema_fields = {**_user_schema(basic_defaults).schema, **_vat_schema(vat_defaults).schema}
        if _region_currency(old_region) != "EUR":
            # Currency defaults: existing entries without the option keep EUR display
            currency_defaults = {
                CONF_CURRENCY_MODE: cur.get(CONF_CURRENCY_MODE, CURRENCY_MODE_EUR),
                CONF_FX_MODE: cur.get(CONF_FX_MODE, FX_MODE_AUTO),
                CONF_FX_RATE: cur.get(CONF_FX_RATE, 0.0),
            }
            schema_fields.update(_currency_schema(old_region, currency_defaults).schema)
        schema = vol.Schema(schema_fields)

        return self.async_show_form(step_id="basic", data_schema=schema)

    async def _async_convert_currency_values(self, region: str, new_mode: str) -> None:
        """One-shot conversion of stored currency-typed values on a mode flip.

        EUR→local multiplies by the day's rate, local→EUR divides. Skipped
        with a warning when no usable rate is available.
        """
        rate = await self._async_flip_rate(region)
        if rate is None or rate <= 0:
            _LOGGER.warning(
                "Currency mode changed but no FX rate available; stored prices NOT converted"
            )
            return
        factor = rate if new_mode == CURRENCY_MODE_LOCAL else 1.0 / rate

        for key in (
            CONF_MAX_PRICE,
            CONF_SPOT_COMMISSION,
            CONF_EXPORT_COMMISSION,
            CONF_FIXED_EXPORT_RATE,
            CONF_EXPORT_PRICE_THRESHOLD,
            CONF_MONTHLY_FIXED_COST,
        ):
            value = self._options.get(key)
            if isinstance(value, (int, float)) and value:
                self._options[key] = round(value * factor, 5)
                _LOGGER.info("Currency flip: %s %s → %s", key, value, self._options[key])

        groups = self._options.get(CONF_TRANSFER_GROUPS) or []
        for group in groups:
            if group.get("monthly_fixed_cost"):
                group["monthly_fixed_cost"] = round(group["monthly_fixed_cost"] * factor, 5)
            for tier in group.get("tiers", []):
                if tier.get("price"):
                    old = tier["price"]
                    tier["price"] = round(old * factor, 5)
                    _LOGGER.info(
                        "Currency flip: transfer tier %s %s → %s",
                        tier.get("label", "?"),
                        old,
                        tier["price"],
                    )
        self._options[CONF_TRANSFER_GROUPS] = groups

        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if coordinator is not None:
            await coordinator._storage.async_scale_period_prices(factor)
            _LOGGER.info("Currency flip: fixed-period prices scaled by %.6f", factor)

    async def _async_flip_rate(self, region: str) -> float | None:
        """Rate for the flip conversion: manual rate → coordinator's active
        auto rate → fresh ECB fetch."""
        if self._options.get(CONF_FX_MODE) == FX_MODE_MANUAL:
            manual = float(self._options.get(CONF_FX_RATE) or 0.0)
            return manual if manual > 0 else None
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if coordinator is not None and coordinator._fx_active_rate:
            return coordinator._fx_active_rate
        currency = _region_currency(region)
        if currency in ECB_CURRENCIES:
            try:
                result = await fetch_ecb_rate(async_get_clientsession(self.hass), currency)
            except Exception as err:
                _LOGGER.warning("ECB rate fetch for currency flip failed: %s", err)
                return None
            return result.rate
        manual = float(self._options.get(CONF_FX_RATE) or 0.0)
        return manual if manual > 0 else None

    # ------ Transfer groups (mirrors config flow) -------------------------

    async def async_step_transfer_groups(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "save")
            if action == "save":
                self._options[CONF_TRANSFER_GROUPS] = self._groups
                return self.async_create_entry(data=self._options)
            if action == "add_group":
                return await self.async_step_add_transfer_group()
            if action.startswith("manage_"):
                self._current_group_idx = int(action.split("_", 1)[1])
                return await self.async_step_transfer_group_detail()

        group_options: list[dict] = []
        for i, g in enumerate(self._groups):
            active_label = " [active]" if g.get("active") else ""
            tier_count = len(g.get("tiers", []))
            group_options.append(
                {
                    "value": f"manage_{i}",
                    "label": f"⚙ Manage: {g['label']} ({tier_count} tiers){active_label}",
                }
            )
        group_options.append({"value": "add_group", "label": "➕ Add group"})
        group_options.append({"value": "save", "label": "✓ Save & close"})

        return self.async_show_form(
            step_id="transfer_groups",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="save"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=group_options)
                    )
                }
            ),
        )

    async def async_step_add_transfer_group(self, user_input: dict | None = None):
        if user_input is not None:
            new_group = {
                "id": str(uuid.uuid4()),
                "label": user_input["label"],
                "active": len(self._groups) == 0,
                "tiers": [],
                CONF_MONTHLY_FIXED_COST: _to_float(user_input[CONF_MONTHLY_FIXED_COST]),
            }
            self._groups.append(new_group)
            self._current_group_idx = len(self._groups) - 1
            return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="add_transfer_group",
            data_schema=_add_group_schema(_units_for(self._options)),
        )

    async def async_step_transfer_group_detail(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "back")
            if action == "back":
                return await self.async_step_transfer_groups()
            if action == "add_tier":
                return await self.async_step_add_transfer_tier()
            if action == "edit_group_settings":
                return await self.async_step_edit_group_settings()
            if action == "set_active":
                for i, g in enumerate(self._groups):
                    g["active"] = i == self._current_group_idx
                return await self.async_step_transfer_group_detail()
            if action == "remove_group":
                self._groups.pop(self._current_group_idx)
                if self._groups and not any(g["active"] for g in self._groups):
                    self._groups[0]["active"] = True
                return await self.async_step_transfer_groups()
            if action.startswith("edit_tier_"):
                self._current_tier_idx = int(action.split("_", 2)[2])
                return await self.async_step_edit_transfer_tier()

        group = self._groups[self._current_group_idx]
        action_options: list[dict] = [
            {"value": "add_tier", "label": "➕ Add tier"},
            {"value": "edit_group_settings", "label": "⚙ Edit group settings"},
        ]
        if not group.get("active"):
            action_options.append({"value": "set_active", "label": "★ Set as active group"})
        for i, tier in enumerate(group.get("tiers", [])):
            action_options.append(
                {"value": f"edit_tier_{i}", "label": f"✎ Edit tier: {tier['label']}"}
            )
        action_options.append({"value": "remove_group", "label": "✕ Remove this group"})
        action_options.append({"value": "back", "label": "← Back to groups"})

        return self.async_show_form(
            step_id="transfer_group_detail",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="back"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=action_options)
                    )
                }
            ),
            description_placeholders={
                "group_label": group["label"],
                "tier_count": str(len(group.get("tiers", []))),
                "tier_list": _tier_list_markdown(group.get("tiers", []), _units_for(self._options)),
            },
        )

    async def async_step_edit_group_settings(self, user_input: dict | None = None):
        group = self._groups[self._current_group_idx]
        if user_input is not None:
            group["label"] = user_input["label"]
            group[CONF_MONTHLY_FIXED_COST] = _to_float(user_input[CONF_MONTHLY_FIXED_COST])
            return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="edit_group_settings",
            data_schema=_group_settings_schema(group, _units_for(self._options)),
        )

    async def async_step_add_transfer_tier(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        units = _units_for(self._options)

        if user_input is not None:
            err = _validate_tier(user_input)
            if err:
                errors["base"] = err
            else:
                self._groups[self._current_group_idx]["tiers"].append(
                    _tier_from_input(user_input, units)
                )
                return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="add_transfer_tier",
            data_schema=_add_tier_schema(units=_units_for(self._options)),
            errors=errors,
        )

    async def async_step_edit_transfer_tier(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        units = _units_for(self._options)
        tiers = self._groups[self._current_group_idx]["tiers"]

        if user_input is not None:
            if user_input.get("delete"):
                tiers.pop(self._current_tier_idx)
                return await self.async_step_transfer_group_detail()
            err = _validate_tier(user_input)
            if err:
                errors["base"] = err
            else:
                tiers[self._current_tier_idx] = _tier_from_input(user_input, units)
                return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="edit_transfer_tier",
            data_schema=_edit_tier_schema(tiers[self._current_tier_idx], _units_for(self._options)),
            errors=errors,
        )

    # ------ Thresholds ----------------------------------------------------

    async def async_step_thresholds(self, user_input: dict | None = None):
        if user_input is not None:
            self._options[CONF_MAX_PRICE] = _to_stored(
                _to_float(user_input[CONF_MAX_PRICE]), _units_for(self._options)
            )
            self._options[CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER] = user_input[
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER
            ]
            self._options[CONF_MAX_RANK] = int(user_input[CONF_MAX_RANK])
            self._options[CONF_FORWARD_AVG_HOURS] = _to_float(user_input[CONF_FORWARD_AVG_HOURS])
            self._options[CONF_CONTROL_FACTOR_FUNCTION] = user_input[CONF_CONTROL_FACTOR_FUNCTION]
            self._options[CONF_CONTROL_FACTOR_SCALING] = _to_float(
                user_input[CONF_CONTROL_FACTOR_SCALING]
            )
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="thresholds",
            data_schema=_thresholds_schema(
                self._options,
                self._options.get(CONF_PRICE_RESOLUTION, DEFAULT_PRICE_RESOLUTION),
            ),
        )

    # ------ Score profiles ------------------------------------------------

    async def async_step_score_profiles(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "save")
            if action == "save":
                return self.async_create_entry(data=self._options)
            if action == "add_profile":
                return await self.async_step_add_score_profile()
            if action.startswith("edit_profile_"):
                self._current_profile_idx = int(action[len("edit_profile_") :])
                return await self.async_step_edit_score_profile()

        profiles = self._options.get(CONF_SCORE_PROFILES, [])
        action_options = []
        for i, p in enumerate(profiles):
            meter_count = len(p.get("meters", []))
            action_options.append(
                {
                    "value": f"edit_profile_{i}",
                    "label": f"✎ Edit: {p['label']} ({meter_count} meter{'s' if meter_count != 1 else ''})",
                }
            )
        action_options.append({"value": "add_profile", "label": "➕ Add score profile"})
        action_options.append({"value": "save", "label": "✓ Save & close"})

        return self.async_show_form(
            step_id="score_profiles",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="save"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=action_options)
                    ),
                }
            ),
        )

    # ------ Advanced options ------------------------------------------------

    async def async_step_advanced_options(self, user_input: dict | None = None):
        if user_input is not None:
            self._options[CONF_EXPOSE_PRICE_ARRAYS] = user_input[CONF_EXPOSE_PRICE_ARRAYS]
            self._options[CONF_EXPOSE_TOTAL_PRICE_ARRAYS] = user_input[
                CONF_EXPOSE_TOTAL_PRICE_ARRAYS
            ]
            self._options[CONF_HIGH_PRECISION] = user_input[CONF_HIGH_PRECISION]
            self._options[CONF_SHOW_ROLLING_AVERAGES] = user_input.get(
                CONF_SHOW_ROLLING_AVERAGES, False
            )
            self._options[CONF_GENERATION_ENABLED] = user_input[CONF_GENERATION_ENABLED]
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="advanced_options",
            data_schema=_advanced_options_schema(self._options),
        )

    # ------ Generation & export settings ---------------------------------

    async def async_step_generation_settings(self, user_input: dict | None = None):
        if user_input is not None:
            self._options[CONF_EXPORT_PRICING_MODE] = user_input[CONF_EXPORT_PRICING_MODE]
            units = _units_for(self._options)
            self._options[CONF_EXPORT_COMMISSION] = _to_stored(
                _to_float(user_input[CONF_EXPORT_COMMISSION]), units
            )
            self._options[CONF_FIXED_EXPORT_RATE] = _to_stored(
                _to_float(user_input[CONF_FIXED_EXPORT_RATE]), units
            )
            self._options[CONF_EXPORT_PRICE_THRESHOLD] = _to_stored(
                _to_float(user_input[CONF_EXPORT_PRICE_THRESHOLD]), units
            )
            self._options[CONF_SOLAR_WINDOW_START] = int(user_input[CONF_SOLAR_WINDOW_START])
            self._options[CONF_SOLAR_WINDOW_END] = int(user_input[CONF_SOLAR_WINDOW_END])
            self._options[CONF_BATTERY_CAPACITY_KWH] = _to_float(
                user_input[CONF_BATTERY_CAPACITY_KWH]
            )
            self._options[CONF_BATTERY_CHARGE_POWER_KW] = _to_float(
                user_input[CONF_BATTERY_CHARGE_POWER_KW]
            )
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="generation_settings",
            data_schema=_generation_settings_schema(self._options),
        )

    async def async_step_add_score_profile(self, user_input: dict | None = None):
        if user_input is not None:
            profiles = list(self._options.get(CONF_SCORE_PROFILES, []))
            profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "label": user_input["label"],
                    "meters": user_input.get("meters") or [],
                    "formula": user_input.get("formula", DEFAULT_SCORE_FORMULA),
                }
            )
            self._options[CONF_SCORE_PROFILES] = profiles
            return await self.async_step_score_profiles()

        return self.async_show_form(
            step_id="add_score_profile",
            data_schema=vol.Schema(
                {
                    vol.Required("label"): selector.TextSelector(),
                    vol.Optional("meters", default=[]): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor",
                            multiple=True,
                        )
                    ),
                    vol.Required("formula", default=SCORE_FORMULA_DEFAULT): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": SCORE_FORMULA_DEFAULT, "label": "Default formula"},
                                {"value": SCORE_FORMULA_RAW, "label": "Raw formula"},
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_edit_score_profile(self, user_input: dict | None = None):
        profiles = [dict(p) for p in self._options.get(CONF_SCORE_PROFILES, [])]
        profile = profiles[self._current_profile_idx]
        meters = list(profile.get("meters") or [])

        if user_input is not None:
            action = user_input.get("action", "done")
            if action == "done":
                return await self.async_step_score_profiles()
            if action == "add_meters":
                return await self.async_step_add_profile_meters()
            if action == "edit_formula":
                return await self.async_step_edit_profile_formula()
            if action.startswith("remove_meter_"):
                idx = int(action[len("remove_meter_") :])
                meters.pop(idx)
                profile["meters"] = meters
                profiles[self._current_profile_idx] = profile
                self._options[CONF_SCORE_PROFILES] = profiles
                return await self.async_step_edit_score_profile()

        action_options = []
        for i, entity_id in enumerate(meters):
            action_options.append({"value": f"remove_meter_{i}", "label": f"✕ Remove: {entity_id}"})
        action_options.append({"value": "add_meters", "label": "➕ Add meter(s)"})
        formula = profile.get("formula", SCORE_FORMULA_DEFAULT)
        action_options.append({"value": "edit_formula", "label": f"⚙ Formula: {formula}"})
        action_options.append({"value": "done", "label": "← Back"})

        return self.async_show_form(
            step_id="edit_score_profile",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="done"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=action_options)
                    ),
                }
            ),
            description_placeholders={
                "profile_label": profile["label"],
                "meter_count": str(len(meters)),
            },
        )

    async def async_step_add_profile_meters(self, user_input: dict | None = None):
        if user_input is not None:
            profiles = [dict(p) for p in self._options.get(CONF_SCORE_PROFILES, [])]
            profile = profiles[self._current_profile_idx]
            existing = list(profile.get("meters") or [])
            for m in user_input.get("meters") or []:
                if m not in existing:
                    existing.append(m)
            profile["meters"] = existing
            profiles[self._current_profile_idx] = profile
            self._options[CONF_SCORE_PROFILES] = profiles
            return await self.async_step_edit_score_profile()

        return self.async_show_form(
            step_id="add_profile_meters",
            data_schema=vol.Schema(
                {
                    vol.Optional("meters", default=[]): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", multiple=True)
                    ),
                }
            ),
        )

    async def async_step_edit_profile_formula(self, user_input: dict | None = None):
        profiles = [dict(p) for p in self._options.get(CONF_SCORE_PROFILES, [])]
        profile = profiles[self._current_profile_idx]

        if user_input is not None:
            profile["formula"] = user_input.get("formula", DEFAULT_SCORE_FORMULA)
            profiles[self._current_profile_idx] = profile
            self._options[CONF_SCORE_PROFILES] = profiles
            return await self.async_step_edit_score_profile()

        return self.async_show_form(
            step_id="edit_profile_formula",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "formula", default=profile.get("formula", SCORE_FORMULA_DEFAULT)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": SCORE_FORMULA_DEFAULT, "label": "Default formula"},
                                {"value": SCORE_FORMULA_RAW, "label": "Raw formula"},
                            ]
                        )
                    ),
                }
            ),
            description_placeholders={"profile_label": profile["label"]},
        )

    # ------ Fixed-price periods -------------------------------------------

    def _coordinator(self):
        from .const import DOMAIN

        return self.hass.data[DOMAIN][self._entry.entry_id]

    async def async_step_fixed_periods(self, user_input: dict | None = None):
        coordinator = self._coordinator()
        storage = coordinator._storage
        periods = storage.periods

        if user_input is not None:
            action = user_input.get("action", "close")
            if action == "close":
                return self.async_create_entry(data=self._options)
            if action == "add_period":
                return await self.async_step_add_fixed_period()
            if action.startswith("remove_period_"):
                period_id = action[len("remove_period_") :]
                await storage.async_remove_period(period_id)
                coordinator.async_update_listeners()
                return await self.async_step_fixed_periods()

        units = _units_for(self._options)
        period_options: list[dict] = []
        for p in periods:
            price = f"{_from_stored(p.price, units)} {units.per_kwh}"
            period_options.append(
                {
                    "value": f"remove_period_{p.id}",
                    "label": f"✕ Remove: {p.label} ({p.start_date} – {p.end_date}, {price})",
                }
            )
        period_options.append({"value": "add_period", "label": "➕ Add period"})
        period_options.append({"value": "close", "label": "✓ Close"})

        return self.async_show_form(
            step_id="fixed_periods",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="close"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=period_options)
                    )
                }
            ),
        )

    async def async_step_add_fixed_period(self, user_input: dict | None = None):
        from .models import FixedPeriod

        errors: dict[str, str] = {}
        units = _units_for(self._options)

        if user_input is not None:
            try:
                start = date.fromisoformat(user_input["start_date"])
                end = date.fromisoformat(user_input["end_date"])
            except ValueError:
                errors["base"] = "period_invalid_dates"
            else:
                if end < start:
                    errors["base"] = "period_invalid_dates"
                elif _to_float(user_input["price"]) <= 0:
                    errors["base"] = "period_price_zero"
                else:
                    coordinator = self._coordinator()
                    storage = coordinator._storage
                    new_start, new_end = start, end
                    overlap = any(
                        not (new_end < p.start_date or new_start > p.end_date)
                        for p in storage.periods
                    )
                    if overlap:
                        errors["base"] = "period_overlap"
                    else:
                        period = FixedPeriod(
                            id=str(uuid.uuid4()),
                            label=user_input["label"],
                            start_date=start,
                            end_date=end,
                            price=_to_stored(_to_float(user_input["price"]), units),
                        )
                        await storage.async_add_period(period)
                        coordinator.async_update_listeners()
                        return await self.async_step_fixed_periods()

        return self.async_show_form(
            step_id="add_fixed_period",
            data_schema=vol.Schema(
                {
                    vol.Required("label"): selector.TextSelector(),
                    vol.Required("start_date"): selector.DateSelector(),
                    vol.Required("end_date"): selector.DateSelector(),
                    vol.Required("price"): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=999,
                            step=0.01,
                            mode="box",
                            unit_of_measurement=units.per_kwh,
                        )
                    ),
                }
            ),
            errors=errors,
        )
