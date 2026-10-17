"""Restore on/off after power loss: whether a power cut keeps the strip off when it was off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity

# Writes go through the coordinator, whose session lock already serialises them.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch where the firmware has the setting.

    It lives on the scene channel, which firmware 5 replaced; and firmware
    before 1.2.13 lacks it (the first read tells).
    """
    coordinator = entry.runtime_data
    if coordinator.supports_scenes and coordinator.supports_power_loss_recovery:
        async_add_entities([PowerLossRecoverySwitch(coordinator)])


class PowerLossRecoverySwitch(NanoleafEntity, SwitchEntity):
    """On: after a power cut the strip comes back on or off, as it was. Off: it always comes back on."""

    _attr_translation_key = "power_loss_recovery"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NanoleafCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, "power_loss_recovery")

    @property
    def available(self) -> bool:
        """Return whether the setting is known."""
        return (
            super().available and self.coordinator.data.power_loss_recovery is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the strip restores its on/off state after a power cut."""
        return self.coordinator.data.power_loss_recovery

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Make the strip come back on or off, as before the power cut."""
        await self.coordinator.async_set_power_loss_recovery(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Make the strip always come back on after a power cut."""
        await self.coordinator.async_set_power_loss_recovery(False)
