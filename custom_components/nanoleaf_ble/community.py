"""Browsing the Nanoleaf community scene library from one strip's entities.

The text entity searches, the select picks a result, and the buttons preview or
save it. They share the state kept here: the last query, its results, the
selected result, and the scenes downloaded recently. Nothing here talks to the
strip; previewing and storing go through the coordinator.
"""

from __future__ import annotations

from collections.abc import Callable
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .const import DOMAIN, PROJECT_URL
from .lib import (
    CommunityClient,
    CommunityScene,
    NanoleafCloudError,
    Scene,
    scene_from_animation,
)
from .library import SOURCE_COMMUNITY, LibraryScene, SceneStore

_LOGGER = logging.getLogger(__name__)

#: Community scenes kept after download, so that previewing one and then
#: saving it downloads it once. The least recently used go first.
COMMUNITY_CACHE_SIZE = 32


class CommunityBrowser:
    """The community search, its results and the downloaded scenes of a strip."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: SceneStore,
        error: Callable[[Exception], HomeAssistantError],
        on_change: Callable[[], None],
    ) -> None:
        """Initialise; ``error`` names the strip, ``on_change`` updates entities."""
        self._hass = hass
        self._store = store
        self.error = error
        self._on_change = on_change
        self.query = ""
        self.results: list[CommunityScene] = []
        self.selected: str | None = None
        self._cache: dict[str, tuple[Scene, list[str]]] = {}

    async def async_client(self) -> CommunityClient:
        """Return a client for the community scene library.

        The User-Agent names the integration and where to reach its maintainer,
        so Nanoleaf can tell who is calling an API it doesn't document.
        """
        integration = await async_get_integration(self._hass, DOMAIN)
        return CommunityClient(
            async_get_clientsession(self._hass),
            f"ha-nanoleaf-ble/{integration.version} (+{PROJECT_URL})",
        )

    async def async_search(self, query: str) -> None:
        """Search the community library; the results feed the select entity."""
        client = await self.async_client()
        try:
            results = await client.search(query.strip())
        except NanoleafCloudError as exc:
            raise self.error(exc) from exc
        self.query = query
        self.results = results
        self.selected = results[0].key if results else None
        self._on_change()

    async def async_scene(self, key: str) -> tuple[Scene, list[str]]:
        """Download and convert a community scene (see COMMUNITY_CACHE_SIZE)."""
        if (converted := self._cache.pop(key, None)) is None:
            client = await self.async_client()
            try:
                animation = await client.download(key)
                converted = scene_from_animation(animation)
            except NanoleafCloudError as exc:
                raise self.error(exc) from exc
            for warning in converted[1]:
                _LOGGER.info("Community scene %s: %s", key, warning)
        # Dicts keep insertion order: re-adding the key makes it the newest.
        self._cache[key] = converted
        while len(self._cache) > COMMUNITY_CACHE_SIZE:
            del self._cache[next(iter(self._cache))]
        return converted

    def result(self, key: str | None) -> CommunityScene | None:
        """Return a search result by key."""
        return next((r for r in self.results if r.key == key), None)

    async def async_save(
        self, key: str, name: str | None = None, creator: str | None = None
    ) -> LibraryScene:
        """Save a community scene to the library (or return it if already there)."""
        scene, _warnings = await self.async_scene(key)
        for item in self._store.library:
            if item.community_key == key:
                return item
        result = self.result(key)
        name = name or (result.name if result else f"Community {key[:8]}")
        if self._store.find(name) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_exists",
                translation_placeholders={"scene": name},
            )
        return self._store.async_add(
            name,
            scene,
            source=SOURCE_COMMUNITY,
            community_key=key,
            creator=creator or (result.creator if result else None),
        )
