"""Connection and state for one Nanoleaf Essentials strip.

The coordinator keeps one authenticated Bluetooth connection open, so commands
take about 200 ms instead of the ~10 s a connect and login cost. The strip
pushes nothing (its characteristics send no indications, even when the phone
app changes it), so state is polled over that connection: on/off, brightness
and colour, the playing scene and the stored scene list. Commands update the
state optimistically; the next poll confirms it.

When the link drops, the coordinator waits a moment (an immediate reconnect is
answered with GATT error 133) and reconnects on the next refresh.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
import logging
import time
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.loader import async_get_integration

from .const import (
    CONF_CLIENT_ID,
    CONF_FIRMWARE,
    CONF_LOCAL_NAME,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    POWER_LOSS_POLL_EVERY,
    RECONNECT_DELAY,
)
from .lib import (
    MAX_SCENES,
    NANOLEAF_COMPANY_ID,
    NO_SCENE_ID,
    PREVIEW_SCENE_ID,
    PROTECTED_SCENE_IDS,
    USER_SCENE_IDS,
    AdvertisementInfo,
    CommunityClient,
    CommunityScene,
    Credentials,
    DeviceInformation,
    LightState,
    NanoleafAuthError,
    NanoleafCloudError,
    NanoleafConnectionError,
    NanoleafDevice,
    NanoleafError,
    NanoleafStripFullError,
    NanoleafUnsupportedError,
    Scene,
    builtin_scene_names,
    default_scenes,
    parse_advertisement,
    scene_from_animation,
)
from .library import (
    SOURCE_COMMUNITY,
    LibraryScene,
    SceneStore,
    StripSceneRecord,
    normalise_name,
)

_LOGGER = logging.getLogger(__name__)

#: Effect targets: a scene stored on the strip, or a library scene to preview.
TARGET_STRIP = "strip"
TARGET_LIBRARY = "library"


@dataclass(frozen=True, slots=True)
class NanoleafData:
    """Everything the entities need for one update cycle."""

    light: LightState
    #: 0x00 nothing playing, 0xFF a preview, otherwise a stored scene ID.
    current_scene: int = NO_SCENE_ID
    scene_ids: tuple[int, ...] = ()
    power_loss_recovery: bool | None = None

    @property
    def is_on(self) -> bool:
        """Return whether the light is on.

        A playing scene counts too: once, the strip read off while visibly lit
        after a scene. It did not recur, but this costs nothing.
        """
        return self.light.on or self.current_scene != NO_SCENE_ID

    @property
    def free_slots(self) -> int:
        """Return how many more scenes the strip can store."""
        return max(0, MAX_SCENES - len(self.scene_ids))


@dataclass(frozen=True, slots=True)
class EffectTarget:
    """What an effect name plays."""

    kind: str
    #: The strip scene ID, or the library scene ID.
    ref: int | str


type NanoleafConfigEntry = ConfigEntry[NanoleafCoordinator]


def advertisement_from_entry(data: dict[str, Any]) -> AdvertisementInfo:
    """Rebuild the advertisement fields the session needs from entry data."""
    firmware = tuple(int(part) for part in str(data[CONF_FIRMWARE]).split("."))
    return AdvertisementInfo(
        advertised_model=data[CONF_MODEL],
        firmware=(*firmware, 0, 0, 0)[:3],  # type: ignore[arg-type]
        hardware="",
    )


def credentials_from_entry(data: dict[str, Any]) -> Credentials:
    """Return the stored login credentials."""
    return Credentials(bytes.fromhex(data[CONF_TOKEN]), data.get(CONF_CLIENT_ID))


async def async_connect(
    hass: HomeAssistant,
    address: str,
    advertisement: AdvertisementInfo,
    local_name: str,
    disconnected_callback: Callable[[BleakClientWithServiceCache], None] | None = None,
) -> tuple[BleakClientWithServiceCache, NanoleafDevice]:
    """Connect through Home Assistant's Bluetooth stack, without logging in."""
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        raise NanoleafConnectionError("not in range of any Bluetooth adapter or proxy")
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            local_name,
            disconnected_callback=disconnected_callback,
            ble_device_callback=lambda: (
                bluetooth.async_ble_device_from_address(hass, address, connectable=True)
                or ble_device
            ),
        )
    except (BleakError, TimeoutError, OSError) as exc:
        raise NanoleafConnectionError(str(exc) or type(exc).__name__) from exc
    return client, NanoleafDevice(client, advertisement, name=local_name)


class NanoleafCoordinator(DataUpdateCoordinator[NanoleafData]):
    """Keeps the connection to one strip and polls its state."""

    config_entry: NanoleafConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: NanoleafConfigEntry, store: SceneStore
    ) -> None:
        """Initialise from the config entry."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.unique_id}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ),
        )
        self.store = store
        self.address: str = entry.unique_id or ""
        self.advertisement = advertisement_from_entry(dict(entry.data))
        self.local_name: str = entry.data.get(CONF_LOCAL_NAME) or self.address
        self.rssi: int | None = None

        self._client: BleakClientWithServiceCache | None = None
        self.device: NanoleafDevice | None = None
        self._connect_lock = asyncio.Lock()
        self._disconnected_at = 0.0
        self._reconnect_timer: CALLBACK_TYPE | None = None
        self._closing = False

        self.device_information: DeviceInformation | None = None
        self.supports_colour_temperature = False
        self.supports_power_loss_recovery = True
        self.colour_temperature_mode = False
        self._polls = 0

        # Scene bookkeeping for this connection.
        self._checked_scenes: set[int] = set()
        self._preview_name: str | None = None

        # Community search state, shared by the text, select and button entities.
        self.community_query = ""
        self.community_results: list[CommunityScene] = []
        self.community_selected: str | None = None
        self._community_cache: dict[str, tuple[Scene, list[str]]] = {}
        #: The library or strip scene the Scene select points at.
        self.scene_selected: str | None = None

    # ------------------------------------------------------------------ properties

    @property
    def model(self) -> str:
        """Return the model code (NL62, NL55, ...)."""
        return self.advertisement.model

    @property
    def ltpdu_v3(self) -> bool:
        """Return whether the strip runs firmware 5 (experimental support)."""
        return self.advertisement.ltpdu_v3

    @property
    def supports_scenes(self) -> bool:
        """Return whether scene features work on this firmware."""
        return not self.ltpdu_v3

    @property
    def protocol(self) -> str:
        """Return the protocol in use."""
        if self.device is not None:
            return self.device.protocol
        if self.ltpdu_v3:
            return "LTPDUv3"
        return "legacy v2" if self.advertisement.v2 else "legacy"

    @property
    def connected(self) -> bool:
        """Return whether an authenticated session is open."""
        return (
            self.device is not None
            and self.device.authenticated
            and not self.device.broken
            and self._client is not None
            and self._client.is_connected
        )

    # ------------------------------------------------------------------ connection

    async def async_start(self) -> None:
        """Connect and log in for setup; raise what Home Assistant expects."""
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._async_on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        if (
            info := bluetooth.async_last_service_info(
                self.hass, self.address, connectable=True
            )
        ) is not None:
            self._async_on_advertisement(info, bluetooth.BluetoothChange.ADVERTISEMENT)
        try:
            await self._async_ensure_connected()
        except NanoleafAuthError as exc:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"name": self.config_entry.title},
            ) from exc
        except NanoleafError as exc:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={
                    "address": self.address,
                    "error": str(exc),
                },
            ) from exc

    @callback
    def _async_on_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Track RSSI, and a firmware update that changes the protocol."""
        self.rssi = service_info.rssi
        data = service_info.manufacturer_data.get(NANOLEAF_COMPANY_ID)
        if data is None:
            return
        try:
            advertisement = parse_advertisement(data)
        except ValueError:
            return
        if advertisement.firmware_version != self.config_entry.data.get(CONF_FIRMWARE):
            _LOGGER.info(
                "%s now reports firmware %s",
                self.config_entry.title,
                advertisement.firmware_version,
            )
            # The entry's update listener reloads it, which matters when the
            # update reaches firmware 5: another protocol, other entities.
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_FIRMWARE: advertisement.firmware_version,
                },
            )
        self.advertisement = advertisement

    @callback
    def _on_disconnected(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        self._disconnected_at = time.monotonic()
        if self._closing:
            return
        _LOGGER.debug("%s: Bluetooth link lost", self.config_entry.title)
        # Reconnect soon rather than at the next poll, so commands stay quick.
        self._cancel_reconnect_timer()
        self._reconnect_timer = async_call_later(
            self.hass, RECONNECT_DELAY, self._async_reconnect_now
        )

    @callback
    def _async_reconnect_now(self, _now: Any) -> None:
        self._reconnect_timer = None
        if not self._closing:
            self.hass.async_create_task(self.async_request_refresh())

    def _cancel_reconnect_timer(self) -> None:
        if self._reconnect_timer is not None:
            self._reconnect_timer()
            self._reconnect_timer = None

    async def _async_ensure_connected(self) -> NanoleafDevice:
        """Return a logged-in session, connecting first if needed."""
        async with self._connect_lock:
            if self.connected and self.device is not None:
                return self.device
            await self._async_drop_connection()
            wait = RECONNECT_DELAY - (time.monotonic() - self._disconnected_at)
            if wait > 0:
                await asyncio.sleep(wait)
            client, device = await async_connect(
                self.hass,
                self.address,
                self.advertisement,
                self.local_name,
                self._on_disconnected,
            )
            self._client = client
            try:
                await device.login(credentials_from_entry(dict(self.config_entry.data)))
            except NanoleafError:
                await self._async_drop_connection()
                raise
            self.device = device
            self._checked_scenes.clear()
            _LOGGER.debug(
                "%s: connected (%s)", self.config_entry.title, device.protocol
            )
            return device

    async def _async_drop_connection(self) -> None:
        client, self._client, self.device = self._client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, OSError) as exc:
                _LOGGER.debug("disconnect failed: %s", exc)
            self._disconnected_at = time.monotonic()

    async def async_disconnect(self) -> None:
        """Close the session and the link (unload, Home Assistant stopping)."""
        self._closing = True
        self._cancel_reconnect_timer()
        if self.device is not None and self.connected:
            try:
                await self.device.end_session()
            except NanoleafError as exc:
                _LOGGER.debug("ending the session failed: %s", exc)
        await self._async_drop_connection()

    # ------------------------------------------------------------------ polling

    async def _async_setup(self) -> None:
        """Read what does not change: device information and capabilities."""
        try:
            device = await self._async_ensure_connected()
            self.device_information = await device.device_information()
            self.supports_colour_temperature = (
                await device.supports_colour_temperature()
            )
        except NanoleafAuthError as exc:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"name": self.config_entry.title},
            ) from exc
        except NanoleafError as exc:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"address": self.address, "error": str(exc)},
            ) from exc

    async def _async_update_data(self) -> NanoleafData:
        try:
            device = await self._async_ensure_connected()
            return await self._async_poll(device)
        except NanoleafAuthError as exc:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"name": self.config_entry.title},
            ) from exc
        except NanoleafError as exc:
            if isinstance(exc, NanoleafConnectionError):
                await self._async_drop_connection()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"address": self.address, "error": str(exc)},
            ) from exc

    async def _async_poll(self, device: NanoleafDevice) -> NanoleafData:
        light = await device.get_state(
            colour_temperature=self.supports_colour_temperature
        )
        previous = self.data
        if not self.supports_scenes:
            return NanoleafData(light=light)

        current = await device.current_scene()
        scene_ids = tuple(await device.list_scenes())
        power_loss = previous.power_loss_recovery if previous else None
        if self.supports_power_loss_recovery and (
            power_loss is None or self._polls % POWER_LOSS_POLL_EVERY == 0
        ):
            try:
                power_loss = await device.get_power_loss_recovery()
            except NanoleafUnsupportedError:
                # Firmware below 1.2.13 has no power-loss recovery setting.
                self.supports_power_loss_recovery = False
                power_loss = None
        self._polls += 1
        await self._async_check_scenes(device, scene_ids)
        if current != PREVIEW_SCENE_ID:
            self._preview_name = None
        return NanoleafData(
            light=light,
            current_scene=current,
            scene_ids=scene_ids,
            power_loss_recovery=power_loss,
        )

    async def _async_check_scenes(
        self, device: NanoleafDevice, scene_ids: tuple[int, ...]
    ) -> None:
        """Keep the strip's scene names right as scenes come and go.

        A scene seen for the first time on this connection is read once and
        matched by content: against what the integration stored under that ID,
        then against the library. Built-in IDs need no read; the app's tables
        name them.
        """
        builtins = builtin_scene_names(self.model)
        records = self.store.strip_scenes(self.address)
        for scene_id in list(records):
            if scene_id not in scene_ids:
                self.store.async_forget_strip_scene(self.address, scene_id)
        self._checked_scenes &= set(scene_ids)
        for scene_id in scene_ids:
            if scene_id in self._checked_scenes or scene_id in builtins:
                continue
            fingerprint = (await device.get_scene(scene_id)).fingerprint
            self._checked_scenes.add(scene_id)
            record = records.get(scene_id)
            if record is not None and record.fingerprint == fingerprint:
                continue
            if (match := self.store.find_by_fingerprint(fingerprint)) is not None:
                self.store.async_set_strip_scene(
                    self.address,
                    scene_id,
                    StripSceneRecord(match.name, fingerprint, match.id),
                )
            elif record is not None:
                # Something else (the phone app) stored another scene here.
                self.store.async_forget_strip_scene(self.address, scene_id)

    # ------------------------------------------------------------------ scene names

    def scene_name(self, scene_id: int) -> str:
        """Return the name of a scene stored on the strip."""
        if (name := builtin_scene_names(self.model).get(scene_id)) is not None:
            return name
        if (record := self.store.strip_scenes(self.address).get(scene_id)) is not None:
            return record.name
        return f"Scene {scene_id}"

    @property
    def effects(self) -> dict[str, EffectTarget]:
        """Return every playable scene by unique name.

        Scenes on the strip come first, then library scenes that are not on it;
        those play as previews, taking no slot.
        """
        if not self.supports_scenes or self.data is None:
            return {}
        effects: dict[str, EffectTarget] = {}
        taken: set[str] = set()

        def add(name: str, target: EffectTarget) -> None:
            unique, number = name, 2
            while normalise_name(unique) in taken:
                unique, number = f"{name} ({number})", number + 1
            taken.add(normalise_name(unique))
            effects[unique] = target

        records = self.store.strip_scenes(self.address)
        on_strip: set[str] = set()
        for scene_id in sorted(
            self.data.scene_ids, key=lambda sid: normalise_name(self.scene_name(sid))
        ):
            add(self.scene_name(scene_id), EffectTarget(TARGET_STRIP, scene_id))
            if (record := records.get(scene_id)) and record.library_id:
                on_strip.add(record.library_id)
        for item in self.store.library:
            if item.id not in on_strip:
                add(item.name, EffectTarget(TARGET_LIBRARY, item.id))
        return effects

    @property
    def current_effect(self) -> str | None:
        """Return the name of what plays, or None for a static colour or off."""
        if self.data is None or not self.supports_scenes:
            return None
        current = self.data.current_scene
        if current == NO_SCENE_ID:
            return None
        if current == PREVIEW_SCENE_ID:
            # Only a preview started here has a known name.
            return self._preview_name
        for name, target in self.effects.items():
            if target.kind == TARGET_STRIP and target.ref == current:
                return name
        return self.scene_name(current)

    def strip_scene_id(self, name: str) -> int:
        """Return the strip scene an effect name refers to."""
        target = self.effects.get(name) or self._find_effect(name)
        if target is None or target.kind != TARGET_STRIP:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_on_strip",
                translation_placeholders={"scene": name},
            )
        return int(target.ref)

    def _find_effect(self, name: str) -> EffectTarget | None:
        wanted = normalise_name(name)
        return next(
            (t for n, t in self.effects.items() if normalise_name(n) == wanted), None
        )

    # ------------------------------------------------------------------ commands

    async def async_run[T](self, action: Callable[[NanoleafDevice], Awaitable[T]]) -> T:
        """Run a command on the strip, reconnecting once if the link dropped.

        Every failure becomes a translated Home Assistant error.
        """
        for attempt in (1, 2):
            try:
                device = await self._async_ensure_connected()
                return await action(device)
            except NanoleafConnectionError as exc:
                await self._async_drop_connection()
                if attempt == 2:
                    raise self._error("command_failed", exc) from exc
                _LOGGER.debug("command failed (%s); reconnecting to retry", exc)
            except NanoleafAuthError as exc:
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                    translation_placeholders={"name": self.config_entry.title},
                ) from exc
            except NanoleafStripFullError as exc:
                raise self._strip_full() from exc
            except NanoleafUnsupportedError as exc:
                raise self._error("not_supported", exc) from exc
            except NanoleafError as exc:
                raise self._error("command_failed", exc) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _error(self, key: str, exc: Exception) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={
                "name": self.config_entry.title,
                "error": str(exc),
            },
        )

    def community_error(self, exc: Exception) -> HomeAssistantError:
        """Return the error for a failed community library request."""
        return self._error("community_failed", exc)

    def _strip_full(self) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="strip_full",
            translation_placeholders={
                "name": self.config_entry.title,
                "max": str(MAX_SCENES),
            },
        )

    def _require_scenes(self) -> None:
        if not self.supports_scenes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scenes_unsupported",
                translation_placeholders={"name": self.config_entry.title},
            )

    @callback
    def _async_set_data(self, **changes: Any) -> None:
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, **changes))

    async def async_set_light(
        self,
        *,
        on: bool | None = None,
        brightness: int | None = None,
        hs_colour: tuple[float, float] | None = None,
        colour_temperature: int | None = None,
    ) -> None:
        """Write on/off, brightness (0-100 %) and a colour, in one request."""
        hue = saturation = None
        if hs_colour is not None:
            hue, saturation = round(hs_colour[0]) % 361, round(hs_colour[1])
        await self.async_run(
            lambda device: device.set_light(
                on=on,
                brightness=brightness,
                hue=hue,
                saturation=saturation,
                colour_temperature=colour_temperature,
            )
        )
        if self.data is None:
            return
        light = self.data.light
        light = replace(
            light,
            on=light.on if on is None else on,
            brightness=light.brightness if brightness is None else brightness,
            hue=light.hue if hue is None else hue,
            saturation=light.saturation if saturation is None else saturation,
            colour_temperature=colour_temperature or light.colour_temperature,
        )
        if hs_colour is not None:
            self.colour_temperature_mode = False
        if colour_temperature is not None:
            self.colour_temperature_mode = True
        changes: dict[str, Any] = {"light": light}
        if on is False:
            changes["current_scene"] = NO_SCENE_ID
        elif hs_colour is not None or colour_temperature is not None:
            # A colour replaces whatever scene was playing.
            changes["current_scene"] = NO_SCENE_ID
            self._preview_name = None
        self._async_set_data(**changes)

    async def async_play_effect(self, name: str) -> None:
        """Play a scene by its effect name."""
        self._require_scenes()
        target = self.effects.get(name) or self._find_effect(name)
        if target is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_found",
                translation_placeholders={"scene": name},
            )
        if target.kind == TARGET_STRIP:
            scene_id = int(target.ref)
            await self.async_run(lambda device: device.activate_scene(scene_id))
            self._preview_name = None
            self._async_after_play(scene_id)
            return
        item = self.store.get(str(target.ref))
        if item is None:  # pragma: no cover - effects come from the store
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_found",
                translation_placeholders={"scene": name},
            )
        await self.async_preview(item.scene, item.name)

    async def async_preview(self, scene: Scene, name: str | None) -> None:
        """Play a scene without storing it."""
        self._require_scenes()
        await self.async_run(lambda device: device.preview_scene(scene))
        self._preview_name = name
        self._async_after_play(PREVIEW_SCENE_ID)

    @callback
    def _async_after_play(self, current: int) -> None:
        # Playing a scene switches the light on.
        if self.data is not None:
            self._async_set_data(
                current_scene=current, light=replace(self.data.light, on=True)
            )

    async def async_set_power_loss_recovery(self, enabled: bool) -> None:
        """Choose whether the strip restores its state after a power cut."""
        await self.async_run(lambda device: device.set_power_loss_recovery(enabled))
        self._async_set_data(power_loss_recovery=enabled)

    async def async_identify(self) -> None:
        """Blink the strip."""
        await self.async_run(lambda device: device.identify())

    # ------------------------------------------------------------------ strip scenes

    def library_scene(self, name: str) -> LibraryScene | None:
        """Return the library scene an effect or library name refers to."""
        target = self.effects.get(name) or self._find_effect(name)
        if target is not None and target.kind == TARGET_LIBRARY:
            return self.store.get(str(target.ref))
        if target is not None and target.kind == TARGET_STRIP:
            record = self.store.strip_scenes(self.address).get(int(target.ref))
            if record is not None and record.library_id:
                return self.store.get(record.library_id)
        return self.store.find(name)

    def require_library_scene(self, name: str) -> LibraryScene:
        """Return the library scene with this name, or raise."""
        if (item := self.library_scene(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_in_library",
                translation_placeholders={"scene": name},
            )
        return item

    def library_scene_on_strip(self, library_id: str) -> int | None:
        """Return the ID a library scene is stored under on this strip, if any."""
        if self.data is None:
            return None
        for scene_id, record in self.store.strip_scenes(self.address).items():
            if record.library_id == library_id and scene_id in self.data.scene_ids:
                return scene_id
        return None

    async def async_add_to_strip(self, item: LibraryScene) -> int:
        """Store a library scene on the strip; return its ID there."""
        self._require_scenes()
        if self.data is None:  # pragma: no cover - set before entities exist
            raise self._error("command_failed", RuntimeError("no data"))
        if self.library_scene_on_strip(item.id) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_already_on_strip",
                translation_placeholders={"scene": item.name},
            )
        scene_id = self._free_scene_id()
        await self.async_run(lambda device: device.add_scene(scene_id, item.scene))
        self.store.async_set_strip_scene(
            self.address,
            scene_id,
            StripSceneRecord(item.name, item.scene.fingerprint, item.id),
        )
        self._checked_scenes.add(scene_id)
        self._async_set_data(scene_ids=(*self.data.scene_ids, scene_id))
        return scene_id

    def _free_scene_id(self) -> int:
        if self.data is None or self.data.free_slots == 0:
            raise self._strip_full()
        used = set(self.data.scene_ids)
        for scene_id in USER_SCENE_IDS:
            if scene_id not in used:
                return scene_id
        raise self._strip_full()  # pragma: no cover - 16 IDs, 12 slots

    async def async_remove_from_strip(self, name: str) -> None:
        """Delete a scene from the strip by name. Factory scenes are refused."""
        self._require_scenes()
        scene_id = self.strip_scene_id(name)
        if scene_id in PROTECTED_SCENE_IDS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="builtin_protected",
                translation_placeholders={"scene": name},
            )
        await self.async_run(lambda device: device.delete_scene(scene_id))
        self.store.async_forget_strip_scene(self.address, scene_id)
        self._checked_scenes.discard(scene_id)
        if self.data is not None:
            self._async_set_data(
                scene_ids=tuple(s for s in self.data.scene_ids if s != scene_id),
                current_scene=NO_SCENE_ID
                if self.data.current_scene == scene_id
                else self.data.current_scene,
            )

    async def async_restore_default_scenes(
        self, *, automatic: bool = False
    ) -> list[int]:
        """Write the app's default scenes that are missing, as space allows.

        Nothing is ever deleted to make room. Return the IDs written. A manual
        restore that cannot fit everything raises; the automatic one after
        pairing raises a repair issue instead.
        """
        self._require_scenes()
        defaults = default_scenes(self.model)
        if not defaults:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_default_scenes",
                translation_placeholders={"name": self.config_entry.title},
            )
        if self.data is None:  # pragma: no cover - set before entities exist
            return []
        missing = [sid for sid in sorted(defaults) if sid not in self.data.scene_ids]
        fitting = missing[: self.data.free_slots]
        written: list[int] = []
        for scene_id in fitting:
            scene = defaults[scene_id]
            try:
                await self.async_run(
                    lambda device, sid=scene_id, s=scene: device.add_scene(sid, s)
                )
            except HomeAssistantError:
                if written:
                    self._async_set_data(scene_ids=(*self.data.scene_ids, *written))
                raise
            written.append(scene_id)
        if written:
            self._async_set_data(scene_ids=(*self.data.scene_ids, *written))
        left_out = [self.scene_name(sid) for sid in missing if sid not in written]
        issue_id = f"default_scenes_incomplete_{self.config_entry.entry_id}"
        if left_out and automatic:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="default_scenes_incomplete",
                translation_placeholders={
                    "name": self.config_entry.title,
                    "scenes": ", ".join(left_out),
                },
            )
        elif left_out:
            raise self._strip_full()
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        return written

    async def async_apply_default_scenes(self) -> None:
        """Write the default scenes once after pairing, as the app does per phone."""
        if (
            not self.supports_scenes
            or not default_scenes(self.model)
            or self.store.defaults_applied(self.address)
        ):
            return
        try:
            written = await self.async_restore_default_scenes(automatic=True)
        except HomeAssistantError as exc:
            # Not marked as done: it is tried again at the next start.
            _LOGGER.warning(
                "Could not write the default scenes to %s: %s",
                self.config_entry.title,
                exc,
            )
            return
        if written:
            _LOGGER.info(
                "Wrote %s default scene(s) to %s",
                len(written),
                self.config_entry.title,
            )
        self.store.async_set_defaults_applied(self.address)

    # ------------------------------------------------------------------ community

    async def async_community_client(self) -> CommunityClient:
        """Return a client for the community scene library."""
        integration = await async_get_integration(self.hass, DOMAIN)
        return CommunityClient(
            async_get_clientsession(self.hass),
            f"ha-nanoleaf-ble/{integration.version}",
        )

    async def async_community_search(self, query: str) -> None:
        """Search the community library; the results feed the select entity."""
        client = await self.async_community_client()
        try:
            results = await client.search(query.strip())
        except NanoleafCloudError as exc:
            raise self.community_error(exc) from exc
        self.community_query = query
        self.community_results = results
        self.community_selected = results[0].key if results else None
        self.async_update_listeners()

    async def async_community_scene(self, key: str) -> tuple[Scene, list[str]]:
        """Download and convert a community scene (cached for the session)."""
        if key not in self._community_cache:
            client = await self.async_community_client()
            try:
                animation = await client.download(key)
                self._community_cache[key] = scene_from_animation(animation)
            except NanoleafCloudError as exc:
                raise self.community_error(exc) from exc
            for warning in self._community_cache[key][1]:
                _LOGGER.info("Community scene %s: %s", key, warning)
        return self._community_cache[key]

    def community_result(self, key: str | None) -> CommunityScene | None:
        """Return a search result by key."""
        return next((r for r in self.community_results if r.key == key), None)

    async def async_save_community_scene(
        self, key: str, name: str | None = None, creator: str | None = None
    ) -> LibraryScene:
        """Save a community scene to the library (or return it if already there)."""
        scene, _warnings = await self.async_community_scene(key)
        for item in self.store.library:
            if item.community_key == key:
                return item
        result = self.community_result(key)
        name = name or (result.name if result else f"Community {key[:8]}")
        if self.store.find(name) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_exists",
                translation_placeholders={"scene": name},
            )
        return self.store.async_add(
            name,
            scene,
            source=SOURCE_COMMUNITY,
            community_key=key,
            creator=creator or (result.creator if result else None),
        )
