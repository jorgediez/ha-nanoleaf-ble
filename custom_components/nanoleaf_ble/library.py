"""The scene library and the names of the scenes stored on each strip.

Scene names never reach a strip: it stores numeric IDs only, and the app keeps
names on the phone. So the integration keeps its own:

* the **library**, shared by every strip: scenes made with the create_scene
  action or saved from the community. Library scenes can be previewed on any
  strip without taking one of its 12 slots, and added to strips.
* per strip (by Bluetooth address), the name of each scene the integration
  stored there, with a fingerprint of its content. If the app later stores a
  different scene under the same ID, the fingerprint no longer matches and the
  stale name is dropped.

One Store holds both. It is domain-wide state, so it lives in hass.data under a
typed key rather than in any one entry's runtime_data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .lib import Scene

_LOGGER = logging.getLogger(__name__)

SOURCE_MANUAL = "manual"
SOURCE_COMMUNITY = "community"

#: Seconds to wait before writing, so a burst of changes is written once.
_SAVE_DELAY = 1


@dataclass(frozen=True, slots=True)
class LibraryScene:
    """A scene kept in the library."""

    id: str
    name: str
    scene: Scene
    source: str = SOURCE_MANUAL
    community_key: str | None = None
    creator: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the stored form."""
        return {
            "name": self.name,
            "scene": self.scene.as_dict(),
            "source": self.source,
            "community_key": self.community_key,
            "creator": self.creator,
        }


@dataclass(frozen=True, slots=True)
class StripSceneRecord:
    """What the integration knows about a scene it stored on a strip."""

    name: str
    fingerprint: str
    library_id: str | None = None


def normalise_name(name: str) -> str:
    """Return the form names are compared in: case-insensitive, trimmed."""
    return " ".join(name.split()).casefold()


class SceneStore:
    """The library and the per-strip scene records, persisted together."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the store; call async_load() before use."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._library: dict[str, LibraryScene] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        """Read the stored data."""
        data = await self._store.async_load() or {}
        for scene_id, item in (data.get("library") or {}).items():
            try:
                self._library[scene_id] = LibraryScene(
                    id=scene_id,
                    name=item["name"],
                    scene=Scene.from_dict(item["scene"]),
                    source=item.get("source", SOURCE_MANUAL),
                    community_key=item.get("community_key"),
                    creator=item.get("creator"),
                )
            except (KeyError, ValueError, TypeError) as exc:
                _LOGGER.warning(
                    "Dropping unreadable library scene %s: %s", scene_id, exc
                )
        self._devices = data.get("devices") or {}

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "library": {sid: scene.as_dict() for sid, scene in self._library.items()},
            "devices": self._devices,
        }

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(self._data_to_save, _SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the library changes; return the remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # ------------------------------------------------------------------ library

    @property
    def library(self) -> list[LibraryScene]:
        """Return the library scenes, sorted by name."""
        return sorted(self._library.values(), key=lambda s: normalise_name(s.name))

    def get(self, library_id: str) -> LibraryScene | None:
        """Return a library scene by its ID."""
        return self._library.get(library_id)

    def find(self, name: str) -> LibraryScene | None:
        """Return the library scene with this name, ignoring case."""
        wanted = normalise_name(name)
        return next(
            (s for s in self._library.values() if normalise_name(s.name) == wanted),
            None,
        )

    def find_by_fingerprint(self, fingerprint: str) -> LibraryScene | None:
        """Return a library scene with this content, if there is one."""
        return next(
            (s for s in self._library.values() if s.scene.fingerprint == fingerprint),
            None,
        )

    @callback
    def async_add(
        self,
        name: str,
        scene: Scene,
        *,
        source: str = SOURCE_MANUAL,
        community_key: str | None = None,
        creator: str | None = None,
    ) -> LibraryScene:
        """Add a scene. The caller checks the name is free."""
        item = LibraryScene(
            id=uuid.uuid4().hex,
            name=" ".join(name.split()),
            scene=scene,
            source=source,
            community_key=community_key,
            creator=creator,
        )
        self._library[item.id] = item
        self._changed()
        return item

    @callback
    def async_rename(self, library_id: str, name: str) -> None:
        """Rename a library scene, and its copies on strips."""
        old = self._library[library_id]
        name = " ".join(name.split())
        self._library[library_id] = LibraryScene(
            id=old.id,
            name=name,
            scene=old.scene,
            source=old.source,
            community_key=old.community_key,
            creator=old.creator,
        )
        for device in self._devices.values():
            for record in device.get("scenes", {}).values():
                if record.get("library_id") == library_id:
                    record["name"] = name
        self._changed()

    @callback
    def async_delete(self, library_id: str) -> None:
        """Delete a library scene. Copies on strips keep their name."""
        del self._library[library_id]
        for device in self._devices.values():
            for record in device.get("scenes", {}).values():
                if record.get("library_id") == library_id:
                    record["library_id"] = None
        self._changed()

    # ------------------------------------------------------------------ strips

    def _device(self, address: str) -> dict[str, Any]:
        return self._devices.setdefault(address, {"scenes": {}})

    def strip_scenes(self, address: str) -> dict[int, StripSceneRecord]:
        """Return the records of the scenes the integration stored on a strip."""
        return {
            int(scene_id): StripSceneRecord(
                name=record["name"],
                fingerprint=record["fingerprint"],
                library_id=record.get("library_id"),
            )
            for scene_id, record in self._devices.get(address, {})
            .get("scenes", {})
            .items()
        }

    @callback
    def async_set_strip_scene(
        self, address: str, scene_id: int, record: StripSceneRecord
    ) -> None:
        """Record a scene stored on a strip."""
        self._device(address)["scenes"][str(scene_id)] = {
            "name": record.name,
            "fingerprint": record.fingerprint,
            "library_id": record.library_id,
        }
        self._changed()

    @callback
    def async_forget_strip_scene(self, address: str, scene_id: int) -> None:
        """Forget a scene that left a strip."""
        if self._devices.get(address, {}).get("scenes", {}).pop(str(scene_id), None):
            self._changed()

    def defaults_applied(self, address: str) -> bool:
        """Return whether the app's default scenes were written to a strip."""
        return bool(self._devices.get(address, {}).get("defaults_applied"))

    @callback
    def async_set_defaults_applied(self, address: str) -> None:
        """Remember that the default scenes were written, as the app does per phone."""
        self._device(address)["defaults_applied"] = True
        self._changed()

    @callback
    def async_keep_devices(self, addresses: set[str]) -> None:
        """Forget the strips at any other address."""
        for address in set(self._devices) - addresses:
            del self._devices[address]
            self._changed()

    @callback
    def async_remove_device(self, address: str) -> None:
        """Forget everything about a strip whose entry was removed."""
        if self._devices.pop(address, None) is not None:
            self._changed()
