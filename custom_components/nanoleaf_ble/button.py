"""Identify, restore the default scenes, and act on the selected scenes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import TARGET_STRIP, NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity
from .lib import default_scenes

# Presses go through the coordinator, whose session lock already serialises them.
PARALLEL_UPDATES = 0


def _no_selection(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def _selected_community_key(coordinator: NanoleafCoordinator) -> str:
    if coordinator.community_selected is None:
        raise _no_selection("no_community_scene_selected")
    return coordinator.community_selected


def _selected_scene(coordinator: NanoleafCoordinator) -> str:
    selected = coordinator.scene_selected
    if selected is None or selected not in coordinator.effects:
        raise _no_selection("no_scene_selected")
    return selected


async def _identify(coordinator: NanoleafCoordinator) -> None:
    await coordinator.async_identify()


async def _restore_defaults(coordinator: NanoleafCoordinator) -> None:
    await coordinator.async_restore_default_scenes()


async def _community_preview(coordinator: NanoleafCoordinator) -> None:
    key = _selected_community_key(coordinator)
    scene, _warnings = await coordinator.async_community_scene(key)
    result = coordinator.community_result(key)
    await coordinator.async_preview(scene, result.name if result else None)


async def _community_save(coordinator: NanoleafCoordinator) -> None:
    await coordinator.async_save_community_scene(_selected_community_key(coordinator))


async def _community_add(coordinator: NanoleafCoordinator) -> None:
    item = await coordinator.async_save_community_scene(
        _selected_community_key(coordinator)
    )
    await coordinator.async_add_to_strip(item)


async def _scene_add(coordinator: NanoleafCoordinator) -> None:
    name = _selected_scene(coordinator)
    if coordinator.effects[name].kind == TARGET_STRIP:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_already_on_strip",
            translation_placeholders={"scene": name},
        )
    await coordinator.async_add_to_strip(coordinator.require_library_scene(name))


async def _scene_remove(coordinator: NanoleafCoordinator) -> None:
    await coordinator.async_remove_from_strip(_selected_scene(coordinator))


async def _scene_delete(coordinator: NanoleafCoordinator) -> None:
    item = coordinator.require_library_scene(_selected_scene(coordinator))
    coordinator.store.async_delete(item.id)


@dataclass(frozen=True, kw_only=True)
class NanoleafButtonDescription(ButtonEntityDescription):
    """Describes a button and what it does."""

    press_fn: Callable[[NanoleafCoordinator], Awaitable[None]]
    available_fn: Callable[[NanoleafCoordinator], bool] = lambda c: c.supports_scenes


BUTTONS: tuple[NanoleafButtonDescription, ...] = (
    NanoleafButtonDescription(
        key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=_identify,
        available_fn=lambda c: True,
    ),
    NanoleafButtonDescription(
        key="restore_default_scenes",
        translation_key="restore_default_scenes",
        entity_category=EntityCategory.CONFIG,
        press_fn=_restore_defaults,
        available_fn=lambda c: c.supports_scenes and bool(default_scenes(c.model)),
    ),
    NanoleafButtonDescription(
        key="community_preview",
        translation_key="community_preview",
        press_fn=_community_preview,
    ),
    NanoleafButtonDescription(
        key="community_save",
        translation_key="community_save",
        press_fn=_community_save,
    ),
    NanoleafButtonDescription(
        key="community_add_to_strip",
        translation_key="community_add_to_strip",
        press_fn=_community_add,
    ),
    NanoleafButtonDescription(
        key="scene_add_to_strip",
        translation_key="scene_add_to_strip",
        press_fn=_scene_add,
    ),
    NanoleafButtonDescription(
        key="scene_remove_from_strip",
        translation_key="scene_remove_from_strip",
        press_fn=_scene_remove,
    ),
    NanoleafButtonDescription(
        key="scene_delete_from_library",
        translation_key="scene_delete_from_library",
        press_fn=_scene_delete,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons the strip supports."""
    coordinator = entry.runtime_data
    async_add_entities(
        NanoleafButton(coordinator, description)
        for description in BUTTONS
        if description.available_fn(coordinator)
    )


class NanoleafButton(NanoleafEntity, ButtonEntity):
    """A button."""

    entity_description: NanoleafButtonDescription

    def __init__(
        self,
        coordinator: NanoleafCoordinator,
        description: NanoleafButtonDescription,
    ) -> None:
        """Initialise from a description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Do what the button says."""
        await self.entity_description.press_fn(self.coordinator)
