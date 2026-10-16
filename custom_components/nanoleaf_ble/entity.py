"""Base entity for Nanoleaf Essentials (Bluetooth)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import NanoleafCoordinator


class NanoleafEntity(CoordinatorEntity[NanoleafCoordinator]):
    """An entity of the one device each strip is.

    Every entity describes the device in full. Platforms can set up in any order,
    and an entity that only named the device by its identifier could register
    first, before anything had given the device a name.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: NanoleafCoordinator, key: str | None) -> None:
        """Initialise from the shared coordinator.

        ``key`` tells the strip's entities apart. The light passes None: its
        unique id is the bare entry id.
        """
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = (
            entry.entry_id if key is None else f"{entry.entry_id}_{key}"
        )

        info = coordinator.device_information
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=coordinator.advertisement.product_name,
            model_id=coordinator.model,
            name=entry.title,
            sw_version=(info and info.firmware_version)
            or coordinator.advertisement.firmware_version,
            hw_version=info.hardware_version if info else None,
            serial_number=info.serial_number if info else None,
        )
