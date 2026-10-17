"""Constants for the Nanoleaf Essentials (Bluetooth) integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.util.hass_dict import HassKey

if TYPE_CHECKING:
    from .library import SceneStore

DOMAIN: Final = "nanoleaf_ble"
MANUFACTURER: Final = "Nanoleaf"
ISSUES_URL: Final = "https://github.com/jorgediez/ha-nanoleaf-ble/issues"

# Config entry data.
CONF_TOKEN: Final = "token"  # noqa: S105 - a config key, not a secret
CONF_CLIENT_ID: Final = "client_id"
CONF_SETUP_CODE: Final = "setup_code"
CONF_ADVANCED: Final = "advanced"
#: The advertisement fields the session depends on, kept so the strip can be
#: reached after a restart even before it has been heard advertising again.
CONF_MODEL: Final = "model"
CONF_FIRMWARE: Final = "firmware"
CONF_LOCAL_NAME: Final = "local_name"

# Options.
CONF_POLL_INTERVAL: Final = "poll_interval"
#: The strip pushes nothing (subscribing to its characteristics yields no
#: indications), so changes made in the phone app are only seen by polling. A
#: poll costs about 0.6 s of air time over the persistent connection.
DEFAULT_POLL_INTERVAL: Final = 15
MIN_POLL_INTERVAL: Final = 5
MAX_POLL_INTERVAL: Final = 300

#: The strip answers GATT error 133 to an immediate reconnect; about 2 s later it
#: works (verified through an ESPHome proxy).
RECONNECT_DELAY: Final = 2.0

#: Power-loss recovery is a setting, not state; read it every this many polls
#: (75 s at the default interval), so a change in the app shows up soon enough.
POWER_LOSS_POLL_EVERY: Final = 5

#: The scene library and per-strip scene names, shared by every config entry.
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
DATA_STORE: HassKey[SceneStore] = HassKey(DOMAIN)

#: Colour temperature range of Essentials lights that have one, in Kelvin.
MIN_KELVIN: Final = 2127
MAX_KELVIN: Final = 6535

# Actions.
SERVICE_CREATE_SCENE: Final = "create_scene"
SERVICE_PREVIEW_SCENE: Final = "preview_scene"
SERVICE_ADD_SCENE_TO_STRIP: Final = "add_scene_to_strip"
SERVICE_REMOVE_SCENE_FROM_STRIP: Final = "remove_scene_from_strip"
SERVICE_RENAME_SCENE: Final = "rename_scene"
SERVICE_DELETE_LIBRARY_SCENE: Final = "delete_library_scene"
SERVICE_SEARCH_COMMUNITY_SCENES: Final = "search_community_scenes"
SERVICE_PREVIEW_COMMUNITY_SCENE: Final = "preview_community_scene"
SERVICE_SAVE_COMMUNITY_SCENE: Final = "save_community_scene"
SERVICE_RESTORE_DEFAULT_SCENES: Final = "restore_default_scenes"

ATTR_SCENE: Final = "scene"
ATTR_NAME: Final = "name"
ATTR_NEW_NAME: Final = "new_name"
ATTR_EFFECT: Final = "effect"
ATTR_COLOURS: Final = "colours"
ATTR_ADD_TO_STRIP: Final = "add_to_strip"
ATTR_PLAY: Final = "play"
ATTR_KEY: Final = "key"
ATTR_QUERY: Final = "query"
ATTR_FEATURED: Final = "featured"
ATTR_SORT: Final = "sort"
