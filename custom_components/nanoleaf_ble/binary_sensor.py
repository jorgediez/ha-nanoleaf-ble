"""Whether the Bluetooth link to the strip is up."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity

# Read-only: the coordinator tracks the link.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection sensor."""
    async_add_entities([ConnectionSensor(entry.runtime_data)])


class ConnectionSensor(NanoleafEntity, BinarySensorEntity):
    """On while a logged-in session is open.

    It changes the moment the link drops or comes back, not at the next poll,
    and stays available: "off" is the answer when the strip cannot be reached.
    """

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NanoleafCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        """Return True: the sensor reports the link, up or down."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the session is open."""
        return self.coordinator.connected
