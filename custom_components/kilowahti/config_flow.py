"""Config flow and options flow for the Kilowahti integration."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    API_REGIONS,
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
    CONF_TRANSFER_GROUPS,
    CONF_VAT_RATE,
    CONTROL_FACTOR_LINEAR,
    CONTROL_FACTOR_SINUSOIDAL,
    COUNTRY_PRESETS,
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
    DEFAULT_PRICE_RESOLUTION,
    DEFAULT_PRICE_THRESHOLD_INCLUDES_TRANSFER,
    DEFAULT_SCORE_PROFILE_ID,
    DEFAULT_SCORE_PROFILE_LABEL,
    DEFAULT_VAT_RATE,
    DOMAIN,
    UNIT_EUROKWH,
    UNIT_SNTPERKWH,
)

_LOGGER = logging.getLogger(__name__)

# Region → country preset lookup
_REGION_TO_COUNTRY: dict[str, str] = {
    "FI": "FI",
    "EE": "EE",
    "LT": "LT",
    "LV": "LV",
    "DK1": "DK",
    "DK2": "DK",
    "NO1": "NO",
    "NO2": "NO",
    "NO3": "NO",
    "NO4": "NO",
    "NO5": "NO",
    "SE1": "SE",
    "SE2": "SE",
    "SE3": "SE",
    "SE4": "SE",
}

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
    country = _REGION_TO_COUNTRY.get(region, "Custom")
    return COUNTRY_PRESETS.get(country, COUNTRY_PRESETS["Custom"])


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
                    options=[{"value": r, "label": r} for r in API_REGIONS],
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


def _vat_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                "vat_rate_pct", default=defaults.get("vat_rate_pct", DEFAULT_VAT_RATE * 100)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=0.1, mode="box")
            ),
            vol.Required(
                CONF_ELECTRICITY_TAX,
                default=defaults.get(CONF_ELECTRICITY_TAX, DEFAULT_ELECTRICITY_TAX),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=0.001, mode="box")
            ),
        }
    )


def _thresholds_schema(defaults: dict, resolution: int = DEFAULT_PRICE_RESOLUTION) -> vol.Schema:
    max_rank = 24 if resolution == 60 else 96
    return vol.Schema(
        {
            vol.Required(
                CONF_MAX_PRICE, default=defaults.get(CONF_MAX_PRICE, DEFAULT_MAX_PRICE)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=999, step=0.1, mode="box")
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


def _sensor_display_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_EXPOSE_PRICE_ARRAYS,
                default=defaults.get(CONF_EXPOSE_PRICE_ARRAYS, DEFAULT_EXPOSE_PRICE_ARRAYS),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_HIGH_PRECISION,
                default=defaults.get(CONF_HIGH_PRECISION, DEFAULT_HIGH_PRECISION),
            ): selector.BooleanSelector(),
        }
    )


def _add_group_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("label"): selector.TextSelector(),
        }
    )


def _add_tier_schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("label", default=defaults.get("label", "")): selector.TextSelector(),
            vol.Required("price", default=defaults.get("price", 5.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=999, step=0.01, mode="box")
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


def _validate_tier(user_input: dict) -> str | None:
    if not user_input.get("months"):
        return "tier_no_months"
    if not user_input.get("weekdays"):
        return "tier_no_weekdays"
    if int(user_input["hour_end"]) <= int(user_input["hour_start"]):
        return "tier_hour_range"
    return None


def _tier_from_input(user_input: dict) -> dict:
    return {
        "label": user_input["label"],
        "price": float(user_input["price"]),
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

    # ------ Step 1: basic --------------------------------------------------

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            self._data["name"] = user_input["name"]
            self._data[CONF_REGION] = user_input[CONF_REGION]
            self._data[CONF_PRICE_RESOLUTION] = int(user_input[CONF_PRICE_RESOLUTION])
            self._data[CONF_DISPLAY_UNIT] = user_input[CONF_DISPLAY_UNIT]
            return await self.async_step_vat_and_tax()

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(self._data),
        )

    # ------ Step 2: VAT & tax ---------------------------------------------

    async def async_step_vat_and_tax(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_VAT_RATE] = float(user_input["vat_rate_pct"]) / 100.0
            self._data[CONF_ELECTRICITY_TAX] = float(user_input[CONF_ELECTRICITY_TAX])
            return await self.async_step_transfer_groups()

        # Pre-fill from region preset
        vat_rate, elec_tax = _preset_for_region(self._data.get(CONF_REGION, "FI"))
        defaults = {
            "vat_rate_pct": round(vat_rate * 100, 1),
            CONF_ELECTRICITY_TAX: elec_tax,
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
                    "label": f"Manage: {g['label']}{active_label} ({tier_count} tiers)",
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
            }
            self._groups.append(new_group)
            self._current_group_idx = len(self._groups) - 1
            return await self.async_step_transfer_group_detail()

        return self.async_show_form(step_id="add_transfer_group", data_schema=_add_group_schema())

    async def async_step_transfer_group_detail(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "back")
            if action == "back":
                return await self.async_step_transfer_groups()
            if action == "add_tier":
                return await self.async_step_add_transfer_tier()
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
            if action.startswith("remove_tier_"):
                tier_idx = int(action.split("_", 2)[2])
                self._groups[self._current_group_idx]["tiers"].pop(tier_idx)
                return await self.async_step_transfer_group_detail()

        group = self._groups[self._current_group_idx]
        action_options: list[dict] = [
            {"value": "add_tier", "label": "➕ Add tier"},
        ]
        if not group.get("active"):
            action_options.append({"value": "set_active", "label": "★ Set as active group"})
        for i, tier in enumerate(group.get("tiers", [])):
            action_options.append(
                {"value": f"remove_tier_{i}", "label": f"✕ Remove tier: {tier['label']}"}
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
            },
        )

    async def async_step_add_transfer_tier(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            err = _validate_tier(user_input)
            if err:
                errors["base"] = err
            else:
                self._groups[self._current_group_idx]["tiers"].append(_tier_from_input(user_input))
                return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="add_transfer_tier",
            data_schema=_add_tier_schema(),
            errors=errors,
        )

    # ------ Step 4: thresholds & control ----------------------------------

    async def async_step_thresholds(self, user_input: dict | None = None):
        if user_input is not None:
            self._data[CONF_MAX_PRICE] = float(user_input[CONF_MAX_PRICE])
            self._data[CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER] = user_input[
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER
            ]
            self._data[CONF_MAX_RANK] = int(user_input[CONF_MAX_RANK])
            self._data[CONF_FORWARD_AVG_HOURS] = float(user_input[CONF_FORWARD_AVG_HOURS])
            self._data[CONF_CONTROL_FACTOR_FUNCTION] = user_input[CONF_CONTROL_FACTOR_FUNCTION]
            self._data[CONF_CONTROL_FACTOR_SCALING] = float(user_input[CONF_CONTROL_FACTOR_SCALING])
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
            return await self.async_step_sensor_display()

        return self.async_show_form(
            step_id="score_profiles",
            data_schema=_score_profiles_schema({}),
        )

    # ------ Step 6: sensor display ----------------------------------------

    async def async_step_sensor_display(self, user_input: dict | None = None):
        if user_input is not None:
            self._data[CONF_EXPOSE_PRICE_ARRAYS] = user_input[CONF_EXPOSE_PRICE_ARRAYS]
            self._data[CONF_HIGH_PRECISION] = user_input[CONF_HIGH_PRECISION]
            return self.async_create_entry(title=self._data["name"], data={}, options=self._data)

        return self.async_show_form(
            step_id="sensor_display",
            data_schema=_sensor_display_schema({}),
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

    # ------ Top-level menu ------------------------------------------------

    async def async_step_init(self, user_input: dict | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "basic",
                "transfer_groups",
                "thresholds",
                "score_profiles",
                "sensor_display",
                "fixed_periods",
            ],
        )

    # ------ Basic settings ------------------------------------------------

    async def async_step_basic(self, user_input: dict | None = None):
        if user_input is not None:
            self._options["name"] = user_input["name"]
            self._options[CONF_REGION] = user_input[CONF_REGION]
            self._options[CONF_PRICE_RESOLUTION] = int(user_input[CONF_PRICE_RESOLUTION])
            self._options[CONF_DISPLAY_UNIT] = user_input[CONF_DISPLAY_UNIT]
            self._options[CONF_VAT_RATE] = float(user_input["vat_rate_pct"]) / 100.0
            self._options[CONF_ELECTRICITY_TAX] = float(user_input[CONF_ELECTRICITY_TAX])
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
        }
        schema = vol.Schema(
            {**_user_schema(basic_defaults).schema, **_vat_schema(vat_defaults).schema}
        )

        return self.async_show_form(step_id="basic", data_schema=schema)

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
                    "label": f"Manage: {g['label']}{active_label} ({tier_count} tiers)",
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
            }
            self._groups.append(new_group)
            self._current_group_idx = len(self._groups) - 1
            return await self.async_step_transfer_group_detail()

        return self.async_show_form(step_id="add_transfer_group", data_schema=_add_group_schema())

    async def async_step_transfer_group_detail(self, user_input: dict | None = None):
        if user_input is not None:
            action = user_input.get("action", "back")
            if action == "back":
                return await self.async_step_transfer_groups()
            if action == "add_tier":
                return await self.async_step_add_transfer_tier()
            if action == "set_active":
                for i, g in enumerate(self._groups):
                    g["active"] = i == self._current_group_idx
                return await self.async_step_transfer_group_detail()
            if action == "remove_group":
                self._groups.pop(self._current_group_idx)
                if self._groups and not any(g["active"] for g in self._groups):
                    self._groups[0]["active"] = True
                return await self.async_step_transfer_groups()
            if action.startswith("remove_tier_"):
                tier_idx = int(action.split("_", 2)[2])
                self._groups[self._current_group_idx]["tiers"].pop(tier_idx)
                return await self.async_step_transfer_group_detail()

        group = self._groups[self._current_group_idx]
        action_options: list[dict] = [{"value": "add_tier", "label": "➕ Add tier"}]
        if not group.get("active"):
            action_options.append({"value": "set_active", "label": "★ Set as active group"})
        for i, tier in enumerate(group.get("tiers", [])):
            action_options.append(
                {"value": f"remove_tier_{i}", "label": f"✕ Remove tier: {tier['label']}"}
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
        )

    async def async_step_add_transfer_tier(self, user_input: dict | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            err = _validate_tier(user_input)
            if err:
                errors["base"] = err
            else:
                self._groups[self._current_group_idx]["tiers"].append(_tier_from_input(user_input))
                return await self.async_step_transfer_group_detail()

        return self.async_show_form(
            step_id="add_transfer_tier",
            data_schema=_add_tier_schema(),
            errors=errors,
        )

    # ------ Thresholds ----------------------------------------------------

    async def async_step_thresholds(self, user_input: dict | None = None):
        if user_input is not None:
            self._options[CONF_MAX_PRICE] = float(user_input[CONF_MAX_PRICE])
            self._options[CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER] = user_input[
                CONF_PRICE_THRESHOLD_INCLUDES_TRANSFER
            ]
            self._options[CONF_MAX_RANK] = int(user_input[CONF_MAX_RANK])
            self._options[CONF_FORWARD_AVG_HOURS] = float(user_input[CONF_FORWARD_AVG_HOURS])
            self._options[CONF_CONTROL_FACTOR_FUNCTION] = user_input[CONF_CONTROL_FACTOR_FUNCTION]
            self._options[CONF_CONTROL_FACTOR_SCALING] = float(
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

        profiles = self._options.get(CONF_SCORE_PROFILES, [])
        action_options = [
            {"value": "add_profile", "label": "➕ Add score profile"},
            {"value": "save", "label": "✓ Save & close"},
        ]

        return self.async_show_form(
            step_id="score_profiles",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="save"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=action_options)
                    ),
                }
            ),
            description_placeholders={"profile_count": str(len(profiles))},
        )

    # ------ Sensor display ------------------------------------------------

    async def async_step_sensor_display(self, user_input: dict | None = None):
        if user_input is not None:
            self._options[CONF_EXPOSE_PRICE_ARRAYS] = user_input[CONF_EXPOSE_PRICE_ARRAYS]
            self._options[CONF_HIGH_PRECISION] = user_input[CONF_HIGH_PRECISION]
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="sensor_display",
            data_schema=_sensor_display_schema(self._options),
        )

    async def async_step_add_score_profile(self, user_input: dict | None = None):
        if user_input is not None:
            profiles = list(self._options.get(CONF_SCORE_PROFILES, []))
            profiles.append(
                {
                    "id": str(uuid.uuid4()),
                    "label": user_input["label"],
                    "meters": user_input.get("meters", []),
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
                            device_class="energy",
                            multiple=True,
                        )
                    ),
                }
            ),
        )

    # ------ Fixed-price periods -------------------------------------------

    async def async_step_fixed_periods(self, user_input: dict | None = None):
        from .storage import KilowahtiStorage

        storage = KilowahtiStorage(self.hass, self._entry.entry_id)
        await storage.async_load()
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
                return await self.async_step_fixed_periods()

        period_options: list[dict] = []
        for p in periods:
            period_options.append(
                {
                    "value": f"remove_period_{p.id}",
                    "label": f"✕ Remove: {p.label} ({p.start_date} – {p.end_date}, {p.price} c/kWh)",
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
        from .storage import KilowahtiStorage

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                start = date.fromisoformat(user_input["start_date"])
                end = date.fromisoformat(user_input["end_date"])
            except ValueError:
                errors["base"] = "period_invalid_dates"
            else:
                if end < start:
                    errors["base"] = "period_invalid_dates"
                elif float(user_input["price"]) <= 0:
                    errors["base"] = "period_price_zero"
                else:
                    storage = KilowahtiStorage(self.hass, self._entry.entry_id)
                    await storage.async_load()
                    # Check for overlaps
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
                            price=float(user_input["price"]),
                        )
                        await storage.async_add_period(period)
                        return await self.async_step_fixed_periods()

        return self.async_show_form(
            step_id="add_fixed_period",
            data_schema=vol.Schema(
                {
                    vol.Required("label"): selector.TextSelector(),
                    vol.Required("start_date"): selector.DateSelector(),
                    vol.Required("end_date"): selector.DateSelector(),
                    vol.Required("price"): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0.001, max=999, step=0.001, mode="box")
                    ),
                }
            ),
            errors=errors,
        )
