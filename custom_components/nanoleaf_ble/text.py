"""Search the Nanoleaf community scene library."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity

# A search talks to the cloud, not the strip.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the search box where the strip supports scenes."""
    coordinator = entry.runtime_data
    if coordinator.supports_scenes:
        async_add_entities([CommunitySearch(coordinator)])


class CommunitySearch(NanoleafEntity, TextEntity):
    """Setting the text runs a search; the results fill the Community scene select."""

    _attr_translation_key = "community_search"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 100

    def __init__(self, coordinator: NanoleafCoordinator) -> None:
        """Initialise the search box."""
        super().__init__(coordinator, "community_search")

    @property
    def native_value(self) -> str:
        """Return the last query."""
        return self.coordinator.community_query

    async def async_set_value(self, value: str) -> None:
        """Search. An empty query lists recent scenes."""
        await self.coordinator.async_community_search(value)
