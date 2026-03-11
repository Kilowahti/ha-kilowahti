"""Number platform for the Kilowahti integration."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NUMBER_PRICE_THRESHOLD, NUMBER_RANK_THRESHOLD, UNIT_EUROKWH
from .coordinator import KilowahtiCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: KilowahtiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            KilowahtiPriceThresholdNumber(coordinator, entry),
            KilowahtiRankThresholdNumber(coordinator, entry),
        ]
    )


class KilowahtiPriceThresholdNumber(CoordinatorEntity[KilowahtiCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_translation_key = NUMBER_PRICE_THRESHOLD
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KilowahtiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_number_{NUMBER_PRICE_THRESHOLD}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.native_unit

    @property
    def native_min_value(self) -> float:
        return 0.0

    @property
    def native_max_value(self) -> float:
        return 5.0 if self.coordinator.native_unit == UNIT_EUROKWH else 500.0

    @property
    def native_step(self) -> float:
        return 0.001 if self.coordinator.native_unit == UNIT_EUROKWH else 0.1

    @property
    def native_value(self) -> float:
        return self.coordinator.format_price(self.coordinator._max_price)

    async def async_set_native_value(self, value: float) -> None:
        value_snt = value * 100.0 if self.coordinator.native_unit == UNIT_EUROKWH else value
        self.coordinator.set_price_threshold(value_snt)


class KilowahtiRankThresholdNumber(CoordinatorEntity[KilowahtiCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_translation_key = NUMBER_RANK_THRESHOLD
    _attr_native_min_value = 1
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KilowahtiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_number_{NUMBER_RANK_THRESHOLD}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_max_value(self) -> float:
        return float(self.coordinator._resolution.slots_per_day)

    @property
    def native_value(self) -> float:
        return float(self.coordinator._max_rank)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_rank_threshold(int(value))
