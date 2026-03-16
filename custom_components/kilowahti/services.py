"""Service handlers for the Kilowahti integration."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
from kilowahti import calc

from .const import DOMAIN, UNIT_EUROKWH
from .coordinator import KilowahtiCoordinator
from .models import FixedPeriod

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_OPT_ENTRY_ID = vol.Optional("config_entry_id")
_OPT_FORMATTED = vol.Optional("formatted", default=True)

GET_PRICES_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        _OPT_FORMATTED: cv.boolean,
    }
)

CHEAPEST_HOURS_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24)),
        _OPT_FORMATTED: cv.boolean,
    }
)

AVERAGE_PRICE_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        _OPT_FORMATTED: cv.boolean,
    }
)

ADD_FIXED_PERIOD_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("label"): cv.string,
        vol.Required("start_date"): cv.date,
        vol.Required("end_date"): cv.date,
        vol.Required("price"): vol.All(vol.Coerce(float), vol.Range(min=0.001)),
    }
)

REMOVE_FIXED_PERIOD_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("period_id"): cv.string,
    }
)

LIST_FIXED_PERIODS_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
    }
)

GET_ACTIVE_PRICES_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Optional("start"): cv.datetime,
        vol.Optional("end"): cv.datetime,
        _OPT_FORMATTED: cv.boolean,
    }
)

GET_EXPORT_PRICES_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Optional("start"): cv.datetime,
        vol.Optional("end"): cv.datetime,
        _OPT_FORMATTED: cv.boolean,
    }
)

BEST_EXPORT_HOURS_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24)),
        _OPT_FORMATTED: cv.boolean,
    }
)

BEST_CHARGE_HOURS_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24)),
        _OPT_FORMATTED: cv.boolean,
    }
)

GENERATION_SCHEDULE_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("forecast"): [
            vol.Schema(
                {
                    vol.Required("time"): cv.datetime,
                    vol.Required("kwh"): vol.All(vol.Coerce(float), vol.Range(min=0)),
                }
            )
        ],
        _OPT_FORMATTED: cv.boolean,
    }
)


# ---------------------------------------------------------------------------
# Coordinator lookup
# ---------------------------------------------------------------------------


def _get_coordinator(hass: HomeAssistant, entry_id: str | None) -> KilowahtiCoordinator:
    coordinators: dict[str, KilowahtiCoordinator] = hass.data.get(DOMAIN, {})

    if not coordinators:
        raise vol.Invalid("No Kilowahti entries are configured")

    if entry_id is not None:
        if entry_id not in coordinators:
            raise vol.Invalid(f"No Kilowahti entry with id '{entry_id}'")
        return coordinators[entry_id]

    if len(coordinators) == 1:
        return next(iter(coordinators.values()))

    raise vol.Invalid(
        "Multiple Kilowahti entries exist; specify config_entry_id in the service call"
    )


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------


def _fmt(coordinator: KilowahtiCoordinator, price: float | None, formatted: bool) -> float | None:
    """Apply unit conversion and display rounding when formatted=True, else return raw value."""
    if price is None:
        return None
    if not formatted:
        return price
    converted = coordinator.format_price(price)
    if converted is None:
        return None
    base = 5 if coordinator._high_precision else 2
    extra = 2 if coordinator.native_unit == UNIT_EUROKWH else 0
    return round(converted, base + extra)


# ---------------------------------------------------------------------------
# Service handlers
# ---------------------------------------------------------------------------


async def _handle_get_prices(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]
    formatted: bool = call.data["formatted"]

    slots = coordinator.slots_in_range(start, end)
    return {
        "unit": coordinator.native_unit,
        "price_periods": [
            {
                "time": slot.dt_utc.isoformat(),
                "price_no_tax": slot.price_no_tax,
                "price": _fmt(coordinator, coordinator._spot_effective(slot), formatted),
                "rank": slot.rank,
            }
            for slot in slots
        ],
    }


async def _handle_cheapest_hours(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]
    hours: float = call.data["hours"]
    formatted: bool = call.data["formatted"]

    slots = coordinator.slots_in_range(start, end)
    if not slots:
        return {"error": "No price slots available in the specified range"}

    resolution_minutes = coordinator._resolution.value
    slots_needed = max(1, round(hours * 60 / resolution_minutes))

    result = calc.cheapest_window(
        slots, slots_needed, coordinator._vat_rate, coordinator._spot_commission
    )
    if result is None:
        return {"error": f"Requested {hours}h but only {len(slots)} slots available in range"}

    best_window, avg_price = result

    return {
        "start": best_window[0].dt_utc.isoformat(),
        "end": best_window[-1].dt_utc.isoformat(),
        "average_price": _fmt(coordinator, avg_price, formatted),
        "unit": coordinator.native_unit,
        "price_periods": [
            {
                "time": s.dt_utc.isoformat(),
                "price": _fmt(coordinator, coordinator._spot_effective(s), formatted),
                "rank": s.rank,
            }
            for s in best_window
        ],
    }


async def _handle_average_price(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]

    formatted: bool = call.data["formatted"]

    slots = coordinator.slots_in_range(start, end)
    if not slots:
        return {"error": "No price slots available in the specified range"}

    prices = [coordinator._spot_effective(s) for s in slots]
    avg = sum(prices) / len(prices)

    return {
        "average_price": _fmt(coordinator, avg, formatted),
        "min_price": _fmt(coordinator, min(prices), formatted),
        "max_price": _fmt(coordinator, max(prices), formatted),
        "unit": coordinator.native_unit,
        "slot_count": len(slots),
    }


async def _handle_add_fixed_period(call: ServiceCall) -> None:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    start = call.data["start_date"]
    end = call.data["end_date"]

    if end < start:
        raise vol.Invalid("end_date must be on or after start_date")

    price = call.data["price"]
    if price <= 0:
        raise vol.Invalid("price must be greater than zero")

    storage = coordinator._storage
    overlap = any(not (end < p.start_date or start > p.end_date) for p in storage.periods)
    if overlap:
        raise vol.Invalid("This period overlaps with an existing fixed-price period")

    period = FixedPeriod(
        id=str(uuid.uuid4()),
        label=call.data["label"],
        start_date=start,
        end_date=end,
        price=price,
    )
    await storage.async_add_period(period)
    coordinator.async_update_listeners()
    _LOGGER.info(
        "Added fixed-price period '%s' (%s – %s, %.3f c/kWh)", period.label, start, end, price
    )


async def _handle_remove_fixed_period(call: ServiceCall) -> None:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    period_id = call.data["period_id"]

    removed = await coordinator._storage.async_remove_period(period_id)
    if not removed:
        raise vol.Invalid(f"No fixed-price period with id '{period_id}'")

    coordinator.async_update_listeners()
    _LOGGER.info("Removed fixed-price period %s", period_id)


async def _handle_get_active_prices(call: ServiceCall) -> ServiceResponse:
    coordinator = _get_coordinator(call.hass, call.data.get("config_entry_id"))
    formatted: bool = call.data["formatted"]
    start: datetime | None = call.data.get("start")
    end: datetime | None = call.data.get("end")

    if start is not None and end is not None:
        slots = coordinator.slots_in_range(start, end)
    else:
        tomorrow = coordinator.tomorrow_slots() or []
        slots = coordinator.today_slots() + tomorrow

    def _slot_dict(slot) -> dict:
        slot_local = dt_util.as_local(slot.dt_utc)
        period = coordinator.fixed_period_for_date(slot_local.date())
        is_fixed = period is not None
        effective = period.price if is_fixed else coordinator._spot_effective(slot)
        transfer = coordinator.transfer_price_for_slot(slot) or 0.0
        return {
            "time": slot_local.isoformat(),
            "price": _fmt(coordinator, effective, formatted),
            "total_price": _fmt(coordinator, effective + transfer, formatted),
            "rank": slot.rank,
            "is_fixed": is_fixed,
        }

    return {
        "unit": coordinator.native_unit,
        "price_periods": [_slot_dict(s) for s in slots],
    }


async def _handle_list_fixed_periods(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))

    periods = coordinator._storage.periods
    return {
        "periods": [
            {
                "id": p.id,
                "label": p.label,
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "price": p.price,
                "active": p.is_active_on(coordinator._now_local().date()),
            }
            for p in periods
        ]
    }


async def _handle_get_export_prices(call: ServiceCall) -> ServiceResponse:
    coordinator = _get_coordinator(call.hass, call.data.get("config_entry_id"))
    formatted: bool = call.data["formatted"]
    start: datetime | None = call.data.get("start")
    end: datetime | None = call.data.get("end")

    if start is not None and end is not None:
        slots = coordinator.slots_in_range(start, end)
    else:
        tomorrow = coordinator.tomorrow_slots() or []
        slots = coordinator.today_slots() + tomorrow

    return {
        "unit": coordinator.native_unit,
        "price_periods": [
            {
                "time": dt_util.as_local(slot.dt_utc).isoformat(),
                "export_price": _fmt(
                    coordinator, coordinator.export_price_for_slot(slot), formatted
                ),
            }
            for slot in slots
        ],
    }


async def _handle_best_export_hours(call: ServiceCall) -> ServiceResponse:
    coordinator = _get_coordinator(call.hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]
    hours: float = call.data["hours"]
    formatted: bool = call.data["formatted"]

    slots = coordinator.slots_in_range(start, end)
    if not slots:
        return {"error": "No price slots available in the specified range"}

    resolution_minutes = coordinator._resolution.value
    slots_needed = max(1, round(hours * 60 / resolution_minutes))

    # Find consecutive window with highest average export price
    if slots_needed > len(slots):
        return {"error": f"Requested {hours}h but only {len(slots)} slots available in range"}

    best_start = 0
    best_avg = -1.0
    for i in range(len(slots) - slots_needed + 1):
        window = slots[i : i + slots_needed]
        prices = [coordinator.export_price_for_slot(s) for s in window]
        avg = sum(prices) / len(prices)
        if avg > best_avg:
            best_avg = avg
            best_start = i

    best_window = slots[best_start : best_start + slots_needed]
    return {
        "start": best_window[0].dt_utc.isoformat(),
        "end": best_window[-1].dt_utc.isoformat(),
        "average_export_price": _fmt(coordinator, best_avg, formatted),
        "unit": coordinator.native_unit,
        "price_periods": [
            {
                "time": dt_util.as_local(s.dt_utc).isoformat(),
                "export_price": _fmt(coordinator, coordinator.export_price_for_slot(s), formatted),
            }
            for s in best_window
        ],
    }


async def _handle_best_charge_hours(call: ServiceCall) -> ServiceResponse:
    coordinator = _get_coordinator(call.hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]
    hours: float = call.data["hours"]
    formatted: bool = call.data["formatted"]

    slots = coordinator.slots_in_range(start, end)
    if not slots:
        return {"error": "No price slots available in the specified range"}

    resolution_minutes = coordinator._resolution.value
    slots_needed = max(1, round(hours * 60 / resolution_minutes))

    if slots_needed > len(slots):
        return {"error": f"Requested {hours}h but only {len(slots)} slots available in range"}

    # Find the consecutive window with the lowest average total price
    best_start = 0
    best_avg = float("inf")
    for i in range(len(slots) - slots_needed + 1):
        window = slots[i : i + slots_needed]
        prices = coordinator._total_prices_for_slots(window)
        avg = sum(prices) / len(prices)
        if avg < best_avg:
            best_avg = avg
            best_start = i

    best_window = slots[best_start : best_start + slots_needed]
    return {
        "start": best_window[0].dt_utc.isoformat(),
        "end": best_window[-1].dt_utc.isoformat(),
        "average_total_price": _fmt(coordinator, best_avg, formatted),
        "unit": coordinator.native_unit,
        "price_periods": [
            {
                "time": dt_util.as_local(s.dt_utc).isoformat(),
                "total_price": _fmt(
                    coordinator,
                    coordinator._spot_effective(s)
                    + (coordinator.transfer_price_for_slot(s) or 0.0),
                    formatted,
                ),
            }
            for s in best_window
        ],
    }


async def _handle_generation_schedule(call: ServiceCall) -> ServiceResponse:
    coordinator = _get_coordinator(call.hass, call.data.get("config_entry_id"))
    formatted: bool = call.data["formatted"]
    forecast: list[dict] = call.data["forecast"]

    schedule = []
    for entry in forecast:
        slot_time: datetime = entry["time"]
        kwh: float = entry["kwh"]

        # Find the matching price slot
        matching = coordinator.slots_in_range(slot_time, slot_time + _SLOT_SEARCH_WINDOW)
        if not matching:
            schedule.append(
                {
                    "time": dt_util.as_local(slot_time).isoformat(),
                    "kwh": kwh,
                    "action": "unknown",
                    "self_consumption_value": None,
                    "export_price": None,
                }
            )
            continue

        slot = matching[0]
        transfer = coordinator.transfer_price_for_slot(slot) or 0.0
        self_value = coordinator._spot_effective(slot) + transfer
        export_p = coordinator.export_price_for_slot(slot)

        # Recommendation: self-consume is almost always better due to VAT + transfer
        # Export recommended only if export_price >= self_consumption_value (very rare)
        if export_p >= self_value:
            action = "export"
        elif kwh > 0:
            action = "self_consume"
        else:
            action = "idle"

        schedule.append(
            {
                "time": dt_util.as_local(slot.dt_utc).isoformat(),
                "kwh": kwh,
                "action": action,
                "self_consumption_value": _fmt(coordinator, self_value, formatted),
                "export_price": _fmt(coordinator, export_p, formatted),
            }
        )

    return {
        "unit": coordinator.native_unit,
        "schedule": schedule,
    }


# Slot search window: wide enough to match the start of any resolution slot
_SLOT_SEARCH_WINDOW = timedelta(minutes=60)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def async_register_services(hass: HomeAssistant) -> None:
    """Register all Kilowahti services. Safe to call multiple times."""
    if hass.services.has_service(DOMAIN, "get_prices"):
        return  # Already registered

    hass.services.async_register(
        DOMAIN,
        "get_active_prices",
        _handle_get_active_prices,
        schema=GET_ACTIVE_PRICES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_prices",
        _handle_get_prices,
        schema=GET_PRICES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "cheapest_hours",
        _handle_cheapest_hours,
        schema=CHEAPEST_HOURS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "average_price",
        _handle_average_price,
        schema=AVERAGE_PRICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "add_fixed_period",
        _handle_add_fixed_period,
        schema=ADD_FIXED_PERIOD_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "remove_fixed_period",
        _handle_remove_fixed_period,
        schema=REMOVE_FIXED_PERIOD_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "list_fixed_periods",
        _handle_list_fixed_periods,
        schema=LIST_FIXED_PERIODS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_export_prices",
        _handle_get_export_prices,
        schema=GET_EXPORT_PRICES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "best_export_hours",
        _handle_best_export_hours,
        schema=BEST_EXPORT_HOURS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "best_charge_hours",
        _handle_best_charge_hours,
        schema=BEST_CHARGE_HOURS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "generation_schedule",
        _handle_generation_schedule,
        schema=GENERATION_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    _LOGGER.debug("Kilowahti services registered")


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister services when the last entry is removed."""
    for service in (
        "get_active_prices",
        "get_prices",
        "cheapest_hours",
        "average_price",
        "add_fixed_period",
        "remove_fixed_period",
        "list_fixed_periods",
        "get_export_prices",
        "best_export_hours",
        "best_charge_hours",
        "generation_schedule",
    ):
        hass.services.async_remove(DOMAIN, service)
