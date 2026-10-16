"""The LTPDU command language both protocol generations carry.

A request is a run of commands, each a set of TLVs with a 2-byte type and a
2-byte big-endian length: endpoint (1), method (4, left out on legacy HomeKit
devices, whose CoAP wrapper carries it) and payload (2, left out for reads).
The device answers each with the endpoint (1) and status-plus-value (3).
"""

from __future__ import annotations

from enum import IntEnum
import random
import struct

from .exceptions import NanoleafProtocolError

TLV_ENDPOINT = 0x0001
TLV_PAYLOAD = 0x0002
TLV_RESPONSE = 0x0003
TLV_METHOD = 0x0004
TLV_FRAGMENT_HEADER = 0x0006
TLV_FRAGMENT = 0x0007

#: Bytes that follow a CoAP option nibble of 13 or 14 (RFC 7252 §3.1).
_EXTENDED_SIZE = {13: 1, 14: 2}


class Method(IntEnum):
    """LTPDU request methods."""

    GET = 0
    POST = 1
    PUT = 2
    DELETE = 3


class Status(IntEnum):
    """Per-command status codes in an LTPDU response."""

    SUCCESS = 0
    CANNOT_READ = 1
    CANNOT_WRITE = 2
    INVALID_VALUE = 3
    INVALID_ENDPOINT = 4
    MALFORMED_PAYLOAD = 5
    INVALID_METHOD = 6


#: CoAP request codes, by LTPDU method.
_COAP_CODES = {Method.GET: 1, Method.POST: 2, Method.PUT: 3, Method.DELETE: 4}


def u16(value: int) -> bytes:
    """Return a value as an unsigned 16-bit big-endian integer."""
    return struct.pack(">H", value)


def encode_command(endpoint: str, value: bytes | None, method: Method | None) -> bytes:
    """Encode one LTPDU command."""
    name = endpoint.encode("ascii")
    out = struct.pack(">HH", TLV_ENDPOINT, len(name)) + name
    if method is not None:
        out += struct.pack(">HHB", TLV_METHOD, 1, method)
    if value is not None:
        out += struct.pack(">HH", TLV_PAYLOAD, len(value)) + value
    return out


def decode_response(data: bytes) -> dict[str, tuple[int, bytes]]:
    """Decode an LTPDU response into {endpoint: (status, value)}.

    A large value can arrive as a fragment header (status, count, total length)
    followed by fragments, which are joined.
    """
    tlvs: list[tuple[int, bytes]] = []
    offset = 0
    while offset + 4 <= len(data):
        tlv_type, length = struct.unpack_from(">HH", data, offset)
        end = offset + 4 + length
        if end > len(data):
            raise NanoleafProtocolError(
                f"truncated response TLV {tlv_type:#06x} at {offset}"
            )
        tlvs.append((tlv_type, data[offset + 4 : end]))
        offset = end

    result: dict[str, tuple[int, bytes]] = {}
    endpoint: str | None = None
    fragments: bytearray | None = None
    fragment_status = 0
    for tlv_type, value in tlvs:
        if tlv_type == TLV_ENDPOINT:
            endpoint = value.decode("ascii", errors="replace")
            fragments = None
        elif endpoint is None:
            continue
        elif tlv_type == TLV_RESPONSE and value:
            result[endpoint] = (value[0], value[1:])
        elif tlv_type == TLV_FRAGMENT_HEADER and value:
            fragment_status, fragments = value[0], bytearray()
            result[endpoint] = (fragment_status, b"")
        elif tlv_type == TLV_FRAGMENT and fragments is not None:
            fragments.extend(value)
            result[endpoint] = (fragment_status, bytes(fragments))
    return result


def coap_wrap(method: Method, commands: bytes, *, public: bool = False) -> bytes:
    """Wrap commands in the minimal CoAP message legacy HomeKit devices expect."""
    message_id = random.randrange(256, 65536)
    path = b"nlpublic" if public else b"nlltpdu"
    header = bytes(
        [
            0x42,  # CoAP version 1, confirmable, 2-byte token
            _COAP_CODES[method],
            message_id >> 8,
            message_id & 0xFF,
            random.randrange(256),
            random.randrange(256),
            0xB0 | len(path),  # option 11 (Uri-Path)
        ]
    )
    return header + path + b"\xff" + commands


def coap_unwrap(reply: bytes) -> bytes:
    """Return the payload of a legacy HomeKit CoAP reply.

    The NL62 always answers with a 2-byte token and no options, so the payload
    starts at byte 7. Options are skipped all the same, in case another model
    sends some.
    """
    if len(reply) < 4:
        raise NanoleafProtocolError(f"CoAP reply too short ({len(reply)} bytes)")
    offset = 4 + (reply[0] & 0x0F)
    while offset < len(reply) and reply[offset] != 0xFF:
        delta, length = reply[offset] >> 4, reply[offset] & 0x0F
        offset += 1
        # Extended option delta and length (RFC 7252 §3.1); the delta's value
        # itself is of no interest, only how many bytes it takes.
        offset += _EXTENDED_SIZE.get(delta, 0)
        if length in _EXTENDED_SIZE:
            size = _EXTENDED_SIZE[length]
            base = 13 if length == 13 else 269
            length = int.from_bytes(reply[offset : offset + size], "big") + base
            offset += size
        offset += length
    if offset >= len(reply):
        # No payload marker: an empty reply.
        return b""
    return reply[offset + 1 :]
