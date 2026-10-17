"""The coordinator: polling, reconnecting, and keeping scene names right."""

from __future__ import annotations

from bleak.exc import BleakError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nanoleaf_ble.const import DATA_STORE, DOMAIN
from custom_components.nanoleaf_ble.coordinator import NanoleafCoordinator
from custom_components.nanoleaf_ble.lib import default_scenes
from custom_components.nanoleaf_ble.library import StripSceneRecord
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .common import FakeLegacyStrip
from .conftest import Bluetooth


def _coordinator(entry: MockConfigEntry) -> NanoleafCoordinator:
    return entry.runtime_data


def _light_state(hass: HomeAssistant, entry: MockConfigEntry):
    entity_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, entry.entry_id
    )
    return hass.states.get(entity_id)


async def test_link_loss_reconnects(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    connections = bluetooth.connections
    await bluetooth.connected.disconnect()
    assert not coordinator.connected
    # The reconnect timer fires after RECONNECT_DELAY (0 in tests).
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    assert coordinator.connected
    assert bluetooth.connections > connections


def _connection_state(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_connection"
    )
    return hass.states.get(entity_id).state


async def test_connection_sensor_follows_the_link(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """Off as soon as the link drops, without waiting for a poll; never unavailable."""
    coordinator = _coordinator(init_integration)
    assert _connection_state(hass, init_integration) == STATE_ON

    bluetooth.in_range = False
    await bluetooth.connected.disconnect()
    assert _connection_state(hass, init_integration) == STATE_OFF
    # The next poll cannot reconnect: the rest goes unavailable, the sensor not.
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert _light_state(hass, init_integration).state == STATE_UNAVAILABLE
    assert _connection_state(hass, init_integration) == STATE_OFF

    bluetooth.in_range = True
    await coordinator.async_refresh()
    assert _connection_state(hass, init_integration) == STATE_ON


async def test_unavailable_while_out_of_range(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    bluetooth.in_range = False
    bluetooth.connected.fail_next = BleakError("gone")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert _light_state(hass, init_integration).state == STATE_UNAVAILABLE

    bluetooth.in_range = True
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert _light_state(hass, init_integration).state != STATE_UNAVAILABLE


async def test_command_retries_once_after_a_drop(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    bluetooth.connected.fail_next = BleakError("dropped")
    await coordinator.async_set_light(on=True)
    assert bluetooth.state.on


async def test_command_fails_after_two_drops(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    bluetooth.connected.fail_next = BleakError("dropped")
    broken = FakeLegacyStrip(bluetooth.state, tokens=bluetooth.tokens)
    broken.fail_next = BleakError("dropped again")
    bluetooth.queued = [broken]
    # The reconnect logs in (two writes); fail on the command after it.
    original = broken.write_gatt_char
    calls = 0

    async def write(char, data, response=True):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise BleakError("dropped again")
        broken.fail_next = None
        await original(char, data, response)

    broken.write_gatt_char = write  # type: ignore[method-assign]
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_light(on=True)
    assert err.value.translation_key == "command_failed"


async def test_token_refused_during_a_command_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    bluetooth.tokens.clear()
    await bluetooth.connected.disconnect()
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_light(on=True)
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_token_refused_during_a_poll(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    bluetooth.tokens.clear()
    await bluetooth.connected.disconnect()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_one_refusal_is_retried_on_a_fresh_connection(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """A refusal the strip does not repeat does not ask the user to pair again."""
    coordinator = _coordinator(init_integration)
    connections = bluetooth.connections
    bluetooth.queued.append(FakeLegacyStrip(bluetooth.state, tokens=set()))
    await bluetooth.connected.disconnect()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.connected
    assert coordinator.last_update_success
    assert bluetooth.connections == connections + 2
    assert bluetooth.overlaps == 0
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_transient_error_during_login_does_not_start_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)

    class Hiccup(FakeLegacyStrip):
        async def read_gatt_char(self, char):
            if self._phase == "token":
                raise BleakError(
                    "Bluetooth GATT Error address=X handle=142 error=133 "
                    "description=Error"
                )
            return await super().read_gatt_char(char)

    bluetooth.queued.append(Hiccup(bluetooth.state))
    await bluetooth.connected.disconnect()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_protocol_error_keeps_the_link(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = _coordinator(init_integration)
    bluetooth.state.statuses["lb/0/oo"] = 2
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.connected
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_light(on=True)
    assert err.value.translation_key == "command_failed"


async def test_unsupported_command(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.state.statuses["lb/0/ct"] = 4
    with pytest.raises(HomeAssistantError) as err:
        await _coordinator(init_integration).async_set_light(colour_temperature=3000)
    assert err.value.translation_key == "not_supported"


async def test_old_firmware_without_power_loss_recovery(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)

    # The strip answers the power-loss query with "invalid endpoint".
    original = bluetooth.state.handle

    def handle(method, endpoint, value):
        if endpoint == "ci" and value and value[0] == 0x09:
            return b"\x04"
        return original(method, endpoint, value)

    bluetooth.state.handle = handle  # type: ignore[method-assign]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    coordinator = _coordinator(config_entry)
    assert not coordinator.supports_power_loss_recovery
    assert coordinator.data.power_loss_recovery is None
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_scene_names_follow_their_content(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """A stale record is dropped; a scene matching the library takes its name."""
    coordinator = _coordinator(init_integration)
    store = hass.data[DATA_STORE]
    address = init_integration.unique_id
    defaults = default_scenes("NL62")

    # The integration stored "Mine" as scene 2; the app then replaced it.
    store.async_set_strip_scene(
        address, 2, StripSceneRecord("Mine", defaults[244].fingerprint)
    )
    bluetooth.state.scenes[2] = defaults[245].encode(2)
    # The app stored a copy of a library scene as scene 5.
    item = store.async_add("Cyber copy", defaults[246])
    bluetooth.state.scenes[5] = defaults[246].encode(5)
    # And a scene nobody knows as scene 7.
    bluetooth.state.scenes[7] = defaults[247].encode(7)

    await coordinator.async_refresh()
    assert coordinator.scene_name(2) == "Scene 2"
    assert coordinator.scene_name(5) == "Cyber copy"
    assert store.strip_scenes(address)[5].library_id == item.id
    assert coordinator.scene_name(7) == "Scene 7"
    assert "Cyber copy" in coordinator.effects
    assert list(coordinator.effects).count("Cyber copy") == 1  # not twice

    # A recorded scene that is still the same keeps its name.
    store.async_set_strip_scene(
        address, 7, StripSceneRecord("Kept", defaults[247].fingerprint)
    )
    coordinator._checked_scenes.discard(7)
    await coordinator.async_refresh()
    assert coordinator.scene_name(7) == "Kept"

    # Scenes that leave the strip are forgotten.
    del bluetooth.state.scenes[7]
    await coordinator.async_refresh()
    assert 7 not in store.strip_scenes(address)


async def test_duplicate_names_are_made_unique(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    store = hass.data[DATA_STORE]
    store.async_add("cloud9", default_scenes("NL62")[244])
    effects = list(_coordinator(init_integration).effects)
    assert "Cloud9" in effects
    assert "cloud9 (2)" in effects
