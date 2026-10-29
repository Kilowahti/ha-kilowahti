"""One-time entity registry migrations.

Renaming an entity key changes its unique_id, which Home Assistant would treat
as a different entity: the old one becomes an orphan and the new one starts
without history. Rewriting the stored unique_id instead keeps the entity, its
entity_id, its history and its long-term statistics intact.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)

# Keys renamed in 2026.9.0, old suffix -> new suffix.
_RENAMED_KEYS: dict[str, str] = {
    "effective_price": "active_price",
    "setting_max_price": "setting_price_threshold",
    "setting_acceptable_rank": "setting_rank_threshold",
    "setting_control_factor_function": "setting_control_factor_shape",
    "today_spot_avg": "today_spot_average",
    "today_spot_min": "today_spot_minimum",
    "today_spot_max": "today_spot_maximum",
    "tomorrow_spot_avg": "tomorrow_spot_average",
    "tomorrow_spot_min": "tomorrow_spot_minimum",
    "tomorrow_spot_max": "tomorrow_spot_maximum",
    "today_total_avg": "today_total_average",
    "today_total_min": "today_total_minimum",
    "today_total_max": "today_total_maximum",
    "tomorrow_total_avg": "tomorrow_total_average",
    "tomorrow_total_min": "tomorrow_total_minimum",
    "tomorrow_total_max": "tomorrow_total_maximum",
    "today_avg": "today_average",
    "today_min": "today_minimum",
    "today_max": "today_maximum",
    "tomorrow_avg": "tomorrow_average",
    "tomorrow_min": "tomorrow_minimum",
    "tomorrow_max": "tomorrow_maximum",
    "next_hours_avg": "next_hours_average",
    "spot_next_hours_avg": "spot_next_hours_average",
    "export_today_avg": "export_today_average",
    "export_today_min": "export_today_minimum",
    "export_today_max": "export_today_maximum",
    "export_tomorrow_avg": "export_tomorrow_average",
    "export_tomorrow_min": "export_tomorrow_minimum",
    "export_tomorrow_max": "export_tomorrow_maximum",
    "next_solar_window_avg": "next_solar_window_average",
    "avg_price_30min": "average_price_30min",
    "avg_price_60min": "average_price_60min",
    "avg_price_120min": "average_price_120min",
    "fixed_period_active": "fixed_price_period_active",
}


def async_migrate_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Adopt entities registered under a pre-2026.9.0 key."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    taken = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}

    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not entity.unique_id.startswith(prefix):
            continue
        old_key = entity.unique_id[len(prefix) :]
        new_key = _RENAMED_KEYS.get(old_key)
        if new_key is None:
            continue

        new_unique_id = f"{prefix}{new_key}"
        if new_unique_id in taken:
            # Both exist, so the rename already ran and produced a duplicate;
            # leave the registry alone rather than guessing which one to keep.
            _LOGGER.debug("Skipping migration of %s: %s already exists", old_key, new_key)
            continue

        _LOGGER.debug("Migrating unique_id %s -> %s", old_key, new_key)
        registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)
        taken.discard(entity.unique_id)
        taken.add(new_unique_id)


def _stable_entity_id(hass: HomeAssistant, entity: er.RegistryEntry) -> tuple[str, str] | None:
    """Return the stable entity ID and the ID Home Assistant generated at registration.

    The generated ID is built from the display name stored in the registry; an
    entity whose current ID differs from it was renamed by the user. Returns None
    for entities without a device.
    """
    if entity.device_id is None or entity.config_entry_id is None:
        return None
    device = dr.async_get(hass).async_get(entity.device_id)
    if device is None or device.name is None:
        return None

    key = entity.unique_id.removeprefix(f"{entity.config_entry_id}_")
    if entity.domain == "number":
        key = key.removeprefix("number_")
    device_slug = slugify(device.name)
    generated = f"{entity.domain}.{slugify(f'{device.name} {entity.original_name}')}"
    return f"{entity.domain}.{device_slug}_{key}", generated


def async_find_renamable_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Entity IDs still on the display-name scheme that the user has not customized."""
    registry = er.async_get(hass)
    found = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        ids = _stable_entity_id(hass, entity)
        if ids is None:
            continue
        stable, generated = ids
        if entity.entity_id == generated and entity.entity_id != stable:
            found.append(entity.entity_id)
    return found


def async_rename_to_stable_entity_ids(
    hass: HomeAssistant, entry_id: str, entity_ids: list[str]
) -> int:
    """Rename the given entities to their stable IDs, returning how many changed.

    Entities that no longer exist or whose stable ID is already taken are skipped.
    """
    registry = er.async_get(hass)
    renamed = 0
    for entity_id in entity_ids:
        entity = registry.async_get(entity_id)
        if entity is None or entity.config_entry_id != entry_id:
            continue
        ids = _stable_entity_id(hass, entity)
        if ids is None or ids[0] == entity_id:
            continue
        if registry.async_get(ids[0]) is not None or hass.states.get(ids[0]) is not None:
            _LOGGER.warning("Cannot rename %s to %s: ID already in use", entity_id, ids[0])
            continue
        registry.async_update_entity(entity_id, new_entity_id=ids[0])
        renamed += 1
    return renamed
