"""Resolve automation trigger/condition targets to a Kilowahti config entry.

Kilowahti triggers and conditions are scoped to a single config entry (one per
configured region/site). A target may reference that entry via a device or an
entity; when no target is given and exactly one entry is loaded, that entry is
used implicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import KilowahtiCoordinator


def _as_list(value: Any) -> list[str]:
    """Normalize a target field (str or list) into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def resolve_entry_id(hass: HomeAssistant, target: dict[str, Any] | None) -> str | None:
    """Resolve a target to a single loaded Kilowahti config entry id.

    Returns None when the target cannot be resolved to exactly one entry.
    """
    entries: list[str] = list(hass.data.get(DOMAIN, {}).keys())
    if not entries:
        return None

    if target:
        dev_reg = dr.async_get(hass)
        for device_id in _as_list(target.get(ATTR_DEVICE_ID)):
            device = dev_reg.async_get(device_id)
            if device is None:
                continue
            for domain, ident in device.identifiers:
                if domain == DOMAIN and ident in entries:
                    return ident

        ent_reg = er.async_get(hass)
        for entity_id in _as_list(target.get(ATTR_ENTITY_ID)):
            entity = ent_reg.async_get(entity_id)
            if entity is not None and entity.config_entry_id in entries:
                return entity.config_entry_id

    # No target (or unresolved): fall back to the sole entry when unambiguous.
    return entries[0] if len(entries) == 1 else None


def resolve_coordinator(
    hass: HomeAssistant, target: dict[str, Any] | None
) -> KilowahtiCoordinator | None:
    """Resolve a target to its Kilowahti coordinator, or None."""
    entry_id = resolve_entry_id(hass, target)
    if entry_id is None:
        return None
    return hass.data.get(DOMAIN, {}).get(entry_id)
