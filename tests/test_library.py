"""The scene store: persistence, and the per-strip records."""

from __future__ import annotations

from typing import Any

from custom_components.nanoleaf_ble.lib import default_scenes
from custom_components.nanoleaf_ble.library import (
    SOURCE_COMMUNITY,
    SceneStore,
    StripSceneRecord,
)
from homeassistant.core import HomeAssistant


async def test_persists(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    store = SceneStore(hass)
    await store.async_load()
    calls = []
    remove = store.async_add_listener(lambda: calls.append(1))
    item = store.async_add(
        "  Forest   walk ",
        default_scenes("NL62")[246],
        source=SOURCE_COMMUNITY,
        community_key="k",
        creator="Ana",
    )
    assert item.name == "Forest walk"
    store.async_set_strip_scene("AA", 3, StripSceneRecord(item.name, "fp", item.id))
    remove()
    assert len(calls) == 2

    store.async_rename(item.id, "Woods")
    # Write now rather than after the save delay.
    await store._store.async_save(store._data_to_save())

    reloaded = SceneStore(hass)
    await reloaded.async_load()
    assert reloaded.find("woods").creator == "Ana"
    assert reloaded.get(item.id).scene == default_scenes("NL62")[246]
    assert reloaded.strip_scenes("AA")[3].name == "Woods"
    assert reloaded.find_by_fingerprint(item.scene.fingerprint).id == item.id

    reloaded.async_delete(item.id)
    assert reloaded.strip_scenes("AA")[3].library_id is None
    reloaded.async_forget_strip_scene("AA", 3)
    reloaded.async_forget_strip_scene("AA", 3)  # already gone
    reloaded.async_set_strip_scene("AA", 4, StripSceneRecord("Other", "fp"))
    reloaded.async_remove_device("AA")
    reloaded.async_remove_device("AA")
    assert not reloaded.strip_scenes("AA")


async def test_unreadable_entries_are_dropped(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage["nanoleaf_ble"] = {
        "version": 1,
        "data": {"library": {"bad": {"name": "x", "scene": {"effect": "nope"}}}},
    }
    store = SceneStore(hass)
    await store.async_load()
    assert store.library == []


async def test_unreadable_strip_records_are_dropped(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    good = {"name": "Mine", "fingerprint": "fp", "library_id": None}
    hass_storage["nanoleaf_ble"] = {
        "version": 1,
        "data": {
            "library": {"bad": "not a scene"},
            "devices": {
                "AA": {
                    "scenes": {
                        "1": good,
                        "two": good,
                        "3": {"name": "No fingerprint"},
                        "4": {"name": 4, "fingerprint": "fp"},
                        "5": {**good, "library_id": 5},
                        "6": "not a record",
                    }
                },
                "BB": {"scenes": ["not", "a", "dict"]},
                "CC": "not a device",
            },
        },
    }
    store = SceneStore(hass)
    await store.async_load()
    assert store.library == []
    assert store.strip_scenes("AA") == {1: StripSceneRecord("Mine", "fp")}
    assert store.strip_scenes("BB") == {}
    assert store.strip_scenes("CC") == {}

    hass_storage["nanoleaf_ble"]["data"]["devices"] = ["not", "a", "dict"]
    store = SceneStore(hass)
    await store.async_load()
    assert store.strip_scenes("AA") == {}
