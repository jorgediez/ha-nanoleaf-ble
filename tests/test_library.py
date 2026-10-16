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
    store.async_set_defaults_applied("AA")
    remove()
    assert len(calls) == 3

    store.async_rename(item.id, "Woods")
    # Write now rather than after the save delay.
    await store._store.async_save(store._data_to_save())

    reloaded = SceneStore(hass)
    await reloaded.async_load()
    assert reloaded.find("woods").creator == "Ana"
    assert reloaded.get(item.id).scene == default_scenes("NL62")[246]
    assert reloaded.strip_scenes("AA")[3].name == "Woods"
    assert reloaded.defaults_applied("AA")
    assert reloaded.find_by_fingerprint(item.scene.fingerprint).id == item.id

    reloaded.async_delete(item.id)
    assert reloaded.strip_scenes("AA")[3].library_id is None
    reloaded.async_forget_strip_scene("AA", 3)
    reloaded.async_forget_strip_scene("AA", 3)  # already gone
    reloaded.async_remove_device("AA")
    reloaded.async_remove_device("AA")
    assert not reloaded.defaults_applied("AA")


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
