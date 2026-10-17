"""High-level control of one Nanoleaf Essentials device.

The device never connects on its own: the caller passes an already-connected
BleakClient (from Home Assistant's Bluetooth stack, plain bleak or an ESPHome
proxy) and owns its lifetime. This object owns the session lock, since both
protocols share one cipher state between the directions and requests must
never overlap.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from bleak.exc import BleakError
from cryptography.exceptions import InvalidTag

from .advertisement import AdvertisementInfo
from .commands import Method, Status, u16
from .const import NO_SCENE_ID, PROTECTED_SCENE_IDS
from .exceptions import (
    NanoleafAuthError,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafProtocolError,
    NanoleafStripFullError,
    NanoleafUnsupportedError,
)
from .legacy import LegacySession
from .ltpdu_v3 import V3Session
from .scenes import Scene, decode_scene

_LOGGER = logging.getLogger(__name__)

#: Ordinary requests answer in about 200 ms.
DEFAULT_TIMEOUT = 15.0
#: The strip once took 32 s to acknowledge a scene preview (the scene itself
#: started at once), so Command Interface requests get longer.
SCENE_TIMEOUT = 40.0

#: Scene ID written into a preview's metadata, as the app does (id & 0x0F of 1).
#: The device reports a running preview as 0xFF whatever this is.
PREVIEW_ID = 0x01

# Command Interface families (POST to "ci").
_CI_SCENE = 0x07
_CI_POWER_LOSS = 0x09
_SCENE_PREVIEW = 0x01
_SCENE_ADD = 0x02
_SCENE_LIST = 0x03
_SCENE_GET = 0x04
_SCENE_DELETE = 0x05
_SCENE_ACTIVATE = 0x06
_SCENE_CURRENT = 0x07
_POWER_LOSS_SET = 0x01
_POWER_LOSS_GET = 0x02
#: A Command Interface reply starts <family | 0x80> <command> <length16>.
_CI_REPLY_HEADER = 4

#: Length of the fixed-layout device information record (legacy firmware).
_DI_FIXED_LENGTH = 37
_DI_TLV_NAMES = {
    0x03: "hardware_version",
    0x04: "serial_number",
    0x06: "eui",
    0x0B: "model_number",
    0x1B: "partner_model",
    0x1C: "sub_model",
    0xFE: "firmware_version",
}


@dataclass(frozen=True, slots=True)
class Credentials:
    """What pairing returns and every later login needs."""

    token: bytes
    #: Assigned by LTPDUv3 devices only.
    client_id: int | None = None


@dataclass(frozen=True, slots=True)
class LightState:
    """The basic light endpoints."""

    on: bool
    brightness: int
    hue: int
    saturation: int
    colour_temperature: int | None = None


@dataclass(frozen=True, slots=True)
class DeviceInformation:
    """What the device says about itself (endpoint "di")."""

    hardware_version: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    eui: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _text(data: bytes) -> str:
    return data.replace(b"\x00", b"").decode("ascii", errors="replace").strip()


def parse_device_information(value: bytes) -> DeviceInformation:
    """Decode the "di" record: a fixed 37-byte layout or TLVs."""
    if len(value) == _DI_FIXED_LENGTH:
        hardware = _text(value[0:10])
        return DeviceInformation(
            hardware_version="1.0.4" if hardware == "0.3.6" else hardware,
            firmware_version=_text(value[10:18]),
            serial_number=_text(value[18:29]),
            eui=value[29:37].hex(),
        )
    fields: dict[str, str] = {}
    offset = 0
    while offset + 2 <= len(value):
        tlv_type, length = value[offset], value[offset + 1]
        data = value[offset + 2 : offset + 2 + length]
        offset += 2 + length
        name = _DI_TLV_NAMES.get(tlv_type, f"type_{tlv_type:02x}")
        printable = all(32 <= byte < 127 for byte in data)
        fields[name] = data.decode("ascii") if printable else data.hex()
    return DeviceInformation(
        hardware_version=fields.pop("hardware_version", None),
        firmware_version=fields.pop("firmware_version", None),
        serial_number=fields.pop("serial_number", None),
        eui=fields.pop("eui", None),
        extra=fields,
    )


class NanoleafDevice:
    """One authenticated session with a Nanoleaf Essentials device."""

    def __init__(
        self,
        client: Any,
        advertisement: AdvertisementInfo,
        *,
        name: str,
        timeout: float = DEFAULT_TIMEOUT,
        scene_timeout: float = SCENE_TIMEOUT,
    ) -> None:
        """Wrap a connected BleakClient.

        ``name`` is the advertised local name; LTPDUv3 uses it as the SRP
        username.
        """
        self.advertisement = advertisement
        self._timeout = timeout
        self._scene_timeout = scene_timeout
        self._lock = asyncio.Lock()
        self._session: LegacySession | V3Session
        if advertisement.ltpdu_v3:
            self._session = V3Session(
                client, username=name, matter=advertisement.matter, timeout=timeout
            )
        else:
            self._session = LegacySession(client, v2=advertisement.v2)
        self._started = False
        #: Set once the session can no longer be trusted; reconnect to recover.
        self.broken = False
        self.authenticated = False

    # ------------------------------------------------------------------ plumbing

    @property
    def ltpdu_v3(self) -> bool:
        """Return whether the session speaks LTPDUv3 (experimental)."""
        return isinstance(self._session, V3Session)

    @property
    def protocol(self) -> str:
        """Return a short description of the protocol in use."""
        if self.ltpdu_v3:
            return "LTPDUv3"
        return "legacy v2" if self.advertisement.v2 else "legacy"

    @property
    def supports_scenes(self) -> bool:
        """Return whether simple scenes work (not on firmware 5: Effects V2)."""
        return not self.ltpdu_v3

    async def _exchange[T](
        self, call: Callable[[], Awaitable[T]], timeout: float | None = None
    ) -> T:
        """Run one request/response under the lock, failing the session on error."""
        async with self._lock:
            if self.broken:
                raise NanoleafConnectionError("the session failed; reconnect")
            if not self._started and self.ltpdu_v3:
                await self._run(self._v3.start, timeout)
                self._started = True
            return await self._run(call, timeout)

    async def _run[T](
        self, call: Callable[[], Awaitable[T]], timeout: float | None
    ) -> T:
        try:
            async with asyncio.timeout(timeout or self._timeout):
                return await call()
        except (BleakError, TimeoutError, OSError, EOFError, InvalidTag) as exc:
            # A message is lost or garbled: the shared cipher state is gone.
            self.broken = True
            self.authenticated = False
            raise NanoleafConnectionError(
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            ) from exc
        except NanoleafAuthError:
            self.broken = True
            self.authenticated = False
            raise

    @property
    def _v3(self) -> V3Session:
        if not isinstance(self._session, V3Session):
            raise NanoleafUnsupportedError("only firmware 5 and later supports this")
        return self._session

    async def _request(
        self,
        method: Method,
        commands: list[tuple[str, bytes | None]],
        timeout: float | None = None,
    ) -> dict[str, tuple[int, bytes]]:
        if not self.authenticated and not self.broken:
            raise NanoleafProtocolError("not logged in")
        return await self._exchange(
            lambda: self._session.request(method, commands), timeout
        )

    @staticmethod
    def _value(result: dict[str, tuple[int, bytes]], endpoint: str) -> bytes:
        if endpoint not in result:
            raise NanoleafProtocolError(f"no answer for {endpoint}")
        status, value = result[endpoint]
        if status == Status.INVALID_ENDPOINT:
            raise NanoleafUnsupportedError(f"{endpoint} is not supported")
        if status != Status.SUCCESS:
            raise NanoleafProtocolError(f"{endpoint} failed with status {status}")
        return value

    # ------------------------------------------------------------------ session

    async def identify(self) -> None:
        """Blink the light. Needs no pairing."""
        await self._exchange(self._session.identify)

    async def pair(self, setup_code: str) -> Credentials:
        """Pair with the 8-digit setup code and log in."""
        code = "".join(ch for ch in setup_code if ch.isdigit())
        if len(code) != 8:
            raise ValueError("the setup code has 8 digits")
        if isinstance(self._session, V3Session):
            session = self._session
            token, client_id = await self._exchange(
                lambda: session.authenticate(setup_code=code)
            )
            credentials = Credentials(token, client_id)
        else:
            legacy = self._session
            credentials = Credentials(
                await self._exchange(lambda: legacy.authenticate(setup_code=code))
            )
        self.authenticated = True
        return credentials

    async def login(self, credentials: Credentials) -> None:
        """Log in with stored credentials."""
        if isinstance(self._session, V3Session):
            session = self._session
            await self._exchange(
                lambda: session.authenticate(
                    token=credentials.token, client_id=credentials.client_id
                )
            )
        else:
            legacy = self._session
            await self._exchange(lambda: legacy.authenticate(token=credentials.token))
            self.authenticated = True
            # The NL62 refuses a bad token with a GATT error, but the app's code
            # says other models answer it with the same empty reply as a good
            # one. A read shows whether the session works: any answer for the
            # endpoint means both sides share the keystream. If the read fails
            # with the link still up, the token is what is wrong.
            try:
                if "di" not in await self._request(Method.GET, [("di", None)]):
                    raise NanoleafProtocolError("no answer for di")
            except NanoleafError as exc:
                self.authenticated = False
                self.broken = True
                if not legacy.connected:
                    raise
                raise NanoleafAuthError(
                    f"the device did not accept the token ({exc})"
                ) from exc
        self.authenticated = True

    async def end_session(self) -> None:
        """Tell an LTPDUv3 device the session ends. Legacy devices have no such message."""
        if self.ltpdu_v3 and self.authenticated and not self.broken:
            await self._exchange(self._v3.disconnect)

    async def unpair(self) -> None:
        """Remove this controller's pairing (LTPDUv3 only).

        Legacy firmware has no device-side unpair: the token stays valid until
        a factory reset.
        """
        if not self.authenticated:
            raise NanoleafProtocolError("not logged in")
        await self._exchange(self._v3.unpair)
        self.authenticated = False

    # ------------------------------------------------------------------ light

    async def get_state(self, *, colour_temperature: bool = False) -> LightState:
        """Read on/off, brightness, hue, saturation (and colour temperature)."""
        endpoints = ["lb/0/oo", "lb/0/pb", "lb/0/hu", "lb/0/sa"]
        if colour_temperature:
            endpoints.append("lb/0/ct")
        result = await self._request(Method.GET, [(ep, None) for ep in endpoints])

        def number(endpoint: str) -> int:
            return int.from_bytes(self._value(result, endpoint), "big")

        return LightState(
            on=number("lb/0/oo") != 0,
            brightness=number("lb/0/pb"),
            hue=number("lb/0/hu"),
            saturation=number("lb/0/sa"),
            colour_temperature=number("lb/0/ct") if colour_temperature else None,
        )

    async def supports_colour_temperature(self) -> bool:
        """Return whether the device has a colour temperature (the NL62 has not)."""
        result = await self._request(Method.GET, [("lb/0/ct", None)])
        status, _value = result.get("lb/0/ct", (Status.INVALID_ENDPOINT, b""))
        return status == Status.SUCCESS

    async def set_light(
        self,
        *,
        on: bool | None = None,
        brightness: int | None = None,
        hue: int | None = None,
        saturation: int | None = None,
        colour_temperature: int | None = None,
    ) -> None:
        """Write any of the basic endpoints in one request.

        Brightness is 0-100 %, hue 0-360, saturation 0-100, colour temperature
        in Kelvin. Brightness dims a running scene without stopping it; a
        colour replaces it.
        """
        commands: list[tuple[str, bytes | None]] = []
        if hue is not None and saturation is not None:
            commands += [("lb/0/hu", u16(hue)), ("lb/0/sa", u16(saturation))]
        if colour_temperature is not None:
            commands.append(("lb/0/ct", u16(colour_temperature)))
        if brightness is not None:
            commands.append(("lb/0/pb", u16(brightness)))
        if on is not None:
            commands.append(("lb/0/oo", b"\x01" if on else b"\x00"))
        if not commands:
            return
        result = await self._request(Method.PUT, commands)
        for endpoint, _value in commands:
            self._value(result, endpoint)

    async def device_information(self) -> DeviceInformation:
        """Read hardware, firmware, serial number and EUI."""
        result = await self._request(Method.GET, [("di", None)])
        return parse_device_information(self._value(result, "di"))

    # ------------------------------------------------------------------ command interface

    async def _ci(self, family: int, command: int, data: bytes = b"") -> bytes:
        """POST to the Command Interface and return the reply's data."""
        if not self.supports_scenes:
            raise NanoleafUnsupportedError("scenes need Effects V2 on firmware 5")
        body = bytes([family, command]) + u16(len(data)) + data
        result = await self._request(
            Method.POST, [("ci", body)], timeout=self._scene_timeout
        )
        reply = self._value(result, "ci")
        # Verified on the NL62: a 4-byte header, although the app drops only 3.
        return reply[_CI_REPLY_HEADER:]

    async def list_scenes(self) -> list[int]:
        """Return the IDs of the stored scenes."""
        return list(await self._ci(_CI_SCENE, _SCENE_LIST))

    async def get_scene(self, scene_id: int) -> Scene:
        """Read a stored scene.

        The device answers a missing ID with stale data rather than an error,
        so check list_scenes() first.
        """
        return decode_scene(await self._ci(_CI_SCENE, _SCENE_GET, bytes([scene_id])))[1]

    async def activate_scene(self, scene_id: int) -> None:
        """Play a stored scene. This also switches the light on."""
        await self._ci(_CI_SCENE, _SCENE_ACTIVATE, bytes([scene_id]))

    async def preview_scene(self, scene: Scene) -> None:
        """Play a scene without storing it. This also switches the light on."""
        await self._ci(_CI_SCENE, _SCENE_PREVIEW, scene.encode(PREVIEW_ID))

    async def add_scene(self, scene_id: int, scene: Scene) -> None:
        """Store a scene under an ID.

        The transport reports success even when the device refuses, so the
        reply byte is what tells (00 stored, 01 refused: no room).
        """
        reply = await self._ci(_CI_SCENE, _SCENE_ADD, scene.encode(scene_id))
        if reply[:1] not in (b"", b"\x00"):
            raise NanoleafStripFullError(
                f"the device refused scene {scene_id} (reply {reply.hex()})"
            )

    async def delete_scene(
        self, scene_id: int, *, allow_protected: bool = False
    ) -> None:
        """Delete a stored scene. Deleting a missing ID also succeeds.

        Factory scenes (250-254) cannot be put back, so they are refused unless
        ``allow_protected`` is given.
        """
        if scene_id in PROTECTED_SCENE_IDS and not allow_protected:
            raise ValueError(f"scene {scene_id} is a factory scene")
        await self._ci(_CI_SCENE, _SCENE_DELETE, bytes([scene_id]))

    async def current_scene(self) -> int:
        """Return what plays: 0x00 nothing (off or a static colour), 0xFF a preview, else the stored ID."""
        reply = await self._ci(_CI_SCENE, _SCENE_CURRENT)
        return reply[0] if reply else NO_SCENE_ID

    async def get_power_loss_recovery(self) -> bool:
        """Return whether the light restores its on/off state after a power cut (else it comes back on)."""
        reply = await self._ci(_CI_POWER_LOSS, _POWER_LOSS_GET, b"\x00")
        return reply[:1] == b"\x01"

    async def set_power_loss_recovery(self, enabled: bool) -> None:
        """Choose whether the light restores its on/off state after a power cut (else it comes back on)."""
        await self._ci(_CI_POWER_LOSS, _POWER_LOSS_SET, b"\x01" if enabled else b"\x00")


__all__ = [
    "Credentials",
    "DeviceInformation",
    "LightState",
    "NanoleafDevice",
    "NanoleafError",
]
