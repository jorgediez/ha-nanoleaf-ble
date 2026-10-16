"""Advertisements, LTPDU commands, CoAP, and the crypto both protocols use.

The crypto checks come from the protocol's offline self-test: each is compared
with an independent computation of the app's algorithm.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import struct

import pytest

from custom_components.nanoleaf_ble.lib import commands, const, ltpdu_v3
from custom_components.nanoleaf_ble.lib.advertisement import parse_advertisement
from custom_components.nanoleaf_ble.lib.exceptions import NanoleafProtocolError

from ..common import EUI, NL62_MANUFACTURER_DATA, advertisement_bytes

N_2048_APP = (
    "21766174458617435773191008891802753781907668374255538511144643224689886235383840"
    "95721090901308605640157139971723580726658164960647214841029141336415219736447718"
    "08873956554837381150726774022351017625219015698207402931495296204193332662620734"
    "71054548368736039519702486226506248861060256971802984953561121442680157668000761"
    "42998822245709041387397397017192709399211475176516806361476111961547623342209644"
    "27831179712363716473338714143358957734746673089670508070055093204247996784170368"
    "67928316761272274230314067548291133582479583061439577559347101961771406173684378"
    "522703483495337037655006751328447510550299250924469288819"
)
N_512_APP = (
    "11229916235553671384460957561912561881437004243772597012148068303101566041140271"
    "835868089403470930092847368898606170616536123073256223246414212750058495475"
)

LIB = Path(__file__).resolve().parents[2] / "custom_components" / "nanoleaf_ble" / "lib"


def test_lib_has_no_home_assistant_imports():
    """The one rule that keeps moving lib/ to its own package cheap."""
    offenders = [
        path.name
        for path in LIB.glob("*.py")
        if "homeassistant" in path.read_text(encoding="utf-8")
    ]
    assert not offenders


def test_lib_uses_relative_imports_only():
    offenders = [
        path.name
        for path in LIB.glob("*.py")
        if "custom_components" in path.read_text(encoding="utf-8")
    ]
    assert not offenders


# --------------------------------------------------------------------------- advertisement


def test_partner_advertisement_names_the_real_model():
    info = parse_advertisement(NL62_MANUFACTURER_DATA)
    assert info.advertised_model == "NLM0"
    assert info.model == "NL62"
    assert info.firmware_version == "1.6.49"
    assert info.hardware == "2.0.0"
    assert info.paired is False
    assert info.device_id == EUI
    assert info.supported
    assert not info.ltpdu_v3
    assert not info.v2
    assert info.product_name == "Secretlab MAGRGB"


def test_regular_advertisement():
    info = parse_advertisement(advertisement_bytes("NL68", (5, 1, 0)))
    assert info.model == "NL68"
    assert info.pair_id == "1234"
    assert info.ltpdu_v3
    assert info.matter
    assert info.v2


def test_old_hardware_revision_is_renamed():
    data = bytearray(advertisement_bytes("NL55", (1, 2, 3)))
    data[7:10] = bytes([0, 3, 6])
    assert parse_advertisement(bytes(data)).hardware == "1.0.4"


def test_short_advertisement_is_rejected():
    with pytest.raises(ValueError, match="too short"):
        parse_advertisement(b"NL55")


def test_ten_byte_advertisement_has_no_flags():
    info = parse_advertisement(b"NL55" + bytes([1, 0, 0, 1, 0, 0]))
    assert info.paired is None
    assert info.device_id is None


@pytest.mark.parametrize(
    ("model", "supported", "v2"),
    [
        ("NL45", True, False),
        ("NL71", False, True),
        ("NL43", False, True),
        ("NL22", False, False),
    ],
)
def test_model_groups(model, supported, v2):
    info = parse_advertisement(advertisement_bytes(model, (1, 0, 0)))
    assert info.supported is supported
    assert info.v2 is v2
    if not supported:
        assert info.product_name == f"Nanoleaf {model}"


# --------------------------------------------------------------------------- commands


def test_encode_command_like_the_app():
    """The example from the protocol doc: set brightness to 50 % on v3."""
    encoded = commands.encode_command("lb/0/pb", commands.u16(50), commands.Method.PUT)
    assert (
        encoded.hex() == "00010007" + b"lb/0/pb".hex() + "0004000102" + "000200020032"
    )


def test_decode_response():
    data = (
        struct.pack(">HH", 1, 7) + b"lb/0/oo" + struct.pack(">HH", 3, 2) + b"\x00\x01"
    )
    assert commands.decode_response(data) == {"lb/0/oo": (0, b"\x01")}


def test_decode_fragmented_response():
    data = (
        struct.pack(">HH", 1, 2)
        + b"di"
        + struct.pack(">HH", 6, 6)
        + bytes([0, 2, 0, 0, 0, 4])
        + struct.pack(">HH", 7, 2)
        + b"ab"
        + struct.pack(">HH", 7, 2)
        + b"cd"
    )
    assert commands.decode_response(data) == {"di": (0, b"abcd")}


def test_decode_ignores_values_without_endpoint():
    data = struct.pack(">HH", 3, 1) + b"\x00"
    assert commands.decode_response(data) == {}


def test_decode_truncated_response():
    with pytest.raises(NanoleafProtocolError, match="truncated"):
        commands.decode_response(struct.pack(">HH", 1, 9) + b"lb")


def test_coap_round_trip():
    wrapped = commands.coap_wrap(commands.Method.PUT, b"payload")
    assert wrapped[0] == 0x42
    assert wrapped[1] == 3
    assert wrapped[6] == 0xB7
    assert wrapped[7:14] == b"nlltpdu"
    # A reply carries the same prefix shape: header, token, payload marker.
    reply = bytes([0x62, 0x44, *wrapped[2:6], 0xFF]) + b"answer"
    assert commands.coap_unwrap(reply) == b"answer"


def test_coap_public_path():
    assert b"nlpublic" in commands.coap_wrap(commands.Method.POST, b"", public=True)


def test_coap_unwrap_skips_options():
    # Token length 0; one option of length 2, one with an extended length.
    reply = bytes([0x60, 0x44, 0, 1, 0xB2]) + b"ab" + bytes([0x1D, 1]) + b"x" * 14
    assert commands.coap_unwrap(reply + b"\xffdata") == b"data"
    wide = bytes([0x60, 0x44, 0, 1, 0xBE, 0, 0]) + b"y" * 269
    assert commands.coap_unwrap(wide + b"\xffz") == b"z"


def test_coap_unwrap_without_payload():
    assert commands.coap_unwrap(bytes([0x60, 0x44, 0, 1])) == b""
    with pytest.raises(NanoleafProtocolError):
        commands.coap_unwrap(b"\x60")


# --------------------------------------------------------------------------- crypto


def test_srp_primes_match_the_app():
    """The decimal constants as they appear in the app."""
    assert int(N_2048_APP) == const.SRP_N_2048
    assert int(N_512_APP) == const.SRP_N_512


@pytest.mark.parametrize("prime", [const.SRP_N_2048, const.SRP_N_512])
def test_srp_matches_an_independent_server(prime):
    username, password = "Nanoleaf LS 1A2B", bytes([1, 2, 3, 4, 5, 6, 7, 8])
    H, ib = ltpdu_v3.sha256, ltpdu_v3.int_bytes
    salt = os.urandom(16)
    s = int.from_bytes(salt, "big")
    x = int.from_bytes(H(ib(s), H(username.encode() + b":" + password)), "big")
    verifier = pow(2, x, prime)
    k = int.from_bytes(H(ib(prime), ib(2)), "big")
    b = secrets.randbits(256)
    server_public = (k * verifier + pow(2, b, prime)) % prime

    client = ltpdu_v3.SRPClient(username, password, prime)
    client.process_challenge(salt, ib(server_public))
    u = int.from_bytes(H(ib(client.public_key), ib(server_public)), "big")
    shared = pow(client.public_key * pow(verifier, u, prime), b, prime)
    assert H(ib(shared)) == client.key
    assert len(client.session_key) == 32


def _crc_app(data: bytes) -> bytes:
    """The app's CRC loop, with Java's signed bytes."""
    crc = 0
    for byte in data:
        signed = byte - 256 if byte > 127 else byte
        crc ^= signed << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 4129) if (32768 & crc) else (crc << 1)
            crc &= 0xFFFFFFFF
    return bytes([(crc >> 8) & 255, crc & 255])


def test_crc_matches_the_app():
    for _ in range(200):
        data = os.urandom(secrets.randbelow(59) + 1)
        assert _crc_app(data) == ltpdu_v3.crc16_xmodem(data)
    assert ltpdu_v3.crc16_xmodem(b"123456789").hex() == "31c3"


@pytest.mark.parametrize(
    "version", [ltpdu_v3.SESSION_3_0, ltpdu_v3.SESSION_3_1, ltpdu_v3.SESSION_3_2]
)
def test_session_ciphers_round_trip(version):
    key = os.urandom(32)
    app, device = ltpdu_v3.V3Cipher(version, key), ltpdu_v3.V3Cipher(version, key)
    for _ in range(5):
        message = os.urandom(20)
        assert device.decrypt(app.encrypt(message)) == message
        assert app.decrypt(device.encrypt(b"reply")) == b"reply"


def test_frames():
    frame = ltpdu_v3.encode_request(0x1234, 0xFFFF, 2, 1, b"\xaa\xbb")
    assert frame.hex() == "010112 34ffff0201 00000002aabb".replace(" ", "")
    response = ltpdu_v3.decode_frame(bytes.fromhex("0102123400420000000000017f"))
    assert (response.type, response.message_id, response.client_id) == (2, 0x1234, 0x42)
    assert response.data == b"\x7f"
    event = ltpdu_v3.decode_frame(bytes.fromhex("010312340042000000090000000155"))
    assert (event.type, event.data) == (3, b"\x55")
    ack = ltpdu_v3.decode_frame(bytes.fromhex("01fe12340042"))
    assert ack.type == ltpdu_v3.MSG_ACK
    with pytest.raises(NanoleafProtocolError):
        ltpdu_v3.decode_frame(b"\x01")


def test_auth_tlvs_are_sorted_by_type():
    encoded = ltpdu_v3.encode_auth_tlvs([(3, b"\x01\x02"), (1, b"\x01"), (7, b"\x02")])
    assert encoded.hex() == "01000101" + "0300020102" + "07000102"
    assert ltpdu_v3.decode_auth_tlvs(encoded) == {
        1: b"\x01",
        3: b"\x01\x02",
        7: b"\x02",
    }
