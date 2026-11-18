"""The actions."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
import voluptuous as vol

from custom_components.nanoleaf_ble.const import DATA_STORE, DOMAIN
from custom_components.nanoleaf_ble.lib import (
    CommunityScene,
    EffectType,
    NanoleafCloudError,
    PaletteColour,
    default_scenes,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
    label_registry as lr,
)

from .conftest import Bluetooth
from .harness import MockConfigEntry

ANIMATION = {
    "pluginUuid": "027842e4-e1d6-4a4c-a731-be74a1ebd4cf",
    "palette": [{"hue": 120, "saturation": 100, "brightness": 100}],
}


def _light(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return er.async_get(hass).async_get_entity_id("light", DOMAIN, entry.entry_id)


async def _call(hass: HomeAssistant, service: str, **data):
    return await hass.services.async_call(
        DOMAIN,
        service,
        data,
        blocking=True,
        return_response=service == "search_community_scenes",
    )


async def test_create_scene_only_in_the_library(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _call(
        hass,
        "create_scene",
        name="Sunset",
        effect="flow",
        colours=[
            [255, 64, 0],
            {"hue": 300, "saturation": 80, "brightness": 50, "repeat": 2},
        ],
        speed=50,
        direction="right",
        loop=False,
    )
    item = hass.data[DATA_STORE].find("sunset")
    assert item is not None
    assert item.scene.effect is EffectType.FLOW
    assert item.scene.params == (50, 0, 1, 0)
    assert item.scene.palette == (
        PaletteColour(15, 100, 100),
        PaletteColour(300, 80, 50, 2),
    )
    assert bluetooth.state.previews == []

    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "create_scene", name="SUNSET", effect="fade", colours=[[1, 2, 3]]
        )
    assert err.value.translation_key == "scene_exists"


async def test_create_store_and_play(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _call(
        hass,
        "create_scene",
        entity_id=_light(hass, init_integration),
        name="Stored",
        effect="stripes",
        colours=[[255, 0, 0], [0, 0, 255]],
        segment=10,
        add_to_strip=True,
        play=True,
    )
    assert 1 in bluetooth.state.scenes
    assert bluetooth.state.current == 1
    assert bluetooth.state.on


async def test_create_and_preview(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _call(
        hass,
        "create_scene",
        entity_id=_light(hass, init_integration),
        name="Preview me",
        effect="highlight",
        colours=[[255, 0, 0]],
        main_colour_probability=90,
        play=True,
    )
    assert bluetooth.state.current == 0xFF
    assert len(bluetooth.state.previews) == 1


async def test_create_needs_a_target_to_store_or_play(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "create_scene",
            name="x",
            effect="fade",
            colours=[[1, 2, 3]],
            play=True,
        )
    assert err.value.translation_key == "target_required"


async def test_invalid_scenes(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "create_scene",
            name="x",
            effect="fade",
            colours=[[1, 2, 3]],
            segment=3,
        )
    assert err.value.translation_key == "invalid_scene"
    for colours in ([[1, 2]], [[300, 0, 0]], [{"hue": 1}], [[1, 2, 3]] * 8):
        with pytest.raises(vol.Invalid):
            await _call(hass, "create_scene", name="x", effect="fade", colours=colours)


async def test_preview_scene(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    await _call(
        hass,
        "preview_scene",
        entity_id=_light(hass, init_integration),
        effect="random",
        colours=[[0, 255, 0]],
    )
    assert bluetooth.state.current == 0xFF
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "preview_scene", entity_id=[], effect="random", colours=[[0, 255, 0]]
        )
    assert err.value.translation_key == "target_required"


async def test_add_remove_rename_delete(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    store = hass.data[DATA_STORE]
    store.async_add("Mine", default_scenes("NL62")[245])
    light = _light(hass, init_integration)

    await _call(hass, "add_scene_to_strip", entity_id=light, scene="mine")
    assert 1 in bluetooth.state.scenes
    await _call(hass, "rename_scene", scene="Mine", new_name="Ours")
    assert init_integration.runtime_data.scene_name(1) == "Ours"
    await _call(hass, "rename_scene", scene="Ours", new_name="OURS")  # same scene

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "rename_scene", scene="Nope", new_name="x")
    assert err.value.translation_key == "scene_not_in_library"
    store.async_add("Taken", default_scenes("NL62")[246])
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "rename_scene", scene="OURS", new_name="taken")
    assert err.value.translation_key == "scene_exists"

    await _call(hass, "remove_scene_from_strip", entity_id=light, scene="OURS")
    assert 1 not in bluetooth.state.scenes
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "remove_scene_from_strip", entity_id=light, scene="OURS")
    assert err.value.translation_key == "scene_not_on_strip"

    await _call(hass, "delete_library_scene", scene="ours")
    assert store.find("OURS") is None
    with pytest.raises(ServiceValidationError):
        await _call(hass, "delete_library_scene", scene="ours")


async def test_deleting_a_library_scene_keeps_the_strip_copy(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    store = hass.data[DATA_STORE]
    store.async_add("Mine", default_scenes("NL62")[245])
    await _call(
        hass,
        "add_scene_to_strip",
        entity_id=_light(hass, init_integration),
        scene="Mine",
    )
    await _call(hass, "delete_library_scene", scene="Mine")
    assert 1 in bluetooth.state.scenes
    assert init_integration.runtime_data.scene_name(1) == "Mine"


async def test_target_must_be_a_strip(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "restore_default_scenes", entity_id="light.kitchen")
    assert err.value.translation_key == "not_a_strip"


async def test_restore_default_scenes(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    del bluetooth.state.scenes[248]
    await init_integration.runtime_data.async_refresh()
    await _call(
        hass, "restore_default_scenes", entity_id=_light(hass, init_integration)
    )
    assert 248 in bluetooth.state.scenes


async def test_community_actions(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    bluetooth: Bluetooth,
    mock_community,
) -> None:
    mock_community.search.return_value = [
        CommunityScene("key-a", "Forest", "Ana", "Flow", True, 9)
    ]
    mock_community.download.return_value = ANIMATION
    response = await _call(hass, "search_community_scenes", query="forest", sort="top")
    assert response == {
        "scenes": [
            {
                "key": "key-a",
                "name": "Forest",
                "creator": "Ana",
                "motion": "Flow",
                "featured": True,
                "downloads": 9,
            }
        ]
    }
    mock_community.search.assert_awaited_with("forest", featured=False, sort="top")

    light = _light(hass, init_integration)
    await _call(hass, "preview_community_scene", entity_id=light, key="key-a")
    assert bluetooth.state.current == 0xFF

    await _call(hass, "save_community_scene", key="key-a", name="My forest")
    item = hass.data[DATA_STORE].find("My forest")
    assert item.community_key == "key-a"
    await _call(
        hass, "save_community_scene", entity_id=light, key="key-a", add_to_strip=True
    )
    assert 1 in bluetooth.state.scenes
    assert init_integration.runtime_data.scene_name(1) == "My forest"

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "save_community_scene", key="key-b", add_to_strip=True)
    assert err.value.translation_key == "target_required"

    mock_community.search.side_effect = NanoleafCloudError("down")
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "search_community_scenes")
    assert err.value.translation_key == "community_failed"


async def test_community_user_agent(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The integration names itself and its project page, never another client."""
    with patch(
        "custom_components.nanoleaf_ble.coordinator.CommunityClient"
    ) as client_class:
        await init_integration.runtime_data.async_community_client()
    user_agent = client_class.call_args.args[1]
    assert re.fullmatch(
        r"ha-nanoleaf-ble/\d+\.\d+\.\d+ "
        r"\(\+https://github\.com/jorgediez/ha-nanoleaf-ble\)",
        user_agent,
    )


async def test_community_needs_a_loaded_strip(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.config_entries.async_unload(config_entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "search_community_scenes")
    assert err.value.translation_key == "no_strips_loaded"


async def test_unloaded_strip_is_not_a_target(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    light = _light(hass, config_entry)
    await hass.config_entries.async_unload(config_entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "restore_default_scenes", entity_id=light)
    assert err.value.translation_key == "not_a_strip"


def test_optional_strips_are_a_field_not_a_target():
    """A "target:" makes the frontend demand one; these work without a strip."""
    from pathlib import Path

    import yaml

    services = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "nanoleaf_ble"
            / "services.yaml"
        ).read_text(encoding="utf-8")
    )
    for name in ("create_scene", "save_community_scene"):
        assert "target" not in services[name], name
        assert "entity_id" in services[name]["fields"], name
    # The entity filter also limits the devices, areas, floors and labels the
    # picker offers; hassfest refuses a device filter on a target.
    for name in TARGETED_ACTIONS:
        assert services[name]["target"] == {
            "entity": {"integration": DOMAIN, "domain": "light"}
        }, name


TARGETED_ACTIONS = (
    "preview_scene",
    "add_scene_to_strip",
    "remove_scene_from_strip",
    "preview_community_scene",
    "restore_default_scenes",
)


def _targets(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Put the strip in an area on a floor and label its light.

    Return one id per kind of target, each of them reaching the strip.
    """
    light = _light(hass, entry)
    devices = dr.async_get(hass)
    (device,) = dr.async_entries_for_config_entry(devices, entry.entry_id)
    floor = fr.async_get(hass).async_create("Office")
    area = ar.async_get(hass).async_create("Desk", floor_id=floor.floor_id)
    devices.async_update_device(device.id, area_id=area.id)
    label = lr.async_get(hass).async_create("Strips")
    er.async_get(hass).async_update_entity(light, labels={label.label_id})
    return {
        "entity_id": light,
        "device_id": device.id,
        "area_id": area.id,
        "floor_id": floor.floor_id,
        "label_id": label.label_id,
    }


def _other_light(hass: HomeAssistant, area_id: str) -> str:
    """Add another integration's light to the area."""
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    entities = er.async_get(hass)
    entity = entities.async_get_or_create("light", "other", "lamp", config_entry=other)
    entities.async_update_entity(entity.entity_id, area_id=area_id)
    return entity.entity_id


@pytest.mark.parametrize(
    "kind", ["entity_id", "device_id", "area_id", "floor_id", "label_id"]
)
@pytest.mark.parametrize(
    "action", [*TARGETED_ACTIONS, "create_scene", "save_community_scene"]
)
async def test_every_kind_of_target(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    bluetooth: Bluetooth,
    mock_community,
    action: str,
    kind: str,
) -> None:
    """Every action that takes strips finds them through any kind of target."""
    targets = _targets(hass, init_integration)
    _other_light(hass, targets["area_id"])
    target = {kind: targets[kind]}
    store = hass.data[DATA_STORE]
    mock_community.download.return_value = ANIMATION
    fade = {"effect": "fade", "colours": [[255, 0, 0]]}

    if action == "preview_scene":
        await _call(hass, action, **target, **fade)
        assert bluetooth.state.current == 0xFF
    elif action == "add_scene_to_strip":
        store.async_add("Mine", default_scenes("NL62")[245])
        await _call(hass, action, **target, scene="Mine")
        assert 1 in bluetooth.state.scenes
    elif action == "remove_scene_from_strip":
        store.async_add("Mine", default_scenes("NL62")[245])
        await _call(
            hass, "add_scene_to_strip", entity_id=targets["entity_id"], scene="Mine"
        )
        await _call(hass, action, **target, scene="Mine")
        assert 1 not in bluetooth.state.scenes
    elif action == "preview_community_scene":
        await _call(hass, action, **target, key="key-a")
        assert bluetooth.state.current == 0xFF
    elif action == "restore_default_scenes":
        del bluetooth.state.scenes[248]
        await init_integration.runtime_data.async_refresh()
        await _call(hass, action, **target)
        assert 248 in bluetooth.state.scenes
    elif action == "create_scene":
        await _call(hass, action, **target, name="Mine", add_to_strip=True, **fade)
        assert 1 in bluetooth.state.scenes
    else:
        await _call(hass, action, **target, key="key-a", add_to_strip=True)
        assert 1 in bluetooth.state.scenes


async def test_a_strip_named_twice_is_done_once(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    targets = _targets(hass, init_integration)
    hass.data[DATA_STORE].async_add("Mine", default_scenes("NL62")[245])
    await _call(hass, "add_scene_to_strip", **targets, scene="Mine")
    assert 1 in bluetooth.state.scenes
    assert 2 not in bluetooth.state.scenes


async def test_a_target_without_a_strip(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An area holding only other things is an error, but not about them."""
    area = ar.async_get(hass).async_create("Hall")
    _other_light(hass, area.id)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "restore_default_scenes", area_id=area.id)
    assert err.value.translation_key == "no_loaded_strip_targeted"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "restore_default_scenes", device_id="none")
    assert err.value.translation_key == "target_required"


async def test_a_light_named_directly_must_be_a_strip(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Unlike one in an area, another light named directly is an error."""
    targets = _targets(hass, init_integration)
    lamp = _other_light(hass, targets["area_id"])
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "restore_default_scenes", entity_id=[targets["entity_id"], lamp]
        )
    assert err.value.translation_key == "not_a_strip"


async def test_unloaded_strip_through_its_device_or_area(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    targets = _targets(hass, config_entry)
    await hass.config_entries.async_unload(config_entry.entry_id)
    for kind in ("device_id", "area_id"):
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, "restore_default_scenes", **{kind: targets[kind]})
        assert err.value.translation_key == "no_loaded_strip_targeted", kind


@pytest.mark.skipif(
    "exclude" not in cv.TARGET_SERVICE_FIELDS,
    reason="targets take exclude from Home Assistant 2026.11",
)
async def test_exclude(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    targets = _targets(hass, init_integration)
    lamp = _other_light(hass, targets["area_id"])
    del bluetooth.state.scenes[248]
    await init_integration.runtime_data.async_refresh()

    # Excluding something else leaves the strip targeted.
    await _call(
        hass,
        "restore_default_scenes",
        area_id=targets["area_id"],
        exclude={"entity_id": [lamp]},
    )
    assert 248 in bluetooth.state.scenes

    # Excluding the strip, by its device or by its light's label, leaves nothing.
    for exclude in (
        {"device_id": [targets["device_id"]]},
        {"label_id": [targets["label_id"]]},
    ):
        with pytest.raises(ServiceValidationError) as err:
            await _call(
                hass,
                "preview_scene",
                floor_id=targets["floor_id"],
                exclude=exclude,
                effect="fade",
                colours=[[255, 0, 0]],
            )
        assert err.value.translation_key == "no_loaded_strip_targeted", exclude
