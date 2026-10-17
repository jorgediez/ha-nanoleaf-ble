"""Control Nanoleaf Essentials lights over Bluetooth LE.

A self-contained client for the protocol documented in docs/PROTOCOL.md. It has
no Home Assistant imports and uses relative imports only, so the folder can
become a package of its own unchanged.
"""

from .advertisement import AdvertisementInfo, parse_advertisement
from .cloud import CommunityClient, CommunityScene, scene_from_animation
from .const import (
    MAX_SCENES,
    NANOLEAF_COMPANY_ID,
    NO_SCENE_ID,
    PREVIEW_SCENE_ID,
    PROTECTED_SCENE_IDS,
    SUPPORTED_MODELS,
    UNVERIFIED_MODELS,
    USER_SCENE_IDS,
    builtin_scene_names,
)
from .defaults import default_scenes
from .device import (
    Credentials,
    DeviceInformation,
    LightState,
    NanoleafDevice,
    parse_device_information,
)
from .exceptions import (
    NanoleafAuthError,
    NanoleafCloudError,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafProtocolError,
    NanoleafStripFullError,
    NanoleafTooManyPairingsError,
    NanoleafUnsupportedError,
)
from .scenes import (
    MAX_COLOURS,
    PARAMETERS,
    Direction,
    EffectType,
    PaletteColour,
    Scene,
    app_palette,
    decode_scene,
)

__all__ = [
    "MAX_COLOURS",
    "MAX_SCENES",
    "NANOLEAF_COMPANY_ID",
    "NO_SCENE_ID",
    "PARAMETERS",
    "PREVIEW_SCENE_ID",
    "PROTECTED_SCENE_IDS",
    "SUPPORTED_MODELS",
    "UNVERIFIED_MODELS",
    "USER_SCENE_IDS",
    "AdvertisementInfo",
    "CommunityClient",
    "CommunityScene",
    "Credentials",
    "DeviceInformation",
    "Direction",
    "EffectType",
    "LightState",
    "NanoleafAuthError",
    "NanoleafCloudError",
    "NanoleafConnectionError",
    "NanoleafDevice",
    "NanoleafError",
    "NanoleafProtocolError",
    "NanoleafStripFullError",
    "NanoleafTooManyPairingsError",
    "NanoleafUnsupportedError",
    "PaletteColour",
    "Scene",
    "app_palette",
    "builtin_scene_names",
    "decode_scene",
    "default_scenes",
    "parse_advertisement",
    "parse_device_information",
    "scene_from_animation",
]
