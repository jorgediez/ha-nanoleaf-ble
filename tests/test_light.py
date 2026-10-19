"""The light: power, brightness, colour, and scenes as effects."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nanoleaf_ble.const import DATA_STORE, DOMAIN
from custom_components.nanoleaf_ble.lib import default_scenes
from custom_components.nanoleaf_ble.light import (
    brightness_to_percent,
    percent_to_brightness,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_HS_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import Bluetooth


def light_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Find the light by unique id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, entry.entry_id
    )
    assert entity_id is not None
    return entity_id


async def _call(hass: HomeAssistant, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(
        LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_state(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    state = hass.states.get(light_id(hass, init_integration))
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.HS]
    assert state.attributes[ATTR_EFFECT_LIST] == sorted(
        state.attributes[ATTR_EFFECT_LIST], key=str.casefold
    )
    assert "Northern Lights" in state.attributes[ATTR_EFFECT_LIST]
    assert len(state.attributes[ATTR_EFFECT_LIST]) == 10


async def test_turn_on_off_brightness_colour(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    entity_id = light_id(hass, init_integration)
    await _call(hass, "turn_on", entity_id, **{ATTR_BRIGHTNESS: 128})
    assert bluetooth.state.on
    assert bluetooth.state.brightness == 50
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 128

    await _call(hass, "turn_on", entity_id, **{ATTR_HS_COLOR: (240, 100)})
    assert (bluetooth.state.hue, bluetooth.state.saturation) == (240, 100)
    assert hass.states.get(entity_id).attributes[ATTR_HS_COLOR] == (240, 100)

    await _call(hass, "turn_off", entity_id)
    assert not bluetooth.state.on
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_play_strip_scene_and_dim_it(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    entity_id = light_id(hass, init_integration)
    await _call(
        hass, "turn_on", entity_id, **{ATTR_EFFECT: "cloud9", ATTR_BRIGHTNESS: 26}
    )
    assert bluetooth.state.current == 252
    assert bluetooth.state.brightness == 10
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_EFFECT] == "Cloud9"

    # A colour replaces the scene.
    await _call(hass, "turn_on", entity_id, **{ATTR_HS_COLOR: (10, 50)})
    assert bluetooth.state.current == 0
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] is None


async def test_library_scene_plays_as_preview(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    store = hass.data[DATA_STORE]
    store.async_add("Sunset", default_scenes("NL62")[246])
    await hass.async_block_till_done()
    entity_id = light_id(hass, init_integration)
    assert "Sunset" in hass.states.get(entity_id).attributes[ATTR_EFFECT_LIST]

    await _call(hass, "turn_on", entity_id, **{ATTR_EFFECT: "Sunset"})
    assert bluetooth.state.current == 0xFF
    assert bluetooth.state.previews
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] == "Sunset"

    # The next poll still names it.
    await init_integration.runtime_data.async_refresh()
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] == "Sunset"


async def test_unknown_effect(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError):
        await _call(
            hass, "turn_on", light_id(hass, init_integration), **{ATTR_EFFECT: "Nope"}
        )


async def test_scene_started_elsewhere(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """The phone app plays The Joker; the next poll reports it."""
    bluetooth.state.on, bluetooth.state.current = True, 248
    await init_integration.runtime_data.async_refresh()
    state = hass.states.get(light_id(hass, init_integration))
    assert state.state == STATE_ON
    assert state.attributes[ATTR_EFFECT] == "The Joker"

    # A preview started elsewhere has no known name.
    bluetooth.state.current = 0xFF
    await init_integration.runtime_data.async_refresh()
    assert (
        hass.states.get(light_id(hass, init_integration)).attributes[ATTR_EFFECT]
        is None
    )


async def test_colour_temperature(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.state.colour_temperature = 2700
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = light_id(hass, config_entry)
    assert set(hass.states.get(entity_id).attributes[ATTR_SUPPORTED_COLOR_MODES]) == {
        ColorMode.HS,
        ColorMode.COLOR_TEMP,
    }
    await _call(hass, "turn_on", entity_id, **{ATTR_COLOR_TEMP_KELVIN: 4000})
    assert bluetooth.state.colour_temperature == 4000
    state = hass.states.get(entity_id)
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 4000
    await hass.config_entries.async_unload(config_entry.entry_id)


def test_brightness_scaling():
    assert brightness_to_percent(1) == 1
    assert brightness_to_percent(255) == 100
    assert percent_to_brightness(100) == 255
    assert percent_to_brightness(0) == 0


async def test_turn_on_polls_soon_for_the_resumed_scene(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """Switched on without a colour, the strip resumes its last scene."""
    entity_id = light_id(hass, init_integration)
    coordinator = init_integration.runtime_data
    # Off, the strip reports no scene; the one it will resume is Cloud9.
    bluetooth.state.current = 252
    await _call(hass, "turn_on", entity_id)
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] is None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] == "Cloud9"

    # With a colour, the state is known already: no extra poll.
    await _call(hass, "turn_off", entity_id)
    await _call(hass, "turn_on", entity_id, **{ATTR_HS_COLOR: (120, 100)})
    assert coordinator._refresh_timer is None


async def test_storing_a_scene_polls_soon_for_it(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """The strip plays a scene as soon as it is stored."""
    entity_id = light_id(hass, init_integration)
    coordinator = init_integration.runtime_data
    item = hass.data[DATA_STORE].async_add("Mine", default_scenes("NL62")[245])
    await coordinator.async_add_to_strip(item)
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] is None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] == "Mine"


async def test_storing_default_scenes_polls_once(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """The defaults go in a row: one poll after the last shows what plays."""
    entity_id = light_id(hass, init_integration)
    coordinator = init_integration.runtime_data
    for scene_id in (244, 245, 246):
        del bluetooth.state.scenes[scene_id]
    await coordinator.async_refresh()
    polls = 0
    poll = coordinator._async_update_data

    async def counted():
        nonlocal polls
        polls += 1
        return await poll()

    coordinator._async_update_data = counted  # type: ignore[method-assign]
    assert await coordinator.async_restore_default_scenes() == [244, 245, 246]
    assert polls == 0

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert polls == 1
    assert hass.states.get(entity_id).attributes[ATTR_EFFECT] == "Assassin's Creed"
