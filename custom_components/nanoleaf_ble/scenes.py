"""The names of one strip's scenes, and what each effect name plays.

The strip stores scenes by number only. Names come from the app's tables for
built-in scenes, from the per-strip records for scenes the integration stored,
and from the library for scenes that play as previews. This module keeps those
names right as scenes come and go, and turns names back into scenes. Writing
to the strip stays with the coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .lib import NO_SCENE_ID, PREVIEW_SCENE_ID, NanoleafDevice, builtin_scene_names
from .library import LibraryScene, SceneStore, StripSceneRecord, normalise_name

if TYPE_CHECKING:
    from .coordinator import NanoleafData

#: Effect targets: a scene stored on the strip, or a library scene to preview.
TARGET_STRIP = "strip"
TARGET_LIBRARY = "library"

#: Where a scene lives, as "Scene to manage" marks it.
LOCATION_STRIP = "strip"
LOCATION_LIBRARY = "library"
LOCATION_BOTH = "strip + library"


@dataclass(frozen=True, slots=True)
class EffectTarget:
    """What an effect name plays."""

    kind: str
    #: The strip scene ID, or the library scene ID.
    ref: int | str


class Strip(Protocol):
    """What the scene names need to know about the strip (the coordinator)."""

    @property
    def address(self) -> str:
        """Return the address the strip's scene records are kept under."""

    @property
    def model(self) -> str:
        """Return the model code, which picks the built-in scene names."""

    @property
    def supports_scenes(self) -> bool:
        """Return whether scene features work on this firmware."""

    @property
    def data(self) -> NanoleafData | None:
        """Return the last polled state, with the stored and playing scenes."""


class StripScenes:
    """Name the scenes of one strip, and find the scene a name refers to."""

    def __init__(self, store: SceneStore, strip: Strip) -> None:
        """Initialise for one strip."""
        self._store = store
        self._strip = strip
        #: Scene IDs read on this connection (see async_check).
        self._checked: set[int] = set()
        #: The name of the preview playing, when it was started here.
        self.preview_name: str | None = None
        #: The scene picked in "Scene to manage" (see managed).
        self.selected: str | None = None

    def _records(self) -> dict[int, StripSceneRecord]:
        return self._store.strip_scenes(self._strip.address)

    # ------------------------------------------------------------------ bookkeeping

    def reset(self) -> None:
        """Read every scene again: a new connection may find them changed."""
        self._checked.clear()

    async def async_check(
        self, device: NanoleafDevice, scene_ids: tuple[int, ...]
    ) -> None:
        """Keep the strip's scene names right as scenes come and go.

        A scene seen for the first time on this connection is read once and
        matched by content: against what the integration stored under that ID,
        then against the library. Built-in IDs need no read; the app's tables
        name them.
        """
        address = self._strip.address
        builtins = builtin_scene_names(self._strip.model)
        records = self._records()
        for scene_id in list(records):
            if scene_id not in scene_ids:
                self._store.async_forget_strip_scene(address, scene_id)
        self._checked &= set(scene_ids)
        for scene_id in scene_ids:
            if scene_id in self._checked or scene_id in builtins:
                continue
            fingerprint = (await device.get_scene(scene_id)).fingerprint
            self._checked.add(scene_id)
            record = records.get(scene_id)
            if record is not None and record.fingerprint == fingerprint:
                continue
            if (match := self._store.find_by_fingerprint(fingerprint)) is not None:
                self._store.async_set_strip_scene(
                    address,
                    scene_id,
                    StripSceneRecord(match.name, fingerprint, match.id),
                )
            elif record is not None:
                # Something else (the phone app) stored another scene here.
                self._store.async_forget_strip_scene(address, scene_id)

    def stored(self, scene_id: int, item: LibraryScene) -> None:
        """Record a library scene the integration stored on the strip."""
        self._store.async_set_strip_scene(
            self._strip.address,
            scene_id,
            StripSceneRecord(item.name, item.scene.fingerprint, item.id),
        )
        self._checked.add(scene_id)

    def removed(self, scene_id: int) -> None:
        """Forget a scene the integration deleted from the strip."""
        self._store.async_forget_strip_scene(self._strip.address, scene_id)
        self._checked.discard(scene_id)

    # ------------------------------------------------------------------ names

    def name(self, scene_id: int) -> str:
        """Return the name of a scene stored on the strip."""
        if (name := builtin_scene_names(self._strip.model).get(scene_id)) is not None:
            return name
        if (record := self._records().get(scene_id)) is not None:
            return record.name
        return f"Scene {scene_id}"

    @property
    def effects(self) -> dict[str, EffectTarget]:
        """Return every playable scene by unique name.

        Scenes on the strip come first, then library scenes that are not on it;
        those play as previews, taking no slot.
        """
        data = self._strip.data
        if not self._strip.supports_scenes or data is None:
            return {}
        effects: dict[str, EffectTarget] = {}
        taken: set[str] = set()

        def add(name: str, target: EffectTarget) -> None:
            unique, number = name, 2
            while normalise_name(unique) in taken:
                unique, number = f"{name} ({number})", number + 1
            taken.add(normalise_name(unique))
            effects[unique] = target

        records = self._records()
        on_strip: set[str] = set()
        for scene_id in sorted(
            data.scene_ids, key=lambda sid: normalise_name(self.name(sid))
        ):
            add(self.name(scene_id), EffectTarget(TARGET_STRIP, scene_id))
            if (record := records.get(scene_id)) and record.library_id:
                on_strip.add(record.library_id)
        for item in self._store.library:
            if item.id not in on_strip:
                add(item.name, EffectTarget(TARGET_LIBRARY, item.id))
        return effects

    @property
    def locations(self) -> dict[str, str]:
        """Return every effect name with where the scene lives.

        A strip scene counts as in the library too while its library entry
        still exists; deleting that entry leaves the strip's copy alone.
        """
        records = self._records()
        locations: dict[str, str] = {}
        for name, target in self.effects.items():
            if target.kind == TARGET_LIBRARY:
                locations[name] = LOCATION_LIBRARY
            elif (
                (record := records.get(int(target.ref)))
                and record.library_id
                and self._store.get(record.library_id) is not None
            ):
                locations[name] = LOCATION_BOTH
            else:
                locations[name] = LOCATION_STRIP
        return locations

    @property
    def current_effect(self) -> str | None:
        """Return the name of what plays, or None for a static colour or off."""
        data = self._strip.data
        if data is None or not self._strip.supports_scenes:
            return None
        current = data.current_scene
        if current == NO_SCENE_ID:
            return None
        if current == PREVIEW_SCENE_ID:
            # Only a preview started here has a known name.
            return self.preview_name
        for name, target in self.effects.items():
            if target.kind == TARGET_STRIP and target.ref == current:
                return name
        return self.name(current)

    @property
    def managed(self) -> str | None:
        """Return the scene the management buttons act on.

        Only the one picked in "Scene to manage", while it still exists: never
        the scene playing, so no button acts on a scene nobody picked.
        """
        return self.selected if self.selected in self.effects else None

    # ------------------------------------------------------------------ lookups

    def find(self, name: str) -> EffectTarget | None:
        """Return what an effect name plays, ignoring case."""
        effects = self.effects
        if (target := effects.get(name)) is not None:
            return target
        wanted = normalise_name(name)
        return next(
            (t for n, t in effects.items() if normalise_name(n) == wanted), None
        )

    def strip_scene_id(self, name: str) -> int:
        """Return the strip scene an effect name refers to."""
        target = self.find(name)
        if target is None or target.kind != TARGET_STRIP:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_on_strip",
                translation_placeholders={"scene": name},
            )
        return int(target.ref)

    def library_scene(self, name: str) -> LibraryScene | None:
        """Return the library scene an effect or library name refers to."""
        target = self.find(name)
        if target is not None and target.kind == TARGET_LIBRARY:
            return self._store.get(str(target.ref))
        if target is not None and target.kind == TARGET_STRIP:
            record = self._records().get(int(target.ref))
            if record is not None and record.library_id:
                return self._store.get(record.library_id)
        return self._store.find(name)

    def require_library_scene(self, name: str) -> LibraryScene:
        """Return the library scene with this name, or raise."""
        if (item := self.library_scene(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_in_library",
                translation_placeholders={"scene": name},
            )
        return item

    def on_strip(self, library_id: str) -> int | None:
        """Return the ID a library scene is stored under on this strip, if any."""
        if (data := self._strip.data) is None:
            return None
        for scene_id, record in self._records().items():
            if record.library_id == library_id and scene_id in data.scene_ids:
                return scene_id
        return None
