"""Simple scenes: the multi-colour scenes Essentials devices store and play.

A scene is a metadata TLV (scene ID, effect type, motion parameters) and a
palette TLV, both with a 1-byte type and a 1-byte length. Times are in tenths
of a second (measured on video). See docs/PROTOCOL.md §4.4, §7.1 and §11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import hashlib
from typing import Any

from .exceptions import NanoleafProtocolError

_TLV_METADATA = 0x01
_TLV_PALETTE = 0x02

#: The palette holds this many entries at most (the app never sends more).
MAX_COLOURS = 7

_REPEAT_FLAG = 1 << 23


class EffectType(IntEnum):
    """Motion of a simple scene."""

    FADE = 1
    RANDOM = 2
    HIGHLIGHT = 3
    STREAM = 4
    FLOW = 5
    STRIPES = 6


class Direction(IntEnum):
    """Direction of a flow or stripes motion."""

    LEFT = 0
    RIGHT = 1
    UP = 2
    DOWN = 3


#: Parameter names per effect type, in wire order.
PARAMETERS: dict[EffectType, tuple[str, ...]] = {
    EffectType.FADE: ("speed", "delay", "loop"),
    EffectType.RANDOM: ("speed", "delay"),
    EffectType.HIGHLIGHT: ("speed", "delay", "main_colour_probability"),
    EffectType.STREAM: (),
    EffectType.FLOW: ("speed", "delay", "direction", "loop"),
    EffectType.STRIPES: ("speed", "direction", "segment"),
}

#: The app's defaults (NanoleafEffect).
DEFAULTS: dict[str, int] = {
    "speed": 24,
    "delay": 0,
    "loop": 1,
    "direction": Direction.LEFT,
    "segment": 50,
    "main_colour_probability": 80,
}

#: Valid range of each parameter. Every one is a single byte on the wire.
RANGES: dict[str, tuple[int, int]] = {
    "speed": (1, 255),
    "delay": (0, 255),
    "loop": (0, 1),
    "direction": (0, 3),
    "segment": (1, 255),
    "main_colour_probability": (0, 100),
}


@dataclass(frozen=True, slots=True)
class PaletteColour:
    """One palette entry: hue 0-360, saturation and brightness 0-100.

    ``repeat`` is the total number of copies of the colour the device plays
    (verified on video); 1 means the entry carries no repeat count.
    """

    hue: int
    saturation: int
    brightness: int
    repeat: int = 1

    def __post_init__(self) -> None:
        """Reject values the 9/7/7-bit wire fields cannot hold."""
        if not (
            0 <= self.hue <= 360
            and 0 <= self.saturation <= 100
            and 0 <= self.brightness <= 100
            and 1 <= self.repeat <= 255
        ):
            raise ValueError(f"palette colour out of range: {self}")

    def as_dict(self) -> dict[str, int]:
        """Return the colour as a JSON-friendly dict."""
        return {
            "hue": self.hue,
            "saturation": self.saturation,
            "brightness": self.brightness,
            "repeat": self.repeat,
        }


@dataclass(frozen=True, slots=True)
class Scene:
    """A simple scene, without the ID it is stored under."""

    effect: EffectType
    params: tuple[int, ...]
    palette: tuple[PaletteColour, ...] = field(default=())

    @classmethod
    def build(
        cls,
        effect: EffectType,
        colours: list[PaletteColour],
        **settings: int,
    ) -> Scene:
        """Build a scene from named settings, filling in the app's defaults.

        Raise ValueError for a setting the effect does not take, a value out of
        range, or a palette that is empty or too long.
        """
        names = PARAMETERS[effect]
        unknown = {k for k, v in settings.items() if v is not None} - set(names)
        if unknown:
            raise ValueError(
                f"{effect.name.lower()} does not take {', '.join(sorted(unknown))}"
            )
        params = []
        for name in names:
            value = settings.get(name)
            value = DEFAULTS[name] if value is None else int(value)
            low, high = RANGES[name]
            if not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
            params.append(value)
        if not 1 <= len(colours) <= MAX_COLOURS:
            raise ValueError(f"a scene needs 1 to {MAX_COLOURS} colours")
        return cls(effect, tuple(params), tuple(colours))

    @property
    def settings(self) -> dict[str, int]:
        """Return the motion parameters by name."""
        return dict(zip(PARAMETERS.get(self.effect, ()), self.params, strict=False))

    def encode(self, scene_id: int) -> bytes:
        """Encode the scene's TLVs, stored under (or previewed as) ``scene_id``."""
        metadata = bytes([scene_id, self.effect, *self.params])
        palette = bytearray([len(self.palette)])
        for colour in self.palette:
            word = (colour.hue << 14) | (colour.saturation << 7) | colour.brightness
            if colour.repeat > 1:
                word |= _REPEAT_FLAG
            palette += word.to_bytes(3, "big")
            if colour.repeat > 1:
                palette.append(colour.repeat)
        return (
            bytes([_TLV_METADATA, len(metadata)])
            + metadata
            + bytes([_TLV_PALETTE, len(palette)])
            + bytes(palette)
        )

    @property
    def fingerprint(self) -> str:
        """Return a short hash of the scene's content, independent of its ID."""
        return hashlib.sha256(self.encode(0)).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        """Return the scene as a JSON-friendly dict (the form the store keeps)."""
        return {
            "effect": self.effect.name.lower(),
            "params": list(self.params),
            "palette": [colour.as_dict() for colour in self.palette],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scene:
        """Rebuild a scene from as_dict()."""
        return cls(
            EffectType[data["effect"].upper()],
            tuple(int(p) for p in data["params"]),
            tuple(PaletteColour(**colour) for colour in data["palette"]),
        )


def decode_scene(data: bytes) -> tuple[int, Scene]:
    """Decode stored scene TLVs into (scene ID, scene)."""
    scene_id: int | None = None
    effect: EffectType | None = None
    params: tuple[int, ...] = ()
    palette: list[PaletteColour] = []
    offset = 0
    while offset + 2 <= len(data):
        tlv_type, length = data[offset], data[offset + 1]
        value = data[offset + 2 : offset + 2 + length]
        offset += 2 + length
        if tlv_type == _TLV_METADATA and len(value) >= 2:
            scene_id = value[0]
            try:
                effect = EffectType(value[1])
            except ValueError as exc:
                raise NanoleafProtocolError(f"unknown effect type {value[1]}") from exc
            params = tuple(value[2:])
        elif tlv_type == _TLV_PALETTE and value:
            palette = _decode_palette(value)
    if scene_id is None or effect is None:
        raise NanoleafProtocolError(f"scene without metadata: {data.hex()}")
    return scene_id, Scene(effect, params, tuple(palette))


def _decode_palette(value: bytes) -> list[PaletteColour]:
    count, colours, offset = value[0], [], 1
    while offset + 3 <= len(value) and len(colours) < count:
        word = int.from_bytes(value[offset : offset + 3], "big")
        offset += 3
        repeat = 1
        if word & _REPEAT_FLAG and offset < len(value):
            repeat = max(1, value[offset])
            offset += 1
        colours.append(
            PaletteColour(
                min((word >> 14) & 0x1FF, 360),
                min((word >> 7) & 0x7F, 100),
                min(word & 0x7F, 100),
                repeat,
            )
        )
    return colours


def app_palette(colours: list[tuple[int, int, int]]) -> tuple[PaletteColour, ...]:
    """Reproduce the app's palette encoder, quirk included.

    A colour followed by two or more identical colours gets a repeat count of
    the number that follow, yet those duplicates are still sent as their own
    entries (NanoleafEffect.toSimpleSceneTLV). Byte-for-byte the same as an
    app-written scene, which is what keeps clones faithful.
    """
    colours = colours[:MAX_COLOURS]

    def following(start: int) -> int:
        count, index = 0, start + 1
        while index < len(colours) and colours[index] == colours[start]:
            count, index = count + 1, index + 1
        return count

    out, index = [], 0
    for hue, saturation, brightness in colours:
        repeat = following(index) if index < len(colours) else 0
        out.append(
            PaletteColour(hue, saturation, brightness, repeat if repeat > 1 else 1)
        )
        index += repeat if repeat > 1 else 1
    return tuple(out)
