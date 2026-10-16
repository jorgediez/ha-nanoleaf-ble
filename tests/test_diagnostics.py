"""Diagnostics leave out what identifies a user or controls the strip."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nanoleaf_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from .common import ADDRESS, EUI, SERIAL, TOKEN
from .conftest import Bluetooth


async def test_redacted(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    data = diagnostics["entry"]["data"]
    assert data["address"] == REDACTED
    assert data["token"] == REDACTED
    assert data["local_name"] == REDACTED

    dumped = json.dumps(diagnostics)
    for secret in (ADDRESS, TOKEN.hex(), SERIAL, EUI):
        assert secret not in dumped, secret

    assert diagnostics["advertisement"]["model"] == "NL62"
    assert diagnostics["capabilities"]["protocol"] == "legacy"
    assert diagnostics["state"]["free_slots"] == 2
    assert diagnostics["coordinator"]["last_exception"] is None


async def test_last_exception_is_readable(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.state.statuses["lb/0/oo"] = 2
    await init_integration.runtime_data.async_refresh()
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    assert diagnostics["coordinator"]["last_exception"].startswith("UpdateFailed: ")
