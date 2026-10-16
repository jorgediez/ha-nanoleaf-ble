"""The strip as a light: on/off, brightness, colour and scenes as effects."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_KELVIN, MIN_KELVIN
from .coordinator import NanoleafConfigEntry, NanoleafCoordinator
from .entity import NanoleafEntity

# Writes go through the coordinator, whose session lock already serialises them.
PARALLEL_UPDATES = 0


def brightness_to_percent(value: int) -> int:
    """Scale Home Assistant's 1-255 to the strip's 0-100 %, never 0 for "on"."""
    return max(1, round(value * 100 / 255))


def percent_to_brightness(value: int) -> int:
    """Scale the strip's 0-100 % to Home Assistant's 0-255."""
    return round(min(max(value, 0), 100) * 255 / 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoleafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the light."""
    async_add_entities([NanoleafLight(entry.runtime_data)])


class NanoleafLight(NanoleafEntity, LightEntity):
    """The strip."""

    _attr_name = None
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(self, coordinator: NanoleafCoordinator) -> None:
        """Initialise the light, with colour temperature where the strip has it."""
        super().__init__(coordinator, None)
        modes = {ColorMode.HS}
        if coordinator.supports_colour_temperature:
            modes.add(ColorMode.COLOR_TEMP)
        self._attr_supported_color_modes = modes
        if coordinator.supports_scenes:
            self._attr_supported_features = LightEntityFeature.EFFECT

    @property
    def is_on(self) -> bool:
        """Return whether the light is on."""
        return self.coordinator.data.is_on

    @property
    def brightness(self) -> int:
        """Return the brightness, which also dims a playing scene."""
        return percent_to_brightness(self.coordinator.data.light.brightness)

    @property
    def color_mode(self) -> ColorMode:
        """Return the colour mode last set."""
        if self.coordinator.colour_temperature_mode and (
            ColorMode.COLOR_TEMP in self.supported_color_modes
        ):
            return ColorMode.COLOR_TEMP
        return ColorMode.HS

    @property
    def hs_color(self) -> tuple[float, float]:
        """Return the static colour (the last one set, while a scene plays)."""
        light = self.coordinator.data.light
        return (float(light.hue), float(light.saturation))

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the colour temperature, where the strip has one."""
        return self.coordinator.data.light.colour_temperature

    @property
    def effect_list(self) -> list[str] | None:
        """Return the scenes on the strip, then the library scenes."""
        if not self.coordinator.supports_scenes:
            return None
        return list(self.coordinator.effects)

    @property
    def effect(self) -> str | None:
        """Return the playing scene."""
        return self.coordinator.current_effect

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally with a colour, brightness or scene."""
        brightness = (
            brightness_to_percent(kwargs[ATTR_BRIGHTNESS])
            if ATTR_BRIGHTNESS in kwargs
            else None
        )
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            await self.coordinator.async_play_effect(effect)
            if brightness is not None:
                await self.coordinator.async_set_light(brightness=brightness)
            return
        await self.coordinator.async_set_light(
            on=True,
            brightness=brightness,
            hs_colour=kwargs.get(ATTR_HS_COLOR),
            colour_temperature=kwargs.get(ATTR_COLOR_TEMP_KELVIN),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off. Turning on again resumes what was playing."""
        await self.coordinator.async_set_light(on=False)
