"""Tests for icons.json validity and coverage."""

from __future__ import annotations

import json
from pathlib import Path

_BASE = Path(__file__).parent.parent / "custom_components" / "kilowahti"


def test_icons_json_keys_exist_in_translations():
    icons = json.loads((_BASE / "icons.json").read_text())
    entity_translations = json.loads((_BASE / "translations" / "en.json").read_text())["entity"]

    assert icons["entity"]
    for platform, keys in icons["entity"].items():
        assert platform in entity_translations, f"unknown platform {platform!r} in icons.json"
        for key in keys:
            assert key in entity_translations[platform], (
                f"icons.json has {platform}.{key} but en.json entity section does not"
            )
