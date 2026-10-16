"""LTPDUv3, spoken by firmware 5.0.0 and later. EXPERIMENTAL.

SRP-6a in Nanoleaf's flavour, then AES-128-CCM (session 3.1/3.2) or AES-128-CTR
with a CRC (3.0). Requests are framed and answered by indication.

This is a transcription of the Android app's code. No firmware 5 device has
been tested yet, so it is not known to work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
import secrets
import struct
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from .commands import Method, decode_response, encode_command
from .const import SRP_G, SRP_N_512, SRP_N_2048, V3_INDICATE_UUID, V3_WRITE_UUID
from .exceptions import NanoleafAuthError, NanoleafProtocolError
from .legacy import SharedCTR

_LOGGER = logging.getLogger(__name__)

FRAME_VERSION = 0x01
MSG_REQUEST = 0x01
MSG_RESPONSE = 0x02
MSG_EVENT = 0x03
MSG_ACK = 0xFE

URI_SECURE = 0x01
URI_LTPDU = 0x02
URI_IDENTIFY = 0x04

#: The client ID before the device assigns one.
UNASSIGNED_CLIENT_ID = 0xFFFF

# Authentication TLV types (1-byte type, 2-byte length).
AUTH_TYPE = 0x01
AUTH_SALT = 0x02
AUTH_PUBLIC_KEY = 0x03
AUTH_PROOF = 0x04
AUTH_TOKEN_REQUEST = 0x05
AUTH_TOKEN = 0x06
AUTH_SESSION_TYPE = 0x07
AUTH_DISCONNECT = 0x08
AUTH_UNPAIR = 0x09
AUTH_SESSION_VERSION = 0x11
AUTH_ERROR = 0xF0

AUTH_WITH_SETUP_CODE = b"\x01"
AUTH_WITH_TOKEN = b"\x02"
#: "Connection-independent", which the app always asks for.
SESSION_CONNECTION_INDEPENDENT = b"\x02"

SESSION_3_0 = b"\x03\x00"
SESSION_3_1 = b"\x03\x01"
SESSION_3_2 = b"\x03\x02"

#: Response codes in a response frame.
RESPONSE_OK = 0
RESPONSE_UNAUTHORIZED = 3


class NotifyingGattClient(Protocol):
    """The part of BleakClient LTPDUv3 uses."""

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool = ...
    ) -> None:
        """Write a characteristic."""

    async def start_notify(
        self, char_specifier: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        """Subscribe to indications."""


# --------------------------------------------------------------------------- framing


def encode_request(
    message_id: int, client_id: int, uri: int, method: int, data: bytes
) -> bytes:
    """Frame a request."""
    return (
        struct.pack(
            ">BBHHBBI",
            FRAME_VERSION,
            MSG_REQUEST,
            message_id,
            client_id,
            uri,
            method,
            len(data),
        )
        + data
    )


@dataclass(frozen=True, slots=True)
class Frame:
    """A decoded frame from the device."""

    type: int
    message_id: int
    client_id: int
    code: int = 0
    data: bytes = b""


def decode_frame(frame: bytes) -> Frame:
    """Decode a response, event or acknowledgement frame."""
    if len(frame) < 6:
        raise NanoleafProtocolError(f"frame too short ({len(frame)} bytes)")
    _version, kind, message_id, client_id = struct.unpack_from(">BBHH", frame)
    if kind == MSG_RESPONSE and len(frame) >= 12:
        code, _length = struct.unpack_from(">HI", frame, 6)
        return Frame(kind, message_id, client_id, code, frame[12:])
    if kind == MSG_EVENT and len(frame) >= 14:
        return Frame(kind, message_id, client_id, data=frame[14:])
    return Frame(kind, message_id, client_id)


def encode_auth_tlvs(items: list[tuple[int, bytes]]) -> bytes:
    """Encode authentication TLVs, sorted by type as the app does."""
    return b"".join(
        bytes([tlv_type]) + struct.pack(">H", len(value)) + value
        for tlv_type, value in sorted(items, key=lambda item: item[0])
    )


def decode_auth_tlvs(data: bytes) -> dict[int, bytes]:
    """Decode authentication TLVs."""
    out: dict[int, bytes] = {}
    offset = 0
    while offset + 3 <= len(data):
        tlv_type = data[offset]
        (length,) = struct.unpack_from(">H", data, offset + 1)
        out[tlv_type] = data[offset + 3 : offset + 3 + length]
        offset += 3 + length
    return out


# --------------------------------------------------------------------------- SRP-6a


def int_bytes(value: int) -> bytes:
    """Return the minimal unsigned big-endian encoding (no padding to len(N))."""
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def sha256(*parts: bytes) -> bytes:
    """Hash the concatenation of the parts."""
    return hashlib.sha256(b"".join(parts)).digest()


class SRPClient:
    """The client side of Nanoleaf's SRP-6a.

    Every value is hashed unpadded, g included, which differs from RFC 5054;
    the device has to match the app, so this matches the app.
    """

    def __init__(self, username: str, password: bytes, prime: int) -> None:
        """Pick the ephemeral secret."""
        self.username = username.encode()
        self.password = password
        self.prime = prime
        self._a = secrets.randbits(prime.bit_length())
        self.public_key = pow(SRP_G, self._a, prime)
        self.key = b""
        self.proof = b""
        self.server_proof = b""

    def process_challenge(self, salt: bytes, server_public: bytes) -> bytes:
        """Return M1 for the device's salt and public key B."""
        prime = self.prime
        s = int.from_bytes(salt, "big")
        b_pub = int.from_bytes(server_public, "big")
        u = int.from_bytes(sha256(int_bytes(self.public_key), int_bytes(b_pub)), "big")
        x = int.from_bytes(
            sha256(int_bytes(s), sha256(self.username + b":" + self.password)), "big"
        )
        k = int.from_bytes(sha256(int_bytes(prime), int_bytes(SRP_G)), "big")
        shared = pow(b_pub - k * pow(SRP_G, x, prime), self._a + u * x, prime)
        self.key = sha256(int_bytes(shared))
        n_xor_g = bytes(
            a ^ b
            for a, b in zip(
                sha256(int_bytes(prime)), sha256(int_bytes(SRP_G)), strict=True
            )
        )
        self.proof = sha256(
            n_xor_g,
            sha256(self.username),
            int_bytes(s),
            int_bytes(self.public_key),
            int_bytes(b_pub),
            self.key,
        )
        self.server_proof = sha256(int_bytes(self.public_key), self.proof, self.key)
        return self.proof

    @property
    def session_key(self) -> bytes:
        """Return the 32-byte secret the session cipher is derived from."""
        return hashlib.sha256(self.key).digest()


# --------------------------------------------------------------------------- session


def crc16_xmodem(data: bytes) -> bytes:
    """Return CRC-16/XMODEM (poly 0x1021, init 0), big-endian."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return struct.pack(">H", crc)


class V3Cipher:
    """The session cipher. One counter (or keystream) serves both directions."""

    def __init__(self, version: bytes, session_key: bytes) -> None:
        """Derive the cipher from the SRP session key."""
        self.version = version
        self._iv_base = session_key[16:24]
        self._counter = 0
        if version == SESSION_3_0:
            self._ctr: SharedCTR | None = SharedCTR(
                session_key[:16], b"\x00" * 8 + self._iv_base
            )
            self._ccm: AESCCM | None = None
        else:
            self._ctr = None
            self._ccm = AESCCM(session_key[:16], tag_length=4)

    def _nonce(self) -> bytes:
        counter = self._counter.to_bytes(8, "big")
        self._counter += 1
        if self.version == SESSION_3_1:
            return counter[3:] + self._iv_base  # 13 bytes
        return counter[4:] + self._iv_base  # 12 bytes (3.2)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt an outgoing message."""
        if self._ccm is None:
            ctr = self._ctr
            if ctr is None:  # pragma: no cover - set in __init__
                raise NanoleafProtocolError("no session cipher")
            return ctr.xor(plaintext + crc16_xmodem(plaintext))
        return self._ccm.encrypt(self._nonce(), plaintext, None)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt an incoming message."""
        if self._ccm is None:
            ctr = self._ctr
            if ctr is None:  # pragma: no cover - set in __init__
                raise NanoleafProtocolError("no session cipher")
            # The trailing CRC is dropped without checking, as the app does.
            return ctr.xor(ciphertext)[:-2]
        return self._ccm.decrypt(self._nonce(), ciphertext, None)


class V3Session:
    """One LTPDUv3 session over a connected GATT client."""

    def __init__(
        self,
        client: NotifyingGattClient,
        *,
        username: str,
        matter: bool,
        timeout: float,
    ) -> None:
        """Wrap a connected client. ``username`` is the advertised local name."""
        self._client = client
        self._username = username
        self._prime = SRP_N_512 if matter else SRP_N_2048
        self._timeout = timeout
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        self._cipher: V3Cipher | None = None
        self.client_id = UNASSIGNED_CLIENT_ID

    async def start(self) -> None:
        """Subscribe to the device's indications."""
        await self._client.start_notify(V3_INDICATE_UUID, self._on_indication)

    def _on_indication(self, _sender: Any, data: bytearray) -> None:
        try:
            frame = decode_frame(bytes(data))
        except NanoleafProtocolError:
            _LOGGER.debug("ignoring malformed frame %s", bytes(data).hex())
            return
        if frame.type == MSG_ACK:
            return
        future = self._pending.get(frame.message_id)
        if future is not None and not future.done():
            future.set_result(frame)

    async def _call(
        self, uri: int, data: bytes, *, timeout: float | None = None
    ) -> Frame:
        message_id = secrets.randbelow(0x10000)
        future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._client.write_gatt_char(
                V3_WRITE_UUID,
                encode_request(message_id, self.client_id, uri, Method.POST, data),
                response=True,
            )
            async with asyncio.timeout(timeout or self._timeout):
                return await future
        finally:
            self._pending.pop(message_id, None)

    async def identify(self) -> None:
        """Blink the light. Needs no pairing."""
        await self._call(URI_IDENTIFY, b"")

    async def authenticate(
        self,
        *,
        setup_code: str | None = None,
        token: bytes | None = None,
        client_id: int | None = None,
    ) -> tuple[bytes, int]:
        """Run SRP with the setup code or a stored token.

        Return (auth token, client ID). Pairing asks the device for a new token;
        a login returns the token it was given.
        """
        if token is not None:
            if client_id is None:
                raise ValueError("an LTPDUv3 token needs its client ID")
            password, auth_type = token, AUTH_WITH_TOKEN
            self.client_id = client_id
        elif setup_code is not None:
            # The setup code's digits as byte values 0-9, not ASCII.
            password, auth_type = (
                bytes(int(d) for d in setup_code),
                AUTH_WITH_SETUP_CODE,
            )
        else:
            raise ValueError("a setup code or a token is required")
        srp = SRPClient(self._username, password, self._prime)

        reply = await self._call(
            URI_SECURE,
            encode_auth_tlvs(
                [
                    (AUTH_TYPE, auth_type),
                    (AUTH_PUBLIC_KEY, int_bytes(srp.public_key)),
                    (AUTH_SESSION_TYPE, SESSION_CONNECTION_INDEPENDENT),
                ]
            ),
        )
        tlvs = self._check_auth_reply(reply, "step 1")
        if self.client_id == UNASSIGNED_CLIENT_ID:
            self.client_id = reply.client_id
        if AUTH_SALT not in tlvs or AUTH_PUBLIC_KEY not in tlvs:
            raise NanoleafProtocolError("step 1 reply lacks the salt or public key")
        version = tlvs.get(AUTH_SESSION_VERSION, SESSION_3_0)

        proof = srp.process_challenge(tlvs[AUTH_SALT], tlvs[AUTH_PUBLIC_KEY])
        reply = await self._call(URI_SECURE, encode_auth_tlvs([(AUTH_PROOF, proof)]))
        tlvs = self._check_auth_reply(reply, "step 2")
        if tlvs.get(AUTH_PROOF) != srp.server_proof:
            # The app computes M2 but never checks it; neither does this.
            _LOGGER.debug("device proof M2 does not match")
        self._cipher = V3Cipher(version, srp.session_key)
        _LOGGER.debug("LTPDUv3 session %s.%s established", version[0], version[1])

        if token is not None:
            return token, self.client_id
        reply = await self._call(
            URI_SECURE,
            self._cipher.encrypt(encode_auth_tlvs([(AUTH_TOKEN_REQUEST, b"")])),
        )
        tlvs = decode_auth_tlvs(self._cipher.decrypt(reply.data))
        if AUTH_TOKEN not in tlvs:
            raise NanoleafProtocolError("step 3 reply lacks the auth token")
        return tlvs[AUTH_TOKEN], self.client_id

    @staticmethod
    def _check_auth_reply(reply: Frame, step: str) -> dict[int, bytes]:
        if reply.code == RESPONSE_UNAUTHORIZED:
            raise NanoleafAuthError(f"{step}: unauthorised")
        if reply.code != RESPONSE_OK:
            raise NanoleafProtocolError(f"{step}: response code {reply.code}")
        tlvs = decode_auth_tlvs(reply.data)
        if AUTH_ERROR in tlvs:
            raise NanoleafAuthError(f"{step}: device error {tlvs[AUTH_ERROR].hex()}")
        return tlvs

    async def request(
        self, method: Method, commands: list[tuple[str, bytes | None]]
    ) -> dict[str, tuple[int, bytes]]:
        """Send commands that share one method and return the decoded response."""
        if self._cipher is None:
            raise NanoleafProtocolError("not authenticated")
        body = b"".join(encode_command(ep, value, method) for ep, value in commands)
        reply = await self._call(URI_LTPDU, self._cipher.encrypt(body))
        if reply.code != RESPONSE_OK:
            raise NanoleafProtocolError(f"LTPDU response code {reply.code}")
        return decode_response(self._cipher.decrypt(reply.data))

    async def _secure_message(self, tlv_type: int) -> None:
        if self._cipher is None:
            raise NanoleafProtocolError("not authenticated")
        reply = await self._call(
            URI_SECURE, self._cipher.encrypt(encode_auth_tlvs([(tlv_type, b"")]))
        )
        if reply.data:
            # Decrypting keeps the shared counter in step for what follows.
            self._cipher.decrypt(reply.data)

    async def disconnect(self) -> None:
        """Tell the device the session ends."""
        await self._secure_message(AUTH_DISCONNECT)

    async def unpair(self) -> None:
        """Remove this controller's pairing from the device."""
        await self._secure_message(AUTH_UNPAIR)
