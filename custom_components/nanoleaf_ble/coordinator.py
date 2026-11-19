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
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .community import CommunityBrowser
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
    REFRESH_SOON,
)
from .lib import (
    MAX_SCENES,
    NANOLEAF_COMPANY_ID,
    NO_SCENE_ID,
    PREVIEW_SCENE_ID,
    PROTECTED_SCENE_IDS,
    USER_SCENE_IDS,
    AdvertisementInfo,
    Credentials,
    DeviceInformation,
    LightState,
    NanoleafAuthError,
    NanoleafConnectionError,
    NanoleafDevice,
    NanoleafError,
    NanoleafStripFullError,
    NanoleafUnsupportedError,
    Scene,
    default_scenes,
    parse_advertisement,
)
from .library import LibraryScene, SceneStore
from .scenes import TARGET_STRIP, StripScenes

_LOGGER = logging.getLogger(__name__)


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


type NanoleafConfigEntry = ConfigEntry[NanoleafCoordinator]


def unique_id_for(advertisement: AdvertisementInfo, address: str) -> str:
    """Return the unique id of a strip: its device ID, else its address.

    The device ID (EUI-64) survives a factory reset; the Bluetooth address
    does not (verified on the NL62), so it is only the fallback for an
    advertisement too short to carry the ID.
    """
    return advertisement.device_id or address


def advertisement_from_entry(data: dict[str, Any]) -> AdvertisementInfo:
    """Rebuild the advertisement fields the session needs from entry data.

    A stored firmware that is not a version (a damaged or hand-edited entry)
    reads as 0.0.0, the legacy protocol, rather than failing setup. The strip's
    advertisement then stores the real one: at setup if it has been heard
    already, or when it is, reloading the entry.
    """
    try:
        firmware = tuple(int(part) for part in str(data[CONF_FIRMWARE]).split("."))
    except (KeyError, ValueError):
        _LOGGER.warning(
            "Stored firmware %r is not a version; using 0.0.0 until the strip "
            "advertises its own",
            data.get(CONF_FIRMWARE),
        )
        firmware = ()
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


async def async_probe(
    hass: HomeAssistant,
    address: str,
    advertisement: AdvertisementInfo,
    local_name: str,
    secret: str | Credentials,
) -> Credentials:
    """Pair with a setup code, or log in with credentials; return the credentials.

    The device information is read too, to show the session really works.
    Raise NanoleafError (or a subclass) when any of it fails.
    """
    client, device = await async_connect(hass, address, advertisement, local_name)
    try:
        if isinstance(secret, Credentials):
            credentials = secret
            await device.login(credentials)
        else:
            credentials = await device.pair(secret)
        await device.device_information()
        return credentials
    finally:
        try:
            await client.disconnect()
        except (BleakError, TimeoutError, OSError) as exc:
            _LOGGER.debug("disconnect after probe failed: %s", exc)


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
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ),
        )
        self.store = store
        self.address: str = entry.data[CONF_ADDRESS]
        self.advertisement = advertisement_from_entry(dict(entry.data))
        self.local_name: str = entry.data.get(CONF_LOCAL_NAME) or self.address
        self.rssi: int | None = None
        #: The last manufacturer data the strip advertised, for diagnostics.
        self.manufacturer_data: bytes | None = None

        self._client: BleakClientWithServiceCache | None = None
        self.device: NanoleafDevice | None = None
        self._connect_lock = asyncio.Lock()
        self._disconnected_at = 0.0
        self._reconnect_timer: CALLBACK_TYPE | None = None
        self._refresh_timer: CALLBACK_TYPE | None = None
        self._closing = False

        self.device_information: DeviceInformation | None = None
        self.supports_colour_temperature = False
        self.supports_power_loss_recovery = True
        self.colour_temperature_mode = False
        self._polls = 0

        #: Scene names, and the scene each effect name plays.
        self.scenes = StripScenes(store, self)

        #: Community search state, shared by the text, select and button entities.
        self.community = CommunityBrowser(
            hass,
            store,
            lambda exc: self._error("community_failed", exc),
            self.async_update_listeners,
        )

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
        self.manufacturer_data = bytes(data)
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
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_FIRMWARE: advertisement.firmware_version,
                },
            )
            # Reload, which matters when the update reaches firmware 5: another
            # protocol, other entities. During setup there is nothing to reload.
            if self.config_entry.state is ConfigEntryState.LOADED:
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
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
        # The connection sensor shows it now, not at the next poll.
        self.async_update_listeners()
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

    @callback
    def _async_refresh_soon(self) -> None:
        """Poll shortly, to learn what the strip chose to show by itself.

        Each call restarts the wait, so a burst of commands polls once.
        """
        self._cancel_refresh_timer()
        self._refresh_timer = async_call_later(
            self.hass, REFRESH_SOON, self._async_refresh_now
        )

    @callback
    def _async_refresh_now(self, _now: Any) -> None:
        self._refresh_timer = None
        if not self._closing:
            self.hass.async_create_task(self.async_refresh())

    def _cancel_refresh_timer(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer()
            self._refresh_timer = None

    async def _async_ensure_connected(self) -> NanoleafDevice:
        """Return a logged-in session, connecting first if needed."""
        async with self._connect_lock:
            if self.connected and self.device is not None:
                return self.device
            await self._async_drop_connection()
            # A refusal is checked once more on a fresh connection before it
            # is reported: asking to pair again stops the entry, so a hiccup
            # mistaken for a refusal would cost far more than one reconnect.
            for attempt in (1, 2):
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
                    await device.login(
                        credentials_from_entry(dict(self.config_entry.data))
                    )
                except NanoleafAuthError as exc:
                    await self._async_drop_connection()
                    if attempt == 2:
                        raise
                    _LOGGER.debug(
                        "%s: login refused (%s); retrying on a fresh connection",
                        self.config_entry.title,
                        exc,
                    )
                    continue
                except NanoleafError:
                    await self._async_drop_connection()
                    raise
                break
            self.device = device
            self.scenes.reset()
            self.async_update_listeners()
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
            if not self._closing:
                self.async_update_listeners()

    async def async_probe(self, secret: str | Credentials) -> Credentials:
        """Check new credentials (reauth, reconfigure) on the strip's only link.

        A second connection beside this one breaks both: the proxy answers
        GATT error 131 and drops the link. So this link closes for the check,
        and reconnects after it (a successful check also reloads the entry).
        """
        async with self._connect_lock:
            self._cancel_reconnect_timer()
            await self._async_drop_connection()
            wait = RECONNECT_DELAY - (time.monotonic() - self._disconnected_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await async_probe(
                    self.hass,
                    self.address,
                    self.advertisement,
                    self.local_name,
                    secret,
                )
            finally:
                self._disconnected_at = time.monotonic()
                if not self._closing:
                    self._reconnect_timer = async_call_later(
                        self.hass, RECONNECT_DELAY, self._async_reconnect_now
                    )

    async def async_disconnect(self) -> None:
        """Close the session and the link (unload, Home Assistant stopping)."""
        self._closing = True
        self._cancel_reconnect_timer()
        self._cancel_refresh_timer()
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
        await self.scenes.async_check(device, scene_ids)
        if current != PREVIEW_SCENE_ID:
            self.scenes.preview_name = None
        return NanoleafData(
            light=light,
            current_scene=current,
            scene_ids=scene_ids,
            power_loss_recovery=power_loss,
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
        if on and not light.on and hs_colour is None and colour_temperature is None:
            self._async_refresh_soon()
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
            self.scenes.preview_name = None
        self._async_set_data(**changes)

    async def async_play_effect(self, name: str) -> None:
        """Play a scene by its effect name."""
        self._require_scenes()
        target = self.scenes.find(name)
        if target is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_not_found",
                translation_placeholders={"scene": name},
            )
        if target.kind == TARGET_STRIP:
            scene_id = int(target.ref)
            await self.async_run(lambda device: device.activate_scene(scene_id))
            self.scenes.preview_name = None
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
        self.scenes.preview_name = name
        self._async_after_play(PREVIEW_SCENE_ID)

    @callback
    def _async_after_play(self, current: int) -> None:
        # Playing a scene switches the light on.
        if self.data is not None:
            self._async_set_data(
                current_scene=current, light=replace(self.data.light, on=True)
            )

    async def async_set_power_loss_recovery(self, enabled: bool) -> None:
        """Choose whether the strip restores its on/off state after a power cut."""
        await self.async_run(lambda device: device.set_power_loss_recovery(enabled))
        self._async_set_data(power_loss_recovery=enabled)

    async def async_identify(self) -> None:
        """Blink the strip."""
        await self.async_run(lambda device: device.identify())

    # ------------------------------------------------------------------ strip scenes

    async def async_add_to_strip(self, item: LibraryScene) -> int:
        """Store a library scene on the strip; return its ID there."""
        self._require_scenes()
        if self.data is None:  # pragma: no cover - set before entities exist
            raise self._error("command_failed", RuntimeError("no data"))
        if self.scenes.on_strip(item.id) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scene_already_on_strip",
                translation_placeholders={"scene": item.name},
            )
        scene_id = self._free_scene_id()
        await self.async_run(lambda device: device.add_scene(scene_id, item.scene))
        self.scenes.stored(scene_id, item)
        self._async_set_data(scene_ids=(*self.data.scene_ids, scene_id))
        # The strip plays a scene as soon as it is stored.
        self._async_refresh_soon()
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
        scene_id = self.scenes.strip_scene_id(name)
        if scene_id in PROTECTED_SCENE_IDS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="builtin_protected",
                translation_placeholders={"scene": name},
            )
        await self.async_run(lambda device: device.delete_scene(scene_id))
        self.scenes.removed(scene_id)
        if self.data is not None:
            self._async_set_data(
                scene_ids=tuple(s for s in self.data.scene_ids if s != scene_id),
                current_scene=NO_SCENE_ID
                if self.data.current_scene == scene_id
                else self.data.current_scene,
            )

    async def async_restore_default_scenes(self) -> list[int]:
        """Write the app's default scenes that are missing, as space allows.

        Only on request, from the button or the action: nothing is written to a
        strip unasked. Nothing is ever deleted to make room. Return the IDs
        written; raise if not everything fits.
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
            # The strip plays each scene it stores; one poll shows the last.
            self._async_refresh_soon()
        if len(written) < len(missing):
            raise self._strip_full()
        return written
