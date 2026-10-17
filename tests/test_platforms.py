"""Sensors, the switch, and the scene-management entities."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nanoleaf_ble.const import DATA_STORE, DOMAIN
from custom_components.nanoleaf_ble.lib import (
    CommunityScene,
    NanoleafCloudError,
    default_scenes,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_OPTION, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .common import (
    SERIAL,
    TOKEN,
    FakeV3Strip,
    StripState,
    advertisement_bytes,
    make_service_info,
)
from .conftest import Bluetooth, entry_data

#: Every entity of a legacy strip: (platform, key). The light's key is None.
ENTITIES = [
    ("light", None),
    ("switch", "power_loss_recovery"),
    ("button", "identify"),
    ("button", "restore_default_scenes"),
    ("button", "community_preview"),
    ("button", "community_save"),
    ("button", "community_add_to_strip"),
    ("button", "scene_add_to_strip"),
    ("button", "scene_remove_from_strip"),
    ("button", "scene_delete_from_library"),
    ("select", "community_scene"),
    ("select", "scene"),
    ("text", "community_search"),
    ("sensor", "free_scene_slots"),
    ("sensor", "scenes_on_strip"),
    ("sensor", "firmware"),
    ("sensor", "hardware_version"),
    ("sensor", "serial_number"),
    ("sensor", "model"),
    ("sensor", "protocol"),
    ("sensor", "rssi"),
    ("binary_sensor", "connection"),
]

COMMUNITY = [
    CommunityScene("key-a", "Pumpkin Hallow", "Nanoleaf", "Radiate", True, 12),
    CommunityScene("key-b", "Pumpkin Hallow", "Nanoleaf", "Fade", False, 3),
    CommunityScene("key-c", "Aurora", "", "Flow", False, 1),
]
ANIMATION = {
    "animName": "Pumpkin Hallow",
    "pluginUuid": "b3fd723a-aae8-4c99-bf2b-087159e0ef53",
    "palette": [
        {"hue": 30, "saturation": 100, "brightness": 100},
        {"hue": 280, "saturation": 80, "brightness": 60},
    ],
}


def entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str | None
) -> str:
    unique_id = entry.entry_id if key is None else f"{entry.entry_id}_{key}"
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert found is not None, unique_id
    return found


async def test_unique_ids_are_pinned(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Changing one orphans users' entities and history."""
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), init_integration.entry_id
    )
    expected = {
        (
            platform,
            init_integration.entry_id
            if key is None
            else f"{init_integration.entry_id}_{key}",
        )
        for platform, key in ENTITIES
    }
    assert {(e.domain, e.unique_id) for e in entries} == expected


async def test_device(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), init_integration.entry_id
    )
    assert len(devices) == 1
    device = devices[0]
    assert device.name == "Secretlab MAGRGB BE3A"
    assert device.manufacturer == "Nanoleaf"
    assert device.model == "Secretlab MAGRGB"
    assert device.model_id == "NL62"
    assert device.sw_version == "1.6.49"
    assert device.hw_version == "2.0.0"
    assert device.serial_number == SERIAL


async def test_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    def value(key: str) -> str:
        return hass.states.get(entity_id(hass, init_integration, "sensor", key)).state

    assert value("free_scene_slots") == "2"
    assert value("scenes_on_strip") == "10"
    assert value("firmware") == "1.6.49"
    assert value("hardware_version") == "2.0.0"
    assert value("serial_number") == SERIAL
    assert value("model") == "NL62"
    assert value("protocol") == "legacy"
    # RSSI is disabled by default.
    rssi = er.async_get(hass).async_get(
        entity_id(hass, init_integration, "sensor", "rssi")
    )
    assert rssi.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_power_loss_switch(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    switch = entity_id(hass, init_integration, "switch", "power_loss_recovery")
    assert hass.states.get(switch).state == STATE_ON
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: switch}, blocking=True
    )
    assert bluetooth.state.power_loss_recovery is False
    assert hass.states.get(switch).state == STATE_OFF
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: switch}, blocking=True
    )
    assert bluetooth.state.power_loss_recovery is True


async def _press(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: entity_id(hass, entry, "button", key)},
        blocking=True,
    )


async def _select(
    hass: HomeAssistant, entry: MockConfigEntry, key: str, option: str
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: entity_id(hass, entry, "select", key), ATTR_OPTION: option},
        blocking=True,
    )


async def test_identify(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _press(hass, init_integration, "identify")
    assert bluetooth.state.identified == 1


async def test_restore_default_scenes(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _select(hass, init_integration, "scene", "Dark Knight [strip]")
    await _press(hass, init_integration, "scene_remove_from_strip")
    assert 247 not in bluetooth.state.scenes
    await _press(hass, init_integration, "restore_default_scenes")
    assert 247 in bluetooth.state.scenes


async def test_restore_when_full(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    coordinator = init_integration.runtime_data
    del bluetooth.state.scenes[244]
    del bluetooth.state.scenes[245]
    for scene_id in (0, 1, 2):
        bluetooth.state.scenes[scene_id] = bluetooth.state.scenes[250]
    await coordinator.async_refresh()
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, init_integration, "restore_default_scenes")
    assert err.value.translation_key == "strip_full"
    # What fits is written; nothing is deleted to make room.
    assert 244 in bluetooth.state.scenes
    assert 245 not in bluetooth.state.scenes


async def test_factory_scenes_are_never_deleted(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _select(hass, init_integration, "scene", "Northern Lights [strip]")
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, init_integration, "scene_remove_from_strip")
    assert err.value.translation_key == "builtin_protected"
    assert 250 in bluetooth.state.scenes


async def test_buttons_need_a_selection(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    for key, error in (
        ("community_preview", "no_community_scene_selected"),
        ("scene_add_to_strip", "no_scene_selected"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await _press(hass, init_integration, key)
        assert err.value.translation_key == error


async def test_community_browsing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    bluetooth: Bluetooth,
    mock_community,
) -> None:
    mock_community.search.return_value = COMMUNITY
    mock_community.download.return_value = ANIMATION
    search = entity_id(hass, init_integration, "text", "community_search")
    await hass.services.async_call(
        "text", "set_value", {ATTR_ENTITY_ID: search, "value": "pumpkin"}, blocking=True
    )
    assert hass.states.get(search).state == "pumpkin"
    select = entity_id(hass, init_integration, "select", "community_scene")
    state = hass.states.get(select)
    assert state.attributes["options"] == [
        "Pumpkin Hallow — Nanoleaf",
        "Pumpkin Hallow — Nanoleaf (2)",
        "Aurora",
    ]
    assert state.state == "Pumpkin Hallow — Nanoleaf"

    await _press(hass, init_integration, "community_preview")
    assert bluetooth.state.current == 0xFF
    light = entity_id(hass, init_integration, "light", None)
    assert hass.states.get(light).attributes["effect"] == "Pumpkin Hallow"

    await _press(hass, init_integration, "community_save")
    store = hass.data[DATA_STORE]
    assert [item.name for item in store.library] == ["Pumpkin Hallow"]
    assert store.library[0].community_key == "key-a"
    assert store.library[0].creator == "Nanoleaf"
    # Saving again is a no-op; the download is cached.
    await _press(hass, init_integration, "community_save")
    assert len(store.library) == 1
    assert mock_community.download.call_count == 1

    # The other "Pumpkin Hallow" clashes by name.
    await _select(
        hass, init_integration, "community_scene", "Pumpkin Hallow — Nanoleaf (2)"
    )
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, init_integration, "community_save")
    assert err.value.translation_key == "scene_exists"

    await _select(
        hass, init_integration, "community_scene", "Pumpkin Hallow — Nanoleaf"
    )
    await _press(hass, init_integration, "community_add_to_strip")
    assert 1 in bluetooth.state.scenes
    assert init_integration.runtime_data.scene_name(1) == "Pumpkin Hallow"
    assert (
        hass.states.get(
            entity_id(hass, init_integration, "sensor", "free_scene_slots")
        ).state
        == "1"
    )


async def test_community_unreachable(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_community
) -> None:
    mock_community.search.side_effect = NanoleafCloudError("timeout")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "text",
            "set_value",
            {
                ATTR_ENTITY_ID: entity_id(
                    hass, init_integration, "text", "community_search"
                ),
                "value": "x",
            },
            blocking=True,
        )
    assert err.value.translation_key == "community_failed"
    coordinator = init_integration.runtime_data
    mock_community.download.side_effect = NanoleafCloudError("timeout")
    with pytest.raises(HomeAssistantError):
        await coordinator.async_community_scene("k")


async def test_scene_management(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    store = hass.data[DATA_STORE]
    item = (
        store.async_add("Mine", init_integration.runtime_data.store.library[0].scene)
        if store.library
        else None
    )
    del item
    from custom_components.nanoleaf_ble.lib import default_scenes

    store.async_add("Mine", default_scenes("NL62")[245])
    await hass.async_block_till_done()
    select = entity_id(hass, init_integration, "select", "scene")
    assert "Mine [library]" in hass.states.get(select).attributes["options"]

    await _select(hass, init_integration, "scene", "Mine [library]")
    assert hass.states.get(select).state == "Mine [library]"
    await _press(hass, init_integration, "scene_add_to_strip")
    assert 1 in bluetooth.state.scenes
    # The pick follows the scene onto the strip.
    assert hass.states.get(select).state == "Mine [strip + library]"
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, init_integration, "scene_add_to_strip")
    assert err.value.translation_key == "scene_already_on_strip"

    # Fill the strip, then adding another is refused before anything is sent.
    store.async_add("Other", default_scenes("NL62")[246])
    await _select(hass, init_integration, "scene", "Other [library]")
    await _press(hass, init_integration, "scene_add_to_strip")
    store.async_add("Third", default_scenes("NL62")[247])
    await _select(hass, init_integration, "scene", "Third [library]")
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, init_integration, "scene_add_to_strip")
    assert err.value.translation_key == "strip_full"

    await _select(hass, init_integration, "scene", "Mine [strip + library]")
    await _press(hass, init_integration, "scene_remove_from_strip")
    assert hass.states.get(select).state == "Mine [library]"
    assert 1 not in bluetooth.state.scenes
    await _press(hass, init_integration, "scene_delete_from_library")
    assert store.find("Mine") is None
    assert hass.states.get(select).state == "unknown"

    # A built-in scene is not in the library.
    await _select(hass, init_integration, "scene", "Cloud9 [strip]")
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, init_integration, "scene_delete_from_library")
    assert err.value.translation_key == "scene_not_in_library"


async def test_strip_full_reply(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """The strip itself refuses, though the integration counted a free slot."""
    bluetooth.state.capacity = 10
    hass.data[DATA_STORE].async_add("Mine", default_scenes("NL62")[245])
    await _select(hass, init_integration, "scene", "Mine [library]")
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, init_integration, "scene_add_to_strip")
    assert err.value.translation_key == "strip_full"


async def test_firmware_5_has_no_scene_entities(
    hass: HomeAssistant, bluetooth: Bluetooth
) -> None:
    bluetooth.queued = [FakeV3Strip(StripState(), tokens={TOKEN: 0x42})]
    bluetooth.service_info = make_service_info(advertisement_bytes("NL55", (5, 0, 1)))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="v3",
        unique_id=bluetooth.service_info.address,
        data=entry_data(model="NL55", firmware="5.0.1", client_id=0x42),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    platforms = {
        (e.domain, e.unique_id.removeprefix(entry.entry_id))
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    assert platforms == {
        ("light", ""),
        ("button", "_identify"),
        ("sensor", "_firmware"),
        ("sensor", "_hardware_version"),
        ("sensor", "_serial_number"),
        ("sensor", "_model"),
        ("sensor", "_protocol"),
        ("sensor", "_rssi"),
        ("binary_sensor", "_connection"),
    }
    light = er.async_get(hass).async_get_entity_id("light", DOMAIN, entry.entry_id)
    assert "effect_list" not in hass.states.get(light).attributes
    with pytest.raises(ServiceValidationError) as err:
        await entry.runtime_data.async_play_effect("x")
    assert err.value.translation_key == "scenes_unsupported"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_management_entities_are_configuration(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """They live in the Configuration card, apart from the light."""
    registry = er.async_get(hass)
    config = {
        (e.domain, e.unique_id.removeprefix(f"{init_integration.entry_id}_"))
        for e in er.async_entries_for_config_entry(registry, init_integration.entry_id)
        if e.entity_category is er.EntityCategory.CONFIG
    }
    assert config == {
        ("switch", "power_loss_recovery"),
        ("button", "restore_default_scenes"),
        ("button", "community_preview"),
        ("button", "community_save"),
        ("button", "community_add_to_strip"),
        ("button", "scene_add_to_strip"),
        ("button", "scene_remove_from_strip"),
        ("button", "scene_delete_from_library"),
        ("select", "community_scene"),
        ("select", "scene"),
        ("text", "community_search"),
    }
    light = registry.async_get(entity_id(hass, init_integration, "light", None))
    assert light.entity_category is None


async def test_scene_to_manage_follows_the_playing_scene(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    select = entity_id(hass, init_integration, "select", "scene")
    assert hass.states.get(select).state == "unknown"  # nothing playing
    bluetooth.state.on, bluetooth.state.current = True, 252
    await init_integration.runtime_data.async_refresh()
    assert hass.states.get(select).state == "Cloud9 [strip]"
    assert hass.states.get(select).name.endswith("Scene to manage")

    # Without a pick, the buttons act on the playing scene.
    bluetooth.state.current = 247
    await init_integration.runtime_data.async_refresh()
    await _press(hass, init_integration, "scene_remove_from_strip")
    assert 247 not in bluetooth.state.scenes

    # A pick wins over the playing scene, and picking plays nothing.
    await _select(hass, init_integration, "scene", "Cloud9 [strip]")
    bluetooth.state.current = 250
    await init_integration.runtime_data.async_refresh()
    assert hass.states.get(select).state == "Cloud9 [strip]"
    assert bluetooth.state.current == 250


async def test_scene_to_manage_marks_where_scenes_live(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """Each option says strip, library or both, so the buttons make sense."""
    store = hass.data[DATA_STORE]
    store.async_add("Mine", default_scenes("NL62")[245])
    await hass.async_block_till_done()
    select = entity_id(hass, init_integration, "select", "scene")
    options = hass.states.get(select).attributes["options"]
    assert "Cloud9 [strip]" in options
    assert "Mine [library]" in options

    await _select(hass, init_integration, "scene", "Mine [library]")
    await _press(hass, init_integration, "scene_add_to_strip")
    assert "Mine [strip + library]" in hass.states.get(select).attributes["options"]

    # Deleting the library entry leaves the strip's copy, now strip only.
    await _press(hass, init_integration, "scene_delete_from_library")
    assert 1 in bluetooth.state.scenes
    assert hass.states.get(select).state == "Mine [strip]"
