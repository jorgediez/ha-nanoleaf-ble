"""The Nanoleaf community scene library (my.nanoleaf.me).

Search and download need no account. Downloads are animations written for the
light panels; this converts them to a strip scene exactly the way the app does
(NanoleafEffect.buildFromAnimationJson + toSimpleSceneTLV), verified byte for
byte against a scene the app stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from .exceptions import NanoleafCloudError
from .scenes import Direction, EffectType, Scene, app_palette

CLOUD_API = "https://my.nanoleaf.me/api/"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: pluginUuid -> strip effect. Anything else becomes Random, as in the app.
PLUGIN_EFFECTS: dict[str, EffectType] = {
    "b3fd723a-aae8-4c99-bf2b-087159e0ef53": EffectType.FADE,
    "ba632d3e-9c2b-4413-a965-510c839b3f71": EffectType.RANDOM,
    "70b7c636-6bf8-491f-89c1-f4103508d642": EffectType.HIGHLIGHT,
    "027842e4-e1d6-4a4c-a731-be74a1ebd4cf": EffectType.FLOW,
    "Motion_STRIPES": EffectType.STRIPES,
}
_DIRECTIONS = {d.name.lower(): d for d in Direction}


@dataclass(frozen=True, slots=True)
class CommunityScene:
    """One search result."""

    key: str
    name: str
    creator: str
    plugin: str
    featured: bool
    downloads: int

    @classmethod
    def from_search(cls, item: dict[str, Any]) -> CommunityScene:
        """Build from one entry of a search reply."""
        return cls(
            key=str(item["key"]),
            name=str(item.get("effect_name") or "Unnamed"),
            creator=str((item.get("creator") or {}).get("display_name") or ""),
            plugin=str((item.get("plugin") or {}).get("name") or ""),
            featured=bool(item.get("featured")),
            downloads=int(item.get("downloads") or 0),
        )


class CommunityClient:
    """Search and download community scenes."""

    def __init__(self, session: aiohttp.ClientSession, user_agent: str) -> None:
        """Use the caller's HTTP session."""
        self._session = session
        self._user_agent = user_agent

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with self._session.request(
                method,
                CLOUD_API + path,
                json=body,
                headers={"User-Agent": self._user_agent},
                timeout=_TIMEOUT,
            ) as response:
                response.raise_for_status()
                # The download redirects to S3, which may label JSON loosely.
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise NanoleafCloudError(f"{method} {path}: {exc}") from exc

    async def search(
        self,
        query: str = "",
        *,
        featured: bool = False,
        sort: str | None = None,
        items: int = 20,
    ) -> list[CommunityScene]:
        """Search colour scenes. ``sort`` is "recent" or "top"."""
        body: dict[str, Any] = {
            "page": 1,
            "items": items,
            "featured": featured,
            "type": "color",
        }
        if query:
            body["query"] = query
        if sort:
            body["sort"] = sort
        reply = await self._request("POST", "v1/effects/search", body)
        if not isinstance(reply, dict):
            raise NanoleafCloudError("search reply is not an object")
        return [
            CommunityScene.from_search(item)
            for item in reply.get("data") or []
            if isinstance(item, dict) and item.get("key")
        ]

    async def download(self, key: str) -> dict[str, Any]:
        """Download a scene's animation JSON."""
        reply = await self._request("GET", f"v1/effects/download/{key}")
        if not isinstance(reply, dict):
            raise NanoleafCloudError("download reply is not an object")
        return reply


def scene_from_animation(animation: dict[str, Any]) -> tuple[Scene, list[str]]:
    """Convert a downloaded animation to a strip scene, as the app does.

    Return the scene and warnings about what the strip cannot reproduce.
    """
    warnings: list[str] = []
    plugin = str(animation.get("pluginUuid", ""))
    effect = PLUGIN_EFFECTS.get(plugin)
    options = {
        option.get("name"): option.get("value")
        for option in animation.get("pluginOptions") or []
        if isinstance(option, dict)
    }
    if effect is None:
        # The app falls back to Random and drops the plugin's own options
        # (verified: a featured Radiate with transTime 9 was stored as [24, 0]).
        effect, options = EffectType.RANDOM, {}
        warnings.append(
            f"{animation.get('animType') or plugin or 'this'} motion has no strip "
            "equivalent; it plays as Random with default timing"
        )

    def number(name: str, default: int) -> int:
        value = options.get(name, default)
        # The strip field is one byte and the app writes value & 0xFF.
        return int(value) & 0xFF if isinstance(value, int | float) else default

    loop_value = options.get("loop", animation.get("loop", True))
    loop = 0 if loop_value in (False, 0, "false") else 1
    direction = _DIRECTIONS.get(str(options.get("linDirection", "left")).lower(), 0)
    speed, delay = number("transTime", 24), number("delayTime", 0)
    params = {
        EffectType.FADE: (speed, delay, loop),
        EffectType.RANDOM: (speed, delay),
        EffectType.HIGHLIGHT: (speed, delay, number("mainColorProb", 80)),
        EffectType.FLOW: (speed, delay, direction, loop),
        EffectType.STRIPES: (speed, direction, number("segment", 50)),
    }[effect]

    if animation.get("colorType", "HSB") != "HSB":
        warnings.append(
            f"colour type {animation.get('colorType')} is not HSB; colours may differ"
        )
    colours = []
    for colour in animation.get("palette") or []:
        if not isinstance(colour, dict):
            continue
        colours.append(
            (
                min(max(int(colour.get("hue", 0)), 0), 360),
                min(max(int(colour.get("saturation", 0)), 0), 100),
                min(max(int(colour.get("brightness", 0)), 0), 100),
            )
        )
    if len(colours) > 7:
        warnings.append(f"the strip plays the first 7 of {len(colours)} colours")
    if not colours:
        raise NanoleafCloudError("the scene has no colours")
    return Scene(effect, params, app_palette(colours)), warnings
