"""The Home Assistant test helpers these tests use, from whichever package has them.

From 2026.11, Home Assistant publishes them itself as pytest-homeassistant. The
oldest release this integration supports, which the min job tests, only has the
community package they came from, pytest-homeassistant-custom-component, with the
same modules under another name. Once hacs.json's minimum reaches 2026.11, delete
this module and import from pytest_homeassistant directly.
"""

from __future__ import annotations

try:
    from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
except ModuleNotFoundError:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
    )

__all__ = ["MockConfigEntry", "async_fire_time_changed"]
