"""Checks on the translations, which are kept in English only."""

from __future__ import annotations

import json
from pathlib import Path
import re

import yaml

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "nanoleaf_ble"
EN = json.loads((INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8"))
SOURCES = {p.name: p.read_text(encoding="utf-8") for p in INTEGRATION.glob("*.py")}

#: Every way the code names an exception's translation key.
RAISED = (
    r'translation_domain=DOMAIN,\s*translation_key="(\w+)"',
    r'_error\(\s*"(\w+)"',
    r'_no_selection\(\s*"(\w+)"',
)
#: How the code names a repair issue's translation key.
ISSUES = r'severity=ir\.IssueSeverity\.\w+,\s*translation_key="(\w+)"'


def _found(*patterns: str) -> set[str]:
    return {
        match
        for text in SOURCES.values()
        for pattern in patterns
        for match in re.findall(pattern, text)
    }


def test_every_raised_error_has_a_message():
    """A missing message shows the bare translation key to the user."""
    raised = _found(*RAISED)
    assert raised, "the scan found no translated errors"
    assert raised == set(EN["exceptions"])


def test_every_issue_has_a_title_and_description():
    issues = _found(ISSUES)
    assert issues == set(EN["issues"])
    for key in issues:
        assert {"title", "description"} <= set(EN["issues"][key])


def test_every_entity_has_a_name():
    keys = _found(r'_attr_translation_key = "(\w+)"', r'translation_key="(\w+)",\n')
    names = {key for platform in EN["entity"].values() for key in platform}
    missing = keys - names - set(EN["exceptions"]) - set(EN["issues"])
    assert not missing
    assert names <= keys


def test_every_action_and_field_is_described():
    services = yaml.safe_load(
        (INTEGRATION / "services.yaml").read_text(encoding="utf-8")
    )
    assert set(services) == set(EN["services"])
    for name, service in services.items():
        fields = set((service or {}).get("fields", {}))
        assert fields == set(EN["services"][name].get("fields", {})), name


def test_flow_placeholders_are_provided():
    """Only placeholders the flow passes appear in its texts."""
    for step in ("pair", "reauth_confirm", "reconfigure"):
        text = json.dumps(EN["config"]["step"][step])
        assert set(re.findall(r"\{(\w+)\}", text)) <= {"name", "firmware"}


def _strings(tree: dict, prefix: str = ""):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from _strings(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


def test_braces_are_only_placeholders():
    """Hassfest rejects any {...} that is not a placeholder name."""
    bad = [
        key
        for key, text in _strings(EN)
        if re.search(r"\{(?![A-Za-z_][A-Za-z0-9_]*\})", text)
    ]
    assert not bad


def test_no_urls():
    """Hassfest wants URLs passed as placeholders, not written in the text."""
    assert not [key for key, text in _strings(EN) if "://" in text]
