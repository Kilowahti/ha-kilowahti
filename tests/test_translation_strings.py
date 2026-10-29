"""Hassfest string rules for exceptions and issues in every translation file."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_DIR = Path(__file__).parent.parent / "custom_components" / "kilowahti" / "translations"
_FILES = sorted(_DIR.glob("*.json"))


def _strings(data: dict, prefix: str = ""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _strings(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.stem)
def test_exception_and_issue_strings_follow_hassfest_rules(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for section in ("exceptions", "issues"):
        for key, text in _strings(data[section], f"{section}."):
            assert "http" not in text, f"{key}: URLs must be description placeholders"
            assert not re.search(r"'\{\w+\}'", text), f"{key}: placeholder inside single quotes"
