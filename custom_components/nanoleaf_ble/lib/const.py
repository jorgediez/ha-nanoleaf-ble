"""Constants of the Nanoleaf Essentials Bluetooth LE protocol.

Everything here comes from the Nanoleaf Android app; see docs/PROTOCOL.md for
where each value was found and what has been verified on hardware.
"""

from __future__ import annotations

from typing import Final

#: Bluetooth SIG company identifier Nanoleaf advertises under (0x080B).
NANOLEAF_COMPANY_ID: Final = 0x080B

# --------------------------------------------------------------------------- models

#: Models this library is meant to drive, with the product name the app shows.
#: NL62 is a "partner" device: it advertises as NLM0 with the real model in the
#: sub-model field.
SUPPORTED_MODELS: Final[dict[str, str]] = {
    "NL45": "Nanoleaf Essentials A19",
    "NL55": "Nanoleaf Essentials Lightstrip",
    "NL62": "Secretlab MAGRGB",
    "NL68": "Nanoleaf Essentials Lightstrip (Matter)",
}

#: Matter Essentials. On LTPDUv3 these use the 512-bit SRP group.
MATTER_ESSENTIALS_MODELS: Final = frozenset(
    {"NL67", "NL68", "NL53", "NL54", "NL65", "NL70", "SQUB01", "SQUB03"}
)
#: 1D devices (string lights, outdoor and Wi-Fi strips, ...).
ONE_D_MODEL_PREFIXES: Final = (
    "NL71",
    "NL72",
    "NL73",
    "NL75",
    "NL77",
    "NL1D",
    "SQCM01",
    "SQFX03",
    "SQFX04",
    "SQSL01",
)
#: Sense+ switches and buttons.
SENSE_PLUS_MODELS: Final = frozenset({"NL43", "NL44", "SQSL02"})

#: Advertised models whose real model sits in the sub-model field.
PARTNER_MODEL_PREFIX: Final = "NLM"

#: Firmware from this version on speaks LTPDUv3 instead of the legacy protocol.
LTPDU_V3_FIRMWARE: Final = (5, 0, 0)

# --------------------------------------------------------------------------- legacy GATT

LEGACY_UNENCRYPTED_UUID: Final = "a9e0a2de-9aea-11ea-bb37-0242ac130002"
LEGACY_ENCRYPTED_UUID: Final = "6d2adda0-9aea-11ea-bb37-0242ac130002"
LEGACY_SETUP_UUID: Final = "6d2adfda-9aea-11ea-bb37-0242ac130002"

#: Key-derivation salts. HomeKit Essentials use the ASCII ones; "v2" devices
#: (Matter Essentials, 1D, Sense+) use the binary ones.
LEGACY_KEY_SALT: Final = b"AES-NL-OPENAPI-KEY"
LEGACY_IV_SALT: Final = b"AES-NL-OPENAPI-IV"
V2_KEY_SALT: Final = bytes.fromhex(
    "bca9e39738e2611c25d243c985d3c592ccbee330e077b89d7978f5fc785cb5e8"
)
V2_IV_SALT: Final = bytes.fromhex(
    "41e2e9eb5e5fa56e800e346d8c2b600742eb49839626faecdc37fb94a3a3c202"
)

#: Legacy reply to a pairing attempt when the device takes no more controllers.
LEGACY_NO_MORE_PAIRINGS: Final = b"\x86"

# --------------------------------------------------------------------------- LTPDUv3 GATT

V3_WRITE_UUID: Final = "a1c10000-8a2f-84c9-1562-f8ac7b913ed4"
V3_INDICATE_UUID: Final = "a1c20000-c78e-15fc-429b-57e13ca8d26f"

#: RFC 5054 2048-bit group, used by every LTPDUv3 device except Matter Essentials.
SRP_N_2048: Final = int(
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050A37329CBB4"
    "A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50E8083969EDB767B0CF60"
    "95179A163AB3661A05FBD5FAAAE82918A9962F0B93B855F97993EC975EEAA80D740ADBF4FF"
    "747359D041D5C33EA71D281E446B14773BCA97B43A23FB801676BD207A436C6481F1D2B907"
    "8717461A5B9D32E688F87748544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB37861"
    "60279004E57AE6AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DB"
    "FBB694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73",
    16,
)
#: Nanoleaf's own 512-bit group for Matter Essentials.
SRP_N_512: Final = int(
    "D66AAFE8E245F9AC245A199F62CE61AB8FA90A4D80C71CD2ADFD0B9DA163B29F2A34AFBDB3"
    "B1B5D0102559CE63D8B6E86B0AA59C14E79D4AA62D1748E4249DF3",
    16,
)
SRP_G: Final = 2

# --------------------------------------------------------------------------- scenes

#: A strip holds this many scenes in total, built-ins included
#: (AccessoryUtilsKt.effectsMaxLimit, confirmed on an NL62).
MAX_SCENES: Final = 12

#: IDs for the scenes this integration stores. The app picks a random number
#: masked with 0x0F, so 0-15; 0 is left out here because the current-effect
#: query answers 0x00 for "nothing playing", and a scene 0 would look the same.
USER_SCENE_IDS: Final = range(1, 16)

#: The current-effect query answers this while an unsaved preview plays.
PREVIEW_SCENE_ID: Final = 0xFF
#: ... and this while nothing plays (off, or a static colour).
NO_SCENE_ID: Final = 0x00

#: Factory firmware scenes. There is no known way to put one back once deleted.
PROTECTED_SCENE_IDS: Final = frozenset(range(250, 255))

#: Scene names per product. Names are never stored on the device: the app maps
#: IDs to names with these tables (Constants in the app).
SECRETLAB_SCENE_NAMES: Final[dict[int, str]] = {
    244: "Cyberpunk 2077",
    245: "VALORANT",
    246: "Assassin's Creed",
    247: "Dark Knight",
    248: "The Joker",
    249: "Attack on Titan",
    250: "Northern Lights",
    251: "Secretlab Signature",
    252: "Cloud9",
    253: "Team Liquid",
    254: "Hip Hop",
}
ESSENTIALS_SCENE_NAMES: Final[dict[int, str]] = {
    251: "Northern Lights",
    252: "Blaze",
    253: "Date Night",
    254: "Starlight",
}


def builtin_scene_names(model: str) -> dict[int, str]:
    """Return the built-in scene names the app shows for a model."""
    return SECRETLAB_SCENE_NAMES if model == "NL62" else ESSENTIALS_SCENE_NAMES
