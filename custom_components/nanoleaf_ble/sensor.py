"""Scene slots, and diagnostic details of the strip."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity

# Read-only: the coordinator fetches everything.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NanoleafSensorDescription(SensorEntityDescription):
    """Describes a sensor and where its value comes from."""

    value_fn: Callable[[NanoleafCoordinator], str | int | None]
    #: Only created where the strip supports scenes.
    scenes_only: bool = False


SENSORS: tuple[NanoleafSensorDescription, ...] = (
    NanoleafSensorDescription(
        key="free_scene_slots",
        translation_key="free_scene_slots",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.data.free_slots,
        scenes_only=True,
    ),
    NanoleafSensorDescription(
        key="scenes_on_strip",
        translation_key="scenes_on_strip",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: len(c.data.scene_ids),
        scenes_only=True,
    ),
    NanoleafSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: (
            (c.device_information and c.device_information.firmware_version)
            or c.advertisement.firmware_version
        ),
    ),
    NanoleafSensorDescription(
        key="hardware_version",
        translation_key="hardware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: (
            c.device_information and c.device_information.hardware_version
        ),
    ),
    NanoleafSensorDescription(
        key="serial_number",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.device_information and c.device_information.serial_number,
    ),
    NanoleafSensorDescription(
        key="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.model,
    ),
    NanoleafSensorDescription(
        key="protocol",
        translation_key="protocol",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.protocol,
    ),
    NanoleafSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        NanoleafSensor(coordinator, description)
        for description in SENSORS
        if coordinator.supports_scenes or not description.scenes_only
    )


class NanoleafSensor(NanoleafEntity, SensorEntity):
    """A value read from the strip or its advertisement."""

    entity_description: NanoleafSensorDescription

    def __init__(
        self,
        coordinator: NanoleafCoordinator,
        description: NanoleafSensorDescription,
    ) -> None:
        """Initialise from a description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | None:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator)
