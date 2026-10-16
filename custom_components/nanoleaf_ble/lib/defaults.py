"""The default scenes the Nanoleaf app writes to Secretlab strips.

Scenes 250-254 come with the firmware. The app writes 244-249 itself, after
pairing, from definitions built into it (SecretLabsDefaultSimpleSceneWriter).
These are the exact bytes the current app sends; strips written by older app
versions carry slightly different copies (colours within 1-2 units, and 247 as
stripes rather than flow).
"""

from __future__ import annotations

from functools import cache

from .scenes import Scene, decode_scene

_SECRETLAB_DEFAULTS_HEX: dict[int, str] = {
    244: "0105f4010a00010207020de8e3306d55",  # Cyberpunk 2077
    245: "0105f506120118020d043c046458e7da58e7da000000",  # VALORANT
    246: "0106f60508000001020a03003264000062000062",  # Assassin's Creed
    247: "0106f705230000010207020000000aa064",  # Dark Knight
    248: "0105f80614011502070240aa641561cd",  # The Joker
    249: "0106f9050c050101020d043483cf241315098cb4241315",  # Attack on Titan
}


@cache
def secretlab_defaults() -> dict[int, Scene]:
    """Return the app-written Secretlab default scenes by ID."""
    return {
        scene_id: decode_scene(bytes.fromhex(data))[1]
        for scene_id, data in _SECRETLAB_DEFAULTS_HEX.items()
    }


def default_scenes(model: str) -> dict[int, Scene]:
    """Return the scenes the app writes to a model after pairing."""
    return secretlab_defaults() if model == "NL62" else {}
