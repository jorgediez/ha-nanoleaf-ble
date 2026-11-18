"""Scene names and lookups, against a stand-in strip rather than a fake one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from custom_components.nanoleaf_ble.coordinator import NanoleafData
from custom_components.nanoleaf_ble.lib import (
    NO_SCENE_ID,
    PREVIEW_SCENE_ID,
    LightState,
    default_scenes,
)
from custom_components.nanoleaf_ble.library import SceneStore, StripSceneRecord
from custom_components.nanoleaf_ble.scenes import (
    LOCATION_BOTH,
    LOCATION_LIBRARY,
    LOCATION_STRIP,
    TARGET_LIBRARY,
    TARGET_STRIP,
    EffectTarget,
    StripScenes,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

ADDRESS = "AA:BB:CC:DD:EE:FF"
LIGHT = LightState(on=True, brightness=50, hue=0, saturation=0)
DEFAULTS = default_scenes("NL62")


@dataclass
class Strip:
    """What StripScenes reads from the coordinator."""

    data: NanoleafData | None = None
    address: str = ADDRESS
    model: str = "NL62"
    supports_scenes: bool = True


def _data(*scene_ids: int, current: int = NO_SCENE_ID) -> NanoleafData:
    return NanoleafData(light=LIGHT, current_scene=current, scene_ids=scene_ids)


@pytest.fixture
async def store(hass: HomeAssistant, hass_storage: dict[str, Any]) -> SceneStore:
    store = SceneStore(hass)
    await store.async_load()
    return store


async def test_nothing_without_scenes(store: SceneStore) -> None:
    store.async_add("Forest", DEFAULTS[244])
    strip = Strip()
    scenes = StripScenes(store, strip)
    assert scenes.effects == {}
    assert scenes.current_effect is None
    assert scenes.on_strip("anything") is None

    strip.data = _data(252, current=252)
    strip.supports_scenes = False
    assert scenes.effects == {}
    assert scenes.current_effect is None


async def test_names_and_locations(store: SceneStore) -> None:
    forest = store.async_add("Forest", DEFAULTS[244])
    ocean = store.async_add("Ocean", DEFAULTS[245])
    store.async_set_strip_scene(
        ADDRESS, 1, StripSceneRecord(forest.name, "fp", forest.id)
    )
    store.async_set_strip_scene(ADDRESS, 2, StripSceneRecord("Mine", "fp"))
    scenes = StripScenes(store, Strip(_data(252, 1, 2, 3)))

    assert scenes.name(252) == "Cloud9"
    assert scenes.name(2) == "Mine"
    assert scenes.name(3) == "Scene 3"
    # Strip scenes by name, then the library scenes not on the strip.
    assert scenes.effects == {
        "Cloud9": EffectTarget(TARGET_STRIP, 252),
        "Forest": EffectTarget(TARGET_STRIP, 1),
        "Mine": EffectTarget(TARGET_STRIP, 2),
        "Scene 3": EffectTarget(TARGET_STRIP, 3),
        "Ocean": EffectTarget(TARGET_LIBRARY, ocean.id),
    }
    assert scenes.locations == {
        "Cloud9": LOCATION_STRIP,
        "Forest": LOCATION_BOTH,
        "Mine": LOCATION_STRIP,
        "Scene 3": LOCATION_STRIP,
        "Ocean": LOCATION_LIBRARY,
    }
    assert scenes.on_strip(forest.id) == 1
    assert scenes.on_strip(ocean.id) is None

    # Deleting the library entry leaves the strip's copy, named as before.
    store.async_delete(forest.id)
    assert scenes.locations["Forest"] == LOCATION_STRIP


async def test_current_effect(store: SceneStore) -> None:
    strip = Strip(_data(252))
    scenes = StripScenes(store, strip)
    assert scenes.current_effect is None

    strip.data = _data(252, current=252)
    assert scenes.current_effect == "Cloud9"

    # A preview is named only when it was started here.
    strip.data = _data(252, current=PREVIEW_SCENE_ID)
    assert scenes.current_effect is None
    scenes.preview_name = "Forest"
    assert scenes.current_effect == "Forest"

    # A scene that plays but was not in the list read with it.
    strip.data = _data(252, current=250)
    assert scenes.current_effect == "Northern Lights"


async def test_lookups(store: SceneStore) -> None:
    forest = store.async_add("Forest", DEFAULTS[244])
    ocean = store.async_add("Ocean", DEFAULTS[245])
    store.async_set_strip_scene(
        ADDRESS, 1, StripSceneRecord(forest.name, "fp", forest.id)
    )
    scenes = StripScenes(store, Strip(_data(252, 1)))

    assert scenes.find("cloud9") == EffectTarget(TARGET_STRIP, 252)
    assert scenes.find("Nowhere") is None
    assert scenes.strip_scene_id("FOREST") == 1
    with pytest.raises(ServiceValidationError) as err:
        scenes.strip_scene_id("Ocean")
    assert err.value.translation_key == "scene_not_on_strip"

    assert scenes.library_scene("forest") == forest  # through the strip record
    assert scenes.library_scene("ocean") == ocean
    assert scenes.library_scene("Cloud9") is None
    with pytest.raises(ServiceValidationError) as err:
        scenes.require_library_scene("Cloud9")
    assert err.value.translation_key == "scene_not_in_library"


async def test_managed_scene(store: SceneStore) -> None:
    strip = Strip(_data(252, 253))
    scenes = StripScenes(store, strip)
    assert scenes.managed is None
    scenes.selected = "Team Liquid"
    assert scenes.managed == "Team Liquid"
    # Gone from the strip: no button acts on it.
    strip.data = _data(252)
    assert scenes.managed is None


async def test_stored_and_removed(store: SceneStore) -> None:
    forest = store.async_add("Forest", DEFAULTS[244])
    scenes = StripScenes(store, Strip(_data(1)))
    scenes.stored(1, forest)
    assert store.strip_scenes(ADDRESS)[1] == StripSceneRecord(
        "Forest", forest.scene.fingerprint, forest.id
    )
    assert scenes.name(1) == "Forest"
    scenes.removed(1)
    assert store.strip_scenes(ADDRESS) == {}
