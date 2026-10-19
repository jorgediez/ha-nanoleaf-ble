"""Fake strips and device data shared by the tests.

The fakes speak the real protocols at the GATT level: X25519 + shared CTR +
CoAP for the legacy firmware, SRP-6a + CCM for LTPDUv3. So the library is
exercised end to end, and the integration tests drive it through them too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import inspect
import secrets
import struct
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from custom_components.nanoleaf_ble.lib import commands, const, ltpdu_v3
from custom_components.nanoleaf_ble.lib.legacy import SharedCTR
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

ADDRESS = "DF:31:88:26:BE:3A"
LOCAL_NAME = "Nanoleaf Light Bulb"
SETUP_CODE = "12345678"
TOKEN = bytes.fromhex("0102030405060708")
SERIAL = "N23490K1500"
EUI = "dc8e95fffe0b345f"

#: The NL62's advertisement, as captured (protocol doc §7).
NL62_MANUFACTURER_DATA = bytes.fromhex(
    "4e4c4d3001063102000010dc8e95fffe0b345f4e4c363200000000"
)


def advertisement_bytes(model: str, firmware: tuple[int, int, int]) -> bytes:
    """Return manufacturer data for a non-partner model."""
    return (
        model.encode()
        + bytes(firmware)
        + bytes([1, 0, 0])
        + b"\x00"
        + bytes.fromhex(EUI)
        + b"\x12\x34"
    )


def make_ble_device(address: str = ADDRESS, name: str = LOCAL_NAME) -> BLEDevice:
    """Return a BLEDevice, whichever bleak version is installed."""
    if "rssi" in inspect.signature(BLEDevice.__init__).parameters:
        return BLEDevice(address, name, {}, -60)  # type: ignore[call-arg]
    return BLEDevice(address, name, {})


def make_service_info(
    manufacturer_data: bytes = NL62_MANUFACTURER_DATA,
    *,
    address: str = ADDRESS,
    name: str = LOCAL_NAME,
    rssi: int = -60,
    company_id: int = const.NANOLEAF_COMPANY_ID,
) -> BluetoothServiceInfoBleak:
    """Return the service info Home Assistant would pass on discovery."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={company_id: manufacturer_data},
        service_data={},
        service_uuids=[],
        source="local",
        device=make_ble_device(address, name),
        advertisement=None,
        connectable=True,
        time=0,
        tx_power=None,
    )


def device_information_record(
    hardware: str = "2.0.0", firmware: str = "1.6.49"
) -> bytes:
    """Return the 37-byte "di" record the NL62 sends."""
    return (
        hardware.encode().ljust(10, b"\x00")
        + firmware.encode().ljust(8, b"\x00")
        + SERIAL.encode()
        + bytes.fromhex(EUI)
    )


@dataclass
class StripState:
    """What a strip remembers, and answers commands from."""

    on: bool = False
    brightness: int = 100
    hue: int = 28
    saturation: int = 66
    colour_temperature: int | None = None
    current: int = 0
    power_loss_recovery: bool | None = True
    scenes: dict[int, bytes] = field(default_factory=dict)
    capacity: int = const.MAX_SCENES
    device_information: bytes = field(default_factory=device_information_record)
    identified: int = 0
    previews: list[bytes] = field(default_factory=list)
    #: Endpoint -> status to answer with instead of success.
    statuses: dict[str, int] = field(default_factory=dict)

    def handle(self, method: int, endpoint: str, value: bytes | None) -> bytes:
        """Answer one command with status + value."""
        if endpoint in self.statuses:
            return bytes([self.statuses[endpoint]])
        if endpoint == "ci":
            return b"\x00" + self._ci(value or b"")
        if endpoint == "di":
            return b"\x00" + self.device_information
        if endpoint == "lb/0/id":
            self.identified += 1
            return b"\x00"
        if endpoint == "lb/0/ct" and self.colour_temperature is None:
            return bytes([commands.Status.INVALID_ENDPOINT])
        fields = {
            "lb/0/oo": ("on", 1),
            "lb/0/pb": ("brightness", 2),
            "lb/0/hu": ("hue", 2),
            "lb/0/sa": ("saturation", 2),
            "lb/0/ct": ("colour_temperature", 2),
        }
        if endpoint not in fields:
            return bytes([commands.Status.INVALID_ENDPOINT])
        name, size = fields[endpoint]
        if method == commands.Method.PUT and value is not None:
            number = int.from_bytes(value, "big")
            setattr(self, name, bool(number) if name == "on" else number)
            if name in ("hue", "saturation", "colour_temperature"):
                self.current = 0
            return b"\x00"
        return b"\x00" + int(getattr(self, name)).to_bytes(size, "big")

    def _ci(self, body: bytes) -> bytes:
        family, command = body[0], body[1]
        (length,) = struct.unpack_from(">H", body, 2)
        data = body[4 : 4 + length]
        reply = b""
        if family == 0x07:
            if command == 0x01:
                self.previews.append(data)
                self.current, self.on = 0xFF, True
            elif command == 0x02:
                scene_id = data[2]
                if len(self.scenes) >= self.capacity and scene_id not in self.scenes:
                    reply = b"\x01"
                else:
                    # As the NL62 does: storing a scene plays it.
                    self.scenes[scene_id] = data
                    self.current, self.on = scene_id, True
                    reply = b"\x00"
            elif command == 0x03:
                reply = bytes(self.scenes)
            elif command == 0x04:
                reply = self.scenes.get(data[0], b"\x01\x02\x00\x00")
            elif command == 0x05:
                self.scenes.pop(int.from_bytes(data, "big"), None)
            elif command == 0x06:
                self.current, self.on = data[0], True
            elif command == 0x07:
                reply = bytes([self.current if self.on else 0])
        elif family == 0x09:
            if self.power_loss_recovery is None:
                return b""
            if command == 0x02:
                reply = b"\x01" if self.power_loss_recovery else b"\x00"
            elif command == 0x01:
                self.power_loss_recovery = data[:1] == b"\x01"
        return bytes([family | 0x80, command]) + struct.pack(">H", len(reply)) + reply


def parse_commands(body: bytes) -> list[tuple[str, int | None, bytes | None]]:
    """Split a request into (endpoint, method, payload)."""
    out: list[tuple[str, int | None, bytes | None]] = []
    offset = 0
    while offset + 4 <= len(body):
        tlv_type, length = struct.unpack_from(">HH", body, offset)
        value = body[offset + 4 : offset + 4 + length]
        offset += 4 + length
        if tlv_type == commands.TLV_ENDPOINT:
            out.append((value.decode(), None, None))
        elif tlv_type == commands.TLV_METHOD:
            endpoint, _method, payload = out[-1]
            out[-1] = (endpoint, value[0], payload)
        elif tlv_type == commands.TLV_PAYLOAD:
            endpoint, method, _payload = out[-1]
            out[-1] = (endpoint, method, value)
    return out


def respond(state: StripState, body: bytes, default_method: int | None) -> bytes:
    """Answer every command in a request."""
    out = b""
    for endpoint, method, value in parse_commands(body):
        name = endpoint.encode()
        answer = state.handle(
            default_method if method is None else method, endpoint, value
        )
        out += struct.pack(">HH", commands.TLV_ENDPOINT, len(name)) + name
        out += struct.pack(">HH", commands.TLV_RESPONSE, len(answer)) + answer
    return out


_COAP_METHODS = {
    1: commands.Method.GET,
    2: commands.Method.POST,
    3: commands.Method.PUT,
    4: commands.Method.DELETE,
}


class FakeClient:
    """The BleakClient side every fake shares."""

    def __init__(self) -> None:
        """Start connected."""
        self.is_connected = True
        self.disconnected_callback: Callable[[Any], None] | None = None
        #: Raised by the next write, to simulate a dropped link.
        self.fail_next: Exception | None = None
        self.writes = 0

    async def disconnect(self) -> bool:
        """Drop the link, as bleak does."""
        if self.is_connected:
            self.is_connected = False
            if self.disconnected_callback is not None:
                self.disconnected_callback(self)
        return True

    def _check(self) -> None:
        self.writes += 1
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc


class FakeLegacyStrip(FakeClient):
    """A legacy-firmware strip (NL62 by default) behind a GATT client."""

    def __init__(
        self,
        state: StripState | None = None,
        *,
        v2: bool = False,
        setup_code: str = SETUP_CODE,
        tokens: set[bytes] | None = None,
        accepts_pairings: bool = True,
        silent_refusal: bool = False,
    ) -> None:
        """Create a strip that knows a setup code and some tokens.

        With ``silent_refusal``, a bad token gets the same empty reply as a good
        one, as the app's code says some models do, and the session then
        answers nothing that decrypts.
        """
        super().__init__()
        self.state = state or StripState()
        self.v2 = v2
        self.setup_code = setup_code
        self.tokens = {TOKEN} if tokens is None else tokens
        self.accepts_pairings = accepts_pairings
        self.silent_refusal = silent_refusal
        self._stale = False
        self._stream: SharedCTR | None = None
        self._setup_reply = b""
        self._encrypted_reply = b""
        self._plain_reply = b""
        self._phase = "keyx"

    async def write_gatt_char(
        self, char: str, data: bytes, response: bool = True
    ) -> None:
        """Take a write."""
        self._check()
        data = bytes(data)
        if char == const.LEGACY_SETUP_UUID:
            self._setup(data)
        elif char == const.LEGACY_ENCRYPTED_UUID:
            assert self._stream is not None
            request = self._stream.xor(data)
            if self.v2:
                reply = respond(self.state, request, None)
            else:
                marker = request.index(b"\xff", 6)
                reply = bytes([0x62, 0x44, *request[2:6], 0xFF]) + respond(
                    self.state, request[marker + 1 :], _COAP_METHODS[request[1]]
                )
            self._encrypted_reply = (
                secrets.token_bytes(len(reply))
                if self._stale
                else self._stream.xor(reply)
            )
        elif char == const.LEGACY_UNENCRYPTED_UUID:
            body = data if self.v2 else data[data.index(b"\xff", 6) + 1 :]
            self._plain_reply = respond(self.state, body, commands.Method.POST)

    def _setup(self, data: bytes) -> None:
        if self._phase == "keyx":
            private = X25519PrivateKey.generate()
            shared = private.exchange(X25519PublicKey.from_public_bytes(data))
            key_salt, iv_salt = (
                (const.V2_KEY_SALT, const.V2_IV_SALT)
                if self.v2
                else (const.LEGACY_KEY_SALT, const.LEGACY_IV_SALT)
            )
            self._stream = SharedCTR(
                hashlib.sha1(key_salt + shared).digest()[:16],  # noqa: S324 - the device KDF
                hashlib.sha1(iv_salt + shared).digest()[:16],  # noqa: S324 - the device KDF
            )
            self._setup_reply = private.public_key().public_bytes_raw()
            self._phase = "public_key"
            return
        assert self._stream is not None
        message = self._stream.xor(data)
        kind, secret = message[0], message[2 : 2 + message[1]]
        # As the NL62 does: a pairing returns the new token; a login returns
        # nothing; a wrong code or token is refused with a GATT error.
        reply, self._refusal = b"", None
        if kind == 0x03 and secret.decode() == self.setup_code:
            if self.accepts_pairings:
                reply = secrets.token_bytes(8)
                self.tokens.add(reply)
            else:
                reply = const.LEGACY_NO_MORE_PAIRINGS
        elif kind == 0x03:
            self._refusal = BleakError(
                f"Bluetooth GATT Error address={ADDRESS} handle=142 error=129 "
                "description=Internal error"
            )
        elif secret not in self.tokens and self.silent_refusal:
            self._stale = True
        elif secret not in self.tokens:
            self._refusal = BleakError(
                f"Bluetooth GATT Error address={ADDRESS} handle=142 error=130 "
                "description=Wrong state"
            )
        self._setup_reply = self._stream.xor(reply)
        self._phase = "token"

    async def read_gatt_char(self, char: str) -> bytearray:
        """Answer a read."""
        self._check()
        if char == const.LEGACY_SETUP_UUID:
            if getattr(self, "_refusal", None) is not None:
                raise self._refusal
            return bytearray(self._setup_reply)
        if char == const.LEGACY_ENCRYPTED_UUID:
            return bytearray(self._encrypted_reply)
        return bytearray(self._plain_reply)


class FakeV3Strip(FakeClient):
    """A firmware-5 strip: SRP-6a, then CCM (or CTR) frames by indication."""

    def __init__(
        self,
        state: StripState | None = None,
        *,
        username: str = LOCAL_NAME,
        setup_code: str = SETUP_CODE,
        tokens: dict[bytes, int] | None = None,
        version: bytes = ltpdu_v3.SESSION_3_2,
        prime: int = const.SRP_N_2048,
    ) -> None:
        """Create a strip that knows a setup code and some (token, client id)s."""
        super().__init__()
        self.state = state or StripState()
        self.username = username.encode()
        self.setup_code = setup_code
        self.tokens = {TOKEN: 0x0042} if tokens is None else tokens
        self.version = version
        self.prime = prime
        self._notify: Callable[[Any, bytearray], None] | None = None
        self._cipher: ltpdu_v3.V3Cipher | None = None
        self._srp: dict[str, Any] = {}
        self.unpaired = False
        self.disconnect_messages = 0

    async def start_notify(
        self, char: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        """Subscribe."""
        self._notify = callback

    async def write_gatt_char(
        self, char: str, data: bytes, response: bool = True
    ) -> None:
        """Take a request frame and answer it by indication."""
        self._check()
        data = bytes(data)
        _ver, _kind, message_id, client_id, uri, _method, _length = struct.unpack_from(
            ">BBHHBBI", data
        )
        payload = data[12:]
        code, reply, reply_client = 0, b"", client_id
        if uri == ltpdu_v3.URI_IDENTIFY:
            self.state.identified += 1
        elif uri == ltpdu_v3.URI_SECURE and self._cipher is None:
            code, reply, reply_client = self._auth(payload, client_id)
        elif uri == ltpdu_v3.URI_SECURE:
            tlvs = ltpdu_v3.decode_auth_tlvs(self._cipher.decrypt(payload))
            if ltpdu_v3.AUTH_TOKEN_REQUEST in tlvs:
                token = secrets.token_bytes(16)
                self.tokens[token] = self._srp["client_id"]
                reply = self._cipher.encrypt(
                    ltpdu_v3.encode_auth_tlvs([(ltpdu_v3.AUTH_TOKEN, token)])
                )
            else:
                if ltpdu_v3.AUTH_UNPAIR in tlvs:
                    self.unpaired = True
                if ltpdu_v3.AUTH_DISCONNECT in tlvs:
                    self.disconnect_messages += 1
                reply = self._cipher.encrypt(b"")
        elif uri == ltpdu_v3.URI_LTPDU and self._cipher is not None:
            body = self._cipher.decrypt(payload)
            reply = self._cipher.encrypt(respond(self.state, body, None))
        frame = (
            struct.pack(
                ">BBHHHI",
                1,
                ltpdu_v3.MSG_RESPONSE,
                message_id,
                reply_client,
                code,
                len(reply),
            )
            + reply
        )
        assert self._notify is not None
        notify = self._notify
        loop = asyncio.get_running_loop()
        # An acknowledgement first, as the device sends one before answering.
        loop.call_soon(
            notify,
            None,
            bytearray(
                struct.pack(">BBHH", 1, ltpdu_v3.MSG_ACK, message_id, reply_client)
            ),
        )
        loop.call_soon(notify, None, bytearray(frame))

    def _auth(self, payload: bytes, client_id: int) -> tuple[int, bytes, int]:
        tlvs = ltpdu_v3.decode_auth_tlvs(payload)
        H, ib, N, g = ltpdu_v3.sha256, ltpdu_v3.int_bytes, self.prime, const.SRP_G
        if ltpdu_v3.AUTH_PUBLIC_KEY in tlvs:
            # Step 1: pick the password the client should know.
            if tlvs[ltpdu_v3.AUTH_TYPE] == ltpdu_v3.AUTH_WITH_SETUP_CODE:
                password = bytes(int(d) for d in self.setup_code)
                if client_id == ltpdu_v3.UNASSIGNED_CLIENT_ID:
                    client_id = 0x0042
            else:
                password = next(
                    (t for t, cid in self.tokens.items() if cid == client_id), b"?"
                )
            salt = secrets.token_bytes(16)
            x = int.from_bytes(
                H(ib(int.from_bytes(salt, "big")), H(self.username + b":" + password)),
                "big",
            )
            k = int.from_bytes(H(ib(N), ib(g)), "big")
            b = secrets.randbits(256)
            server_public = (k * pow(g, x, N) + pow(g, b, N)) % N
            self._srp = {
                "salt": salt,
                "A": int.from_bytes(tlvs[ltpdu_v3.AUTH_PUBLIC_KEY], "big"),
                "B": server_public,
                "b": b,
                "x": x,
                "client_id": client_id,
            }
            return (
                0,
                ltpdu_v3.encode_auth_tlvs(
                    [
                        (ltpdu_v3.AUTH_SALT, salt),
                        (ltpdu_v3.AUTH_PUBLIC_KEY, ib(server_public)),
                        (ltpdu_v3.AUTH_SESSION_VERSION, self.version),
                    ]
                ),
                client_id,
            )
        # Step 2: check the client's proof.
        srp = self._srp
        u = int.from_bytes(H(ib(srp["A"]), ib(srp["B"])), "big")
        shared = pow(srp["A"] * pow(pow(g, srp["x"], N), u, N), srp["b"], N)
        key = H(ib(shared))
        n_xor_g = bytes(a ^ c for a, c in zip(H(ib(N)), H(ib(g)), strict=True))
        proof = H(
            n_xor_g,
            H(self.username),
            ib(int.from_bytes(srp["salt"], "big")),
            ib(srp["A"]),
            ib(srp["B"]),
            key,
        )
        if proof != tlvs.get(ltpdu_v3.AUTH_PROOF):
            return ltpdu_v3.RESPONSE_UNAUTHORIZED, b"", srp["client_id"]
        self._cipher = ltpdu_v3.V3Cipher(self.version, hashlib.sha256(key).digest())
        server_proof = H(ib(srp["A"]), proof, key)
        return (
            0,
            ltpdu_v3.encode_auth_tlvs([(ltpdu_v3.AUTH_PROOF, server_proof)]),
            srp["client_id"],
        )
