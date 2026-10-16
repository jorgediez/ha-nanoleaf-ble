"""NanoleafDevice end to end, against fake legacy and LTPDUv3 strips."""

from __future__ import annotations

from bleak.exc import BleakError
import pytest

from custom_components.nanoleaf_ble.lib import (
    Credentials,
    NanoleafAuthError,
    NanoleafConnectionError,
    NanoleafDevice,
    NanoleafProtocolError,
    NanoleafStripFullError,
    NanoleafTooManyPairingsError,
    NanoleafUnsupportedError,
    default_scenes,
    ltpdu_v3,
    parse_advertisement,
    parse_device_information,
)

from ..common import (
    EUI,
    LOCAL_NAME,
    NL62_MANUFACTURER_DATA,
    SERIAL,
    SETUP_CODE,
    TOKEN,
    FakeLegacyStrip,
    FakeV3Strip,
    StripState,
    advertisement_bytes,
)

NL62 = parse_advertisement(NL62_MANUFACTURER_DATA)
NL68 = parse_advertisement(advertisement_bytes("NL68", (1, 2, 3)))
NL55_V3 = parse_advertisement(advertisement_bytes("NL55", (5, 0, 1)))
NL68_V3 = parse_advertisement(advertisement_bytes("NL68", (5, 0, 1)))


async def _logged_in(
    strip=None, advertisement=NL62
) -> tuple[NanoleafDevice, FakeLegacyStrip]:
    strip = strip or FakeLegacyStrip()
    device = NanoleafDevice(strip, advertisement, name=LOCAL_NAME)
    await device.login(Credentials(TOKEN))
    return device, strip


# --------------------------------------------------------------------------- legacy


async def test_pair_and_login():
    strip = FakeLegacyStrip()
    device = NanoleafDevice(strip, NL62, name=LOCAL_NAME)
    assert device.protocol == "legacy"
    credentials = await device.pair("123-45-678")
    assert len(credentials.token) == 8
    assert credentials.client_id is None
    assert device.authenticated

    again = NanoleafDevice(FakeLegacyStrip(tokens={credentials.token}), NL62, name="x")
    await again.login(credentials)
    assert again.authenticated


async def test_wrong_setup_code_and_token():
    device = NanoleafDevice(FakeLegacyStrip(), NL62, name=LOCAL_NAME)
    with pytest.raises(NanoleafAuthError, match="setup code"):
        await device.pair("87654321")
    assert device.broken
    device = NanoleafDevice(FakeLegacyStrip(), NL62, name=LOCAL_NAME)
    with pytest.raises(NanoleafAuthError, match="token"):
        await device.login(Credentials(b"\x00" * 8))
    with pytest.raises(ValueError, match="8 digits"):
        await NanoleafDevice(FakeLegacyStrip(), NL62, name="x").pair("123")


async def test_no_more_pairings():
    device = NanoleafDevice(
        FakeLegacyStrip(accepts_pairings=False), NL62, name=LOCAL_NAME
    )
    with pytest.raises(NanoleafTooManyPairingsError):
        await device.pair(SETUP_CODE)


async def test_identify_needs_no_login():
    strip = FakeLegacyStrip()
    await NanoleafDevice(strip, NL62, name=LOCAL_NAME).identify()
    assert strip.state.identified == 1


async def test_requests_need_a_login():
    device = NanoleafDevice(FakeLegacyStrip(), NL62, name=LOCAL_NAME)
    with pytest.raises(NanoleafProtocolError, match="not logged in"):
        await device.get_state()


async def test_light_state_and_writes():
    device, strip = await _logged_in()
    state = await device.get_state()
    assert (state.on, state.brightness, state.hue, state.saturation) == (
        False,
        100,
        28,
        66,
    )
    assert not await device.supports_colour_temperature()

    await device.set_light(on=True, brightness=40, hue=240, saturation=100)
    assert (strip.state.on, strip.state.brightness, strip.state.hue) == (True, 40, 240)
    await device.set_light()  # nothing to send


async def test_colour_temperature_where_supported():
    device, _strip = await _logged_in(
        FakeLegacyStrip(StripState(colour_temperature=2700))
    )
    assert await device.supports_colour_temperature()
    await device.set_light(colour_temperature=4000)
    assert (await device.get_state(colour_temperature=True)).colour_temperature == 4000


async def test_failed_endpoint_raises():
    device, strip = await _logged_in()
    strip.state.statuses["lb/0/pb"] = 2
    with pytest.raises(NanoleafProtocolError, match="status 2"):
        await device.set_light(brightness=10)
    strip.state.statuses["lb/0/pb"] = 4
    with pytest.raises(NanoleafUnsupportedError):
        await device.get_state()


async def test_device_information():
    device, _strip = await _logged_in()
    info = await device.device_information()
    assert (info.hardware_version, info.firmware_version) == ("2.0.0", "1.6.49")
    assert (info.serial_number, info.eui) == (SERIAL, EUI)


def test_device_information_tlv_form():
    data = bytes([0x03, 3]) + b"1.0" + bytes([0x04, 2]) + b"SN" + bytes([0x0C, 2, 0, 1])
    data += bytes([0xFE, 5]) + b"5.0.1" + bytes([0x06, 2, 0xAB, 0xCD])
    info = parse_device_information(data)
    assert info.hardware_version == "1.0"
    assert info.serial_number == "SN"
    assert info.firmware_version == "5.0.1"
    assert info.eui == "abcd"
    assert info.extra == {"type_0c": "0001"}


def test_device_information_old_hardware():
    record = b"0.3.6".ljust(10, b"\x00") + b"1".ljust(8, b"\x00") + b"S" * 11 + bytes(8)
    assert parse_device_information(record).hardware_version == "1.0.4"


async def test_scenes():
    defaults = default_scenes("NL62")
    state = StripState(scenes={250: defaults[244].encode(250)})
    device, strip = await _logged_in(FakeLegacyStrip(state))

    assert await device.list_scenes() == [250]
    assert await device.get_scene(250) == defaults[244]
    await device.add_scene(3, defaults[245])
    assert sorted(strip.state.scenes) == [3, 250]

    await device.activate_scene(3)
    assert strip.state.on
    assert await device.current_scene() == 3
    await device.preview_scene(defaults[246])
    assert await device.current_scene() == 0xFF
    assert strip.state.previews[-1][0] == 0x01  # the preview ID the app uses

    await device.delete_scene(3)
    assert sorted(strip.state.scenes) == [250]
    with pytest.raises(ValueError, match="factory"):
        await device.delete_scene(250)
    await device.delete_scene(250, allow_protected=True)
    assert strip.state.scenes == {}

    strip.state.on = False
    assert await device.current_scene() == 0


async def test_strip_full():
    scene = default_scenes("NL62")[244]
    state = StripState(scenes={i: scene.encode(i) for i in range(12)})
    device, _strip = await _logged_in(FakeLegacyStrip(state))
    with pytest.raises(NanoleafStripFullError):
        await device.add_scene(13, scene)


async def test_power_loss_recovery():
    device, strip = await _logged_in()
    assert await device.get_power_loss_recovery()
    await device.set_power_loss_recovery(False)
    assert strip.state.power_loss_recovery is False
    assert not await device.get_power_loss_recovery()


async def test_dropped_link_breaks_the_session():
    device, strip = await _logged_in()
    strip.fail_next = BleakError("disconnected")
    with pytest.raises(NanoleafConnectionError, match="disconnected"):
        await device.get_state()
    assert device.broken
    with pytest.raises(NanoleafConnectionError, match="reconnect"):
        await device.get_state()


async def test_timeouts_break_the_session():
    device, strip = await _logged_in()
    strip.fail_next = TimeoutError()
    with pytest.raises(NanoleafConnectionError, match="TimeoutError"):
        await device.get_state()


async def test_legacy_has_no_unpair():
    device, _strip = await _logged_in()
    await device.end_session()  # a no-op on legacy firmware
    with pytest.raises(NanoleafUnsupportedError):
        await device.unpair()
    with pytest.raises(NanoleafProtocolError):
        await NanoleafDevice(FakeLegacyStrip(), NL62, name="x").unpair()


async def test_v2_devices_skip_coap():
    strip = FakeLegacyStrip(v2=True)
    device, strip = await _logged_in(strip, NL68)
    assert device.protocol == "legacy v2"
    await device.set_light(on=True)
    assert strip.state.on
    await device.identify()
    assert strip.state.identified == 1


# --------------------------------------------------------------------------- LTPDUv3


@pytest.mark.parametrize(
    "version", [ltpdu_v3.SESSION_3_0, ltpdu_v3.SESSION_3_1, ltpdu_v3.SESSION_3_2]
)
async def test_v3_pair_login_and_control(version):
    strip = FakeV3Strip(version=version)
    device = NanoleafDevice(strip, NL55_V3, name=LOCAL_NAME)
    assert device.ltpdu_v3
    assert not device.supports_scenes
    credentials = await device.pair(SETUP_CODE)
    assert credentials.client_id == 0x0042

    strip = FakeV3Strip(version=version, tokens={credentials.token: 0x0042})
    device = NanoleafDevice(strip, NL55_V3, name=LOCAL_NAME)
    await device.login(credentials)
    await device.set_light(on=True, brightness=40)
    assert (strip.state.on, strip.state.brightness) == (True, 40)
    assert (await device.get_state()).brightness == 40
    with pytest.raises(NanoleafUnsupportedError, match="Effects V2"):
        await device.list_scenes()

    await device.end_session()
    assert strip.disconnect_messages == 1
    await device.unpair()
    assert strip.unpaired
    assert not device.authenticated


async def test_v3_matter_uses_the_small_group():
    from custom_components.nanoleaf_ble.lib.const import SRP_N_512

    strip = FakeV3Strip(prime=SRP_N_512)
    device = NanoleafDevice(strip, NL68_V3, name=LOCAL_NAME)
    await device.login(Credentials(TOKEN, 0x0042))
    await device.identify()
    assert strip.state.identified == 1


async def test_v3_wrong_token():
    device = NanoleafDevice(FakeV3Strip(), NL55_V3, name=LOCAL_NAME)
    with pytest.raises(NanoleafAuthError):
        await device.login(Credentials(b"wrong", 0x0042))


async def test_v3_token_needs_its_client_id():
    session = ltpdu_v3.V3Session(FakeV3Strip(), username="x", matter=False, timeout=1)
    with pytest.raises(ValueError, match="client ID"):
        await session.authenticate(token=TOKEN)
    with pytest.raises(ValueError, match="required"):
        await session.authenticate()
    with pytest.raises(NanoleafProtocolError):
        await session.request(0, [])
    with pytest.raises(NanoleafProtocolError):
        await session.disconnect()


async def test_v3_silent_device_times_out():
    class Silent(FakeV3Strip):
        async def write_gatt_char(self, char, data, response=True):
            self._check()

    device = NanoleafDevice(Silent(), NL55_V3, name=LOCAL_NAME, timeout=0.05)
    with pytest.raises(NanoleafConnectionError):
        await device.login(Credentials(TOKEN, 0x0042))
