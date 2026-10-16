"""The legacy Bluetooth protocol, spoken by firmware below 5.0.0.

X25519 key exchange, then AES-128-CTR with ONE keystream that both directions
consume, so requests and replies must strictly alternate: a lost message
desynchronises the session for good. Verified on an NL62 (firmware 1.6.49).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .commands import Method, coap_unwrap, coap_wrap, decode_response, encode_command
from .const import (
    LEGACY_ENCRYPTED_UUID,
    LEGACY_IV_SALT,
    LEGACY_KEY_SALT,
    LEGACY_NO_MORE_PAIRINGS,
    LEGACY_SETUP_UUID,
    LEGACY_UNENCRYPTED_UUID,
    V2_IV_SALT,
    V2_KEY_SALT,
)
from .exceptions import (
    NanoleafAuthError,
    NanoleafProtocolError,
    NanoleafTooManyPairingsError,
)

_LOGGER = logging.getLogger(__name__)

#: TLV8 types of the login message (the app reuses HAP's names for them).
_TLV_TOKEN = 0x02
_TLV_SETUP_CODE = 0x03

_X25519_KEY_LENGTH = 32


class GattClient(Protocol):
    """The part of BleakClient the protocols use."""

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool = ...
    ) -> None:
        """Write a characteristic."""

    async def read_gatt_char(self, char_specifier: str) -> bytearray:
        """Read a characteristic."""


class SharedCTR:
    """One AES-CTR keystream consumed by both directions."""

    def __init__(self, key: bytes, nonce: bytes) -> None:
        """Start the keystream at the given initial counter block."""
        self._stream = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()

    def xor(self, data: bytes) -> bytes:
        """Encrypt or decrypt, advancing the shared keystream."""
        return self._stream.update(data)


class LegacySession:
    """One authenticated legacy session over a connected GATT client."""

    def __init__(self, client: GattClient, *, v2: bool) -> None:
        """Wrap a connected client.

        ``v2`` devices (Matter Essentials, 1D, Sense+) use the binary salts and
        send plain LTPDU with the method TLV; HomeKit Essentials wrap it in CoAP.
        """
        self._client = client
        self._v2 = v2
        self._stream: SharedCTR | None = None

    async def identify(self) -> None:
        """Blink the light. Needs no pairing."""
        command = encode_command("lb/0/id", b"\x01", Method.POST if self._v2 else None)
        request = command if self._v2 else coap_wrap(Method.POST, command, public=True)
        await self._client.write_gatt_char(
            LEGACY_UNENCRYPTED_UUID, request, response=True
        )
        await self._client.read_gatt_char(LEGACY_UNENCRYPTED_UUID)

    async def authenticate(
        self, *, setup_code: str | None = None, token: bytes | None = None
    ) -> bytes:
        """Exchange keys, then pair with the setup code or log in with a token.

        Return the auth token: a new one when pairing, the same one on login.
        The steps must run in this order on a fresh connection; reading the
        setup characteristic out of turn makes the device answer error 135.
        """
        private_key = X25519PrivateKey.generate()
        await self._client.write_gatt_char(
            LEGACY_SETUP_UUID,
            private_key.public_key().public_bytes_raw(),
            response=True,
        )
        device_key = bytes(await self._client.read_gatt_char(LEGACY_SETUP_UUID))
        if len(device_key) < _X25519_KEY_LENGTH:
            raise NanoleafProtocolError(
                f"device public key is {len(device_key)} bytes, expected 32"
            )
        shared = private_key.exchange(
            X25519PublicKey.from_public_bytes(device_key[:_X25519_KEY_LENGTH])
        )
        key_salt, iv_salt = (
            (V2_KEY_SALT, V2_IV_SALT) if self._v2 else (LEGACY_KEY_SALT, LEGACY_IV_SALT)
        )
        # SHA-1 is what the device uses to derive the session key; it is not a
        # choice made here.
        key = hashlib.sha1(key_salt + shared).digest()[:16]  # noqa: S324 - protocol-defined KDF
        nonce = hashlib.sha1(iv_salt + shared).digest()[:16]  # noqa: S324 - protocol-defined KDF
        self._stream = SharedCTR(key, nonce)

        if token is None:
            if setup_code is None:
                raise ValueError("a setup code or a token is required")
            secret = setup_code.encode("ascii")
            message = bytes([_TLV_SETUP_CODE, len(secret)]) + secret
        else:
            message = bytes([_TLV_TOKEN, len(token)]) + token
        await self._client.write_gatt_char(
            LEGACY_SETUP_UUID, self._stream.xor(message), response=True
        )
        reply = self._stream.xor(
            bytes(await self._client.read_gatt_char(LEGACY_SETUP_UUID))
        )
        if reply == LEGACY_NO_MORE_PAIRINGS:
            raise NanoleafTooManyPairingsError("the device accepts no more pairings")
        if not reply:
            raise NanoleafAuthError(
                "the device refused the token"
                if token is not None
                else "the device refused the setup code"
            )
        _LOGGER.debug("legacy session authenticated (%s-byte token)", len(reply))
        return reply

    async def request(
        self, method: Method, commands: list[tuple[str, bytes | None]]
    ) -> dict[str, tuple[int, bytes]]:
        """Send commands that share one method and return the decoded response."""
        if self._stream is None:
            raise NanoleafProtocolError("not authenticated")
        body = b"".join(
            encode_command(endpoint, value, method if self._v2 else None)
            for endpoint, value in commands
        )
        payload = body if self._v2 else coap_wrap(method, body)
        await self._client.write_gatt_char(
            LEGACY_ENCRYPTED_UUID, self._stream.xor(payload), response=True
        )
        reply = self._stream.xor(
            bytes(await self._client.read_gatt_char(LEGACY_ENCRYPTED_UUID))
        )
        return decode_response(reply if self._v2 else coap_unwrap(reply))
