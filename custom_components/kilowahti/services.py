"""Service handlers for the Kilowahti integration."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import KilowahtiCoordinator
from .models import FixedPeriod

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_OPT_ENTRY_ID = vol.Optional("config_entry_id")

GET_PRICES_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
    }
)

CHEAPEST_HOURS_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24)),
    }
)

AVERAGE_PRICE_SCHEMA = vol.Schema(
    {
        _OPT_ENTRY_ID: cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
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
# Service handlers
# ---------------------------------------------------------------------------


async def _handle_get_prices(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]

    slots = coordinator.slots_in_range(start, end)
    return {
        "slots": [
            {
                "time": slot.dt_utc.isoformat(),
                "price_no_tax": slot.price_no_tax,
                "price": coordinator.format_price(coordinator._spot_effective(slot)),
                "rank": slot.rank,
                "unit": coordinator.native_unit,
            }
            for slot in slots
        ]
    }


async def _handle_cheapest_hours(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    start: datetime = call.data["start"]
    end: datetime = call.data["end"]
    hours: float = call.data["hours"]

    slots = coordinator.slots_in_range(start, end)
    if not slots:
        return {"error": "No price slots available in the specified range"}

    resolution_minutes = coordinator._resolution.value
    slots_needed = max(1, round(hours * 60 / resolution_minutes))

    if slots_needed > len(slots):
        return {"error": f"Requested {hours}h but only {len(slots)} slots available in range"}

    # Sliding window: find the window of `slots_needed` consecutive slots with lowest avg
    best_start_idx = 0
    best_total = sum(coordinator._spot_effective(s) for s in slots[:slots_needed])
    current_total = best_total

    for i in range(1, len(slots) - slots_needed + 1):
        current_total -= coordinator._spot_effective(slots[i - 1])
        current_total += coordinator._spot_effective(slots[i + slots_needed - 1])
        if current_total < best_total:
            best_total = current_total
            best_start_idx = i

    best_window = slots[best_start_idx : best_start_idx + slots_needed]
    avg_price = best_total / slots_needed

    return {
        "start": best_window[0].dt_utc.isoformat(),
        "end": best_window[-1].dt_utc.isoformat(),
        "average_price": coordinator.format_price(avg_price),
        "unit": coordinator.native_unit,
        "slots": [
            {
                "time": s.dt_utc.isoformat(),
                "price": coordinator.format_price(coordinator._spot_effective(s)),
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

    slots = coordinator.slots_in_range(start, end)
    if not slots:
        return {"error": "No price slots available in the specified range"}

    prices = [coordinator._spot_effective(s) for s in slots]
    avg = sum(prices) / len(prices)

    return {
        "average_price": coordinator.format_price(avg),
        "min_price": coordinator.format_price(min(prices)),
        "max_price": coordinator.format_price(max(prices)),
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
    overlap = any(
        not (end < p.start_date or start > p.end_date)
        for p in storage.periods
    )
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
    _LOGGER.info("Added fixed-price period '%s' (%s – %s, %.3f snt/kWh)", period.label, start, end, price)


async def _handle_remove_fixed_period(call: ServiceCall) -> None:
    hass = call.hass
    coordinator = _get_coordinator(hass, call.data.get("config_entry_id"))
    period_id = call.data["period_id"]

    removed = await coordinator._storage.async_remove_period(period_id)
    if not removed:
        raise vol.Invalid(f"No fixed-price period with id '{period_id}'")

    coordinator.async_update_listeners()
    _LOGGER.info("Removed fixed-price period %s", period_id)


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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def async_register_services(hass: HomeAssistant) -> None:
    """Register all Kilowahti services. Safe to call multiple times."""
    if hass.services.has_service(DOMAIN, "get_prices"):
        return  # Already registered

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
    _LOGGER.debug("Kilowahti services registered")


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister services when the last entry is removed."""
    for service in ("get_prices", "cheapest_hours", "average_price",
                    "add_fixed_period", "remove_fixed_period", "list_fixed_periods"):
        hass.services.async_remove(DOMAIN, service)
