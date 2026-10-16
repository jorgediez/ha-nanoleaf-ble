"""Guard against Home Assistant APIs that are scheduled for removal.

These are source scans rather than imports, so they also catch uses on code
paths the other tests do not reach.
"""

from __future__ import annotations

from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "nanoleaf_ble"

SOURCES = sorted(INTEGRATION.glob("*.py"))


def test_the_scan_actually_sees_the_integration():
    """A typo in the glob would make every test below pass vacuously."""
    assert SOURCES, f"no Python sources found under {INTEGRATION}"
    assert (INTEGRATION / "light.py") in SOURCES


def test_platforms_use_the_config_entry_entities_callback():
    """Config-entry platforms take AddConfigEntryEntitiesCallback, not the old alias."""
    platforms = [
        p for p in SOURCES if "async_add_entities" in p.read_text(encoding="utf-8")
    ]
    assert platforms, "no platform modules found"
    for path in platforms:
        text = path.read_text(encoding="utf-8")
        assert "AddConfigEntryEntitiesCallback" in text, path.name
        assert "import AddEntitiesCallback" not in text, path.name


def test_no_mireds():
    """Colour temperature is in Kelvin only; the mired attributes are deprecated."""
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "ATTR_COLOR_TEMP," not in text, path.name
        assert "_attr_min_mireds" not in text, path.name
        assert "_attr_max_mireds" not in text, path.name


def test_platforms_set_parallel_updates():
    for name in ("button", "light", "select", "sensor", "switch", "text"):
        text = (INTEGRATION / f"{name}.py").read_text(encoding="utf-8")
        assert "PARALLEL_UPDATES" in text, name
