"""Pick a community search result, or a scene to manage.

The buttons next to each select act on what it points at, so scenes can be
browsed, previewed, saved and stored from the device page without YAML. They
are configuration entities: they sit in the device page's Configuration card,
apart from the light, and stay off auto-generated dashboards.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity

# Selecting changes only what the integration points at; nothing is sent.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the selects where the strip supports scenes."""
    coordinator = entry.runtime_data
    if coordinator.supports_scenes:
        async_add_entities(
            [CommunitySceneSelect(coordinator), SceneSelect(coordinator)]
        )


def community_labels(coordinator: NanoleafCoordinator) -> dict[str, str]:
    """Return a unique label for each search result, mapped to its key."""
    labels: dict[str, str] = {}
    for result in coordinator.community_results:
        label = f"{result.name} — {result.creator}" if result.creator else result.name
        unique, number = label, 2
        while unique in labels:
            unique, number = f"{label} ({number})", number + 1
        labels[unique] = result.key
    return labels


class CommunitySceneSelect(NanoleafEntity, SelectEntity):
    """The results of the last community search."""

    _attr_translation_key = "community_scene"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NanoleafCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, "community_scene")

    @property
    def options(self) -> list[str]:
        """Return the search results."""
        return list(community_labels(self.coordinator))

    @property
    def current_option(self) -> str | None:
        """Return the selected result."""
        for label, key in community_labels(self.coordinator).items():
            if key == self.coordinator.community_selected:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Point at a result."""
        self.coordinator.community_selected = community_labels(self.coordinator)[option]
        self.coordinator.async_update_listeners()


def scene_labels(coordinator: NanoleafCoordinator) -> dict[str, str]:
    """Return each option of "Scene to manage" with the scene it stands for.

    The option marks where the scene lives, e.g. "Aurora [strip + library]",
    so it is clear which management buttons apply.
    """
    return {
        f"{name} [{location}]": name
        for name, location in coordinator.scene_locations.items()
    }


class SceneSelect(NanoleafEntity, SelectEntity):
    """Which scene the management buttons act on.

    Lists every scene: those on the strip, then those in the library, each
    marked with where it lives. Picking one plays nothing; until one is
    picked it shows the scene playing.
    """

    _attr_translation_key = "scene_to_manage"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NanoleafCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, "scene")

    @property
    def options(self) -> list[str]:
        """Return the scene names, marked with where each scene lives."""
        return list(scene_labels(self.coordinator))

    @property
    def current_option(self) -> str | None:
        """Return the picked scene, or the one playing."""
        managed = self.coordinator.managed_scene
        for label, name in scene_labels(self.coordinator).items():
            if name == managed:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Point at a scene."""
        self.coordinator.scene_selected = scene_labels(self.coordinator)[option]
        self.coordinator.async_update_listeners()
