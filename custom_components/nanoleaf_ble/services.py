"""Actions: create, preview and manage scenes, and use the community library.

Actions that touch a strip take any target: its light, its device, or an
area, floor or label that includes it. Library actions (rename, delete) need no
target, since the library is shared by every strip.
"""

from __future__ import annotations

import colorsys
from functools import partial
import inspect
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
    Platform,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    service,
)

from .const import (
    ATTR_ADD_TO_STRIP,
    ATTR_COLOURS,
    ATTR_EFFECT,
    ATTR_FEATURED,
    ATTR_KEY,
    ATTR_NAME,
    ATTR_NEW_NAME,
    ATTR_PLAY,
    ATTR_QUERY,
    ATTR_SCENE,
    ATTR_SORT,
    DATA_STORE,
    DOMAIN,
    SERVICE_ADD_SCENE_TO_STRIP,
    SERVICE_CREATE_SCENE,
    SERVICE_DELETE_LIBRARY_SCENE,
    SERVICE_PREVIEW_COMMUNITY_SCENE,
    SERVICE_PREVIEW_SCENE,
    SERVICE_REMOVE_SCENE_FROM_STRIP,
    SERVICE_RENAME_SCENE,
    SERVICE_RESTORE_DEFAULT_SCENES,
    SERVICE_SAVE_COMMUNITY_SCENE,
    SERVICE_SEARCH_COMMUNITY_SCENES,
)
from .coordinator import NanoleafCoordinator
from .lib import (
    MAX_COLOURS,
    Direction,
    EffectType,
    NanoleafCloudError,
    PaletteColour,
    Scene,
)

#: Effects a scene can be created with. Stream control takes no palette motion.
EFFECTS = {
    effect.name.lower(): effect
    for effect in EffectType
    if effect is not EffectType.STREAM
}
DIRECTIONS = {direction.name.lower(): direction for direction in Direction}
SETTINGS = ("speed", "delay", "segment", "main_colour_probability")


def _colour(value: Any) -> PaletteColour:
    """Accept [r, g, b] or {hue, saturation, brightness[, repeat]}."""
    try:
        if isinstance(value, dict):
            return PaletteColour(
                hue=int(value["hue"]),
                saturation=int(value["saturation"]),
                brightness=int(value.get("brightness", 100)),
                repeat=int(value.get("repeat", 1)),
            )
        red, green, blue = (int(part) for part in value)
        if not all(0 <= part <= 255 for part in (red, green, blue)):
            raise ValueError("RGB values are 0-255")
        hue, saturation, brightness = colorsys.rgb_to_hsv(
            red / 255, green / 255, blue / 255
        )
        return PaletteColour(
            round(hue * 360) % 360, round(saturation * 100), round(brightness * 100)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise vol.Invalid(f"invalid colour {value!r}: {exc}") from exc


SCENE_FIELDS = {
    vol.Required(ATTR_EFFECT): vol.In(EFFECTS),
    vol.Required(ATTR_COLOURS): vol.All(
        cv.ensure_list, vol.Length(min=1, max=MAX_COLOURS), [_colour]
    ),
    vol.Optional("speed"): vol.All(vol.Coerce(int), vol.Range(1, 255)),
    vol.Optional("delay"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
    vol.Optional("loop"): cv.boolean,
    vol.Optional("direction"): vol.In(DIRECTIONS),
    vol.Optional("segment"): vol.All(vol.Coerce(int), vol.Range(1, 255)),
    vol.Optional("main_colour_probability"): vol.All(
        vol.Coerce(int), vol.Range(0, 100)
    ),
}
#: Every way to name strips: entity, device, area, floor, label (and, from
#: 2026.11, exclude). Whether one is required is checked when the action runs,
#: so a missing target gets this integration's message.
TARGET = cv.TARGET_SERVICE_FIELDS
OPTIONAL_TARGET = cv.TARGET_SERVICE_FIELDS
TARGET_KEYS = (
    ATTR_ENTITY_ID,
    ATTR_DEVICE_ID,
    ATTR_AREA_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
)

# Home Assistant 2025.x takes hass before the call; later releases (2026.9 at
# the latest) take only the call. Drop this once hacs.json's minimum does.
_EXTRACT_TAKES_HASS = (
    "hass" in inspect.signature(service.async_extract_entity_ids).parameters
)

CREATE_SCENE_SCHEMA = vol.Schema(
    {
        **OPTIONAL_TARGET,
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=64)),
        vol.Optional(ATTR_ADD_TO_STRIP, default=False): cv.boolean,
        vol.Optional(ATTR_PLAY, default=False): cv.boolean,
        **SCENE_FIELDS,
    }
)
PREVIEW_SCENE_SCHEMA = vol.Schema({**TARGET, **SCENE_FIELDS})
STRIP_SCENE_SCHEMA = vol.Schema({**TARGET, vol.Required(ATTR_SCENE): cv.string})
RENAME_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCENE): cv.string,
        vol.Required(ATTR_NEW_NAME): vol.All(cv.string, vol.Length(min=1, max=64)),
    }
)
LIBRARY_SCENE_SCHEMA = vol.Schema({vol.Required(ATTR_SCENE): cv.string})
SEARCH_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_QUERY, default=""): cv.string,
        vol.Optional(ATTR_FEATURED, default=False): cv.boolean,
        vol.Optional(ATTR_SORT): vol.In(["recent", "top"]),
    }
)
COMMUNITY_KEY_SCHEMA = vol.Schema({**TARGET, vol.Required(ATTR_KEY): cv.string})
SAVE_COMMUNITY_SCHEMA = vol.Schema(
    {
        **OPTIONAL_TARGET,
        vol.Required(ATTR_KEY): cv.string,
        vol.Optional(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=64)),
        vol.Optional(ATTR_ADD_TO_STRIP, default=False): cv.boolean,
    }
)
TARGET_SCHEMA = vol.Schema(TARGET)


def _scene_from_call(data: dict[str, Any]) -> Scene:
    settings: dict[str, int | None] = {name: data.get(name) for name in SETTINGS}
    if "loop" in data:
        settings["loop"] = int(data["loop"])
    if "direction" in data:
        settings["direction"] = DIRECTIONS[data["direction"]]
    effect = EFFECTS[data[ATTR_EFFECT]]
    try:
        return Scene.build(effect, data[ATTR_COLOURS], **settings)
    except ValueError as exc:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_scene",
            translation_placeholders={"error": str(exc)},
        ) from exc


def _named(value: Any) -> list[str]:
    """Return the ids a target key names; "none" and empty names nothing."""
    if value is None or value == "none":
        return []
    return list(cv.ensure_list(value))


async def _target_entity_ids(hass: HomeAssistant, call: ServiceCall) -> set[str]:
    """Return the entities the target reaches, after any exclude."""
    if _EXTRACT_TAKES_HASS:
        return await service.async_extract_entity_ids(hass, call)
    return await service.async_extract_entity_ids(call)


async def _coordinators(
    hass: HomeAssistant, call: ServiceCall
) -> list[NanoleafCoordinator]:
    """Return the coordinators of the targeted strips.

    Lights named directly must be strips that are loaded. Devices, areas,
    floors and labels may also hold other things, which are skipped, and strips
    that are not loaded, which are skipped too.
    """
    if not any(_named(call.data.get(key)) for key in TARGET_KEYS):
        return []
    registry = er.async_get(hass)
    for entity_id in _named(call.data.get(ATTR_ENTITY_ID)):
        entry_id = (
            entity.config_entry_id
            if (entity := registry.async_get(entity_id)) is not None
            else None
        )
        entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_strip",
                translation_placeholders={"entity_id": entity_id},
            )
    # Entities rather than config entries, so that a strip whose light is
    # excluded is left out even when its device is in a targeted area.
    coordinators: list[NanoleafCoordinator] = []
    for entity_id in sorted(await _target_entity_ids(hass, call)):
        entity = registry.async_get(entity_id)
        if (
            entity is None
            or entity.platform != DOMAIN
            or entity.domain != Platform.LIGHT
            or entity.config_entry_id is None
        ):
            continue
        entry = hass.config_entries.async_get_entry(entity.config_entry_id)
        if (
            entry is not None
            and entry.state is ConfigEntryState.LOADED
            and entry.runtime_data not in coordinators
        ):
            coordinators.append(entry.runtime_data)
    if not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_loaded_strip_targeted"
        )
    return coordinators


async def _require_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[NanoleafCoordinator]:
    coordinators = await _coordinators(hass, call)
    if not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="target_required"
        )
    return coordinators


def _check_name_free(hass: HomeAssistant, name: str) -> None:
    if hass.data[DATA_STORE].find(name) is not None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_exists",
            translation_placeholders={"scene": name},
        )


async def _create_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    scene = _scene_from_call(dict(call.data))
    name = call.data[ATTR_NAME]
    _check_name_free(hass, name)
    coordinators = await _coordinators(hass, call)
    if (call.data[ATTR_ADD_TO_STRIP] or call.data[ATTR_PLAY]) and not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="target_required"
        )
    item = hass.data[DATA_STORE].async_add(name, scene)
    for coordinator in coordinators:
        if call.data[ATTR_ADD_TO_STRIP]:
            scene_id = await coordinator.async_add_to_strip(item)
            if call.data[ATTR_PLAY]:
                await coordinator.async_run(
                    lambda device, sid=scene_id: device.activate_scene(sid)
                )
                await coordinator.async_request_refresh()
        elif call.data[ATTR_PLAY]:
            await coordinator.async_preview(item.scene, item.name)


async def _preview_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    scene = _scene_from_call(dict(call.data))
    for coordinator in await _require_targets(hass, call):
        await coordinator.async_preview(scene, None)


async def _add_scene_to_strip(hass: HomeAssistant, call: ServiceCall) -> None:
    for coordinator in await _require_targets(hass, call):
        await coordinator.async_add_to_strip(
            coordinator.require_library_scene(call.data[ATTR_SCENE])
        )


async def _remove_scene_from_strip(hass: HomeAssistant, call: ServiceCall) -> None:
    for coordinator in await _require_targets(hass, call):
        await coordinator.async_remove_from_strip(call.data[ATTR_SCENE])


async def _rename_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    store = hass.data[DATA_STORE]
    item = store.find(call.data[ATTR_SCENE])
    if item is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_not_in_library",
            translation_placeholders={"scene": call.data[ATTR_SCENE]},
        )
    other = store.find(call.data[ATTR_NEW_NAME])
    if other is not None and other.id != item.id:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_exists",
            translation_placeholders={"scene": call.data[ATTR_NEW_NAME]},
        )
    store.async_rename(item.id, call.data[ATTR_NEW_NAME])


async def _delete_library_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    store = hass.data[DATA_STORE]
    item = store.find(call.data[ATTR_SCENE])
    if item is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_not_in_library",
            translation_placeholders={"scene": call.data[ATTR_SCENE]},
        )
    store.async_delete(item.id)


async def _search_community_scenes(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    coordinator = _any_coordinator(hass)
    client = await coordinator.community.async_client()
    try:
        results = await client.search(
            call.data[ATTR_QUERY],
            featured=call.data[ATTR_FEATURED],
            sort=call.data.get(ATTR_SORT),
        )
    except NanoleafCloudError as exc:
        raise coordinator.community.error(exc) from exc
    return {
        "scenes": [
            {
                "key": result.key,
                "name": result.name,
                "creator": result.creator,
                "motion": result.plugin,
                "featured": result.featured,
                "downloads": result.downloads,
            }
            for result in results
        ]
    }


async def _preview_community_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    for coordinator in await _require_targets(hass, call):
        scene, _warnings = await coordinator.community.async_scene(call.data[ATTR_KEY])
        await coordinator.async_preview(scene, None)


async def _save_community_scene(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinators = await _coordinators(hass, call)
    if call.data[ATTR_ADD_TO_STRIP] and not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="target_required"
        )
    coordinator = coordinators[0] if coordinators else _any_coordinator(hass)
    item = await coordinator.community.async_save(
        call.data[ATTR_KEY], call.data.get(ATTR_NAME)
    )
    if call.data[ATTR_ADD_TO_STRIP]:
        for target in coordinators:
            await target.async_add_to_strip(item)


async def _restore_default_scenes(hass: HomeAssistant, call: ServiceCall) -> None:
    for coordinator in await _require_targets(hass, call):
        await coordinator.async_restore_default_scenes()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_SCENE, partial(_create_scene, hass), CREATE_SCENE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PREVIEW_SCENE,
        partial(_preview_scene, hass),
        PREVIEW_SCENE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_SCENE_TO_STRIP,
        partial(_add_scene_to_strip, hass),
        STRIP_SCENE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_SCENE_FROM_STRIP,
        partial(_remove_scene_from_strip, hass),
        STRIP_SCENE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RENAME_SCENE, partial(_rename_scene, hass), RENAME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_LIBRARY_SCENE,
        partial(_delete_library_scene, hass),
        LIBRARY_SCENE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_COMMUNITY_SCENES,
        partial(_search_community_scenes, hass),
        SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PREVIEW_COMMUNITY_SCENE,
        partial(_preview_community_scene, hass),
        COMMUNITY_KEY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_COMMUNITY_SCENE,
        partial(_save_community_scene, hass),
        SAVE_COMMUNITY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_DEFAULT_SCENES,
        partial(_restore_default_scenes, hass),
        TARGET_SCHEMA,
    )


def _any_coordinator(hass: HomeAssistant) -> NanoleafCoordinator:
    """Return a loaded strip's coordinator, for actions that need no strip."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="no_strips_loaded"
    )
