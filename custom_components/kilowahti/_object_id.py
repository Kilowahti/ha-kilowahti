"""Language-independent entity ID suggestions.

Home Assistant builds an entity's ID from its translated display name, so the
same sensor becomes `sensor.kilowahti_home_spot_price` in English and
`sensor.kilowahti_home_spot_hinta` in Finnish. Kilowahti suggests an object ID
derived from the entity key instead, so IDs are identical on every install and
automations, blueprints and documentation carry across languages. Displayed
names remain translated.

Only entities registered from 2026.9.0 onwards are affected; Home Assistant
assigns an entity ID once, at first registration, and never revisits it.
"""

from __future__ import annotations


def stable_object_id(key: str) -> str:
    """Return the object ID suggestion for an entity key."""
    return key.replace("_", " ")
