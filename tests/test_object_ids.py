"""Entity IDs must not depend on the user's language."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kilowahti.const import CONF_SCORE_PROFILES, DOMAIN
from homeassistant.helpers import entity_registry as er

from .conftest import TODAY_PAYLOAD, TODAY_URL_RE, TOMORROW_URL_RE


async def _ids_for_language(hass, options, lang: str) -> dict[str, str]:
    await hass.config.async_set_time_zone("UTC")
    await hass.config.async_update(language=lang)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Home",
        options={
            **options,
            CONF_SCORE_PROFILES: [
                {"id": "total", "label": "Total", "meters": [], "formula": "default"}
            ],
        },
    )
    with aioresponses() as m:
        m.get(TODAY_URL_RE, payload=TODAY_PAYLOAD, repeat=True)
        m.get(TOMORROW_URL_RE, status=404, repeat=True)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    return {
        e.unique_id.replace(f"{entry.entry_id}_", ""): e.entity_id
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
    }


@pytest.mark.parametrize("lang", ["fi", "de", "sv"])
async def test_entity_ids_do_not_follow_the_ui_language(hass, options, mock_utcnow, lang):
    """A Finnish install shows Finnish names but keeps the English entity IDs."""
    ids = await _ids_for_language(hass, options, lang)

    assert ids["spot_price"] == "sensor.kilowahti_test_home_spot_price"
    assert ids["price_rank"] == "sensor.kilowahti_test_home_price_rank"
    assert ids["active_price"] == "sensor.kilowahti_test_home_active_price"
    assert ids["tomorrow_average"] == "sensor.kilowahti_test_home_tomorrow_average"
    assert ids["tomorrow_available"] == "binary_sensor.kilowahti_test_home_tomorrow_available"
    assert ids["number_price_threshold"] == "number.kilowahti_test_home_price_threshold"
    assert ids["score_total_daily"] == "sensor.kilowahti_test_home_score_total_daily"


async def test_every_entity_id_is_its_key(hass, options, mock_utcnow):
    """One rule for the whole integration, so the docs can list a single set of IDs."""
    ids = await _ids_for_language(hass, options, "en")

    # The number entities carry a "number_" prefix in their unique_id only
    prefixed = {
        "number_price_threshold": "price_threshold",
        "number_rank_threshold": "rank_threshold",
    }
    for key, entity_id in ids.items():
        expected = prefixed.get(key, key)
        object_id = entity_id.split(".", 1)[1]
        assert object_id == f"kilowahti_test_home_{expected}", f"{key} became {entity_id}"


async def test_translated_names_are_unaffected(hass, options, mock_utcnow):
    """Only the IDs are pinned; the displayed name still follows the language."""
    ids = await _ids_for_language(hass, options, "fi")

    state = hass.states.get(ids["spot_price"])
    assert state.attributes["friendly_name"].endswith("Spot-hinta")
