"""Setting up, unloading and removing a strip."""

from __future__ import annotations

from bleak.exc import BleakError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nanoleaf_ble.const import CONF_FIRMWARE, DATA_STORE, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .common import (
    TOKEN,
    FakeLegacyStrip,
    FakeV3Strip,
    StripState,
    advertisement_bytes,
    make_service_info,
)
from .conftest import Bluetooth, entry_data


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.connected
    assert coordinator.device_information.serial_number == "N23490K1500"
    assert not coordinator.supports_colour_temperature

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not bluetooth.connected.is_connected


async def test_not_in_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.in_range = False
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_cannot_connect(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.connect_error = BleakError("out of slots")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_token_refused_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.tokens = set()
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_first_refresh_failure_disconnects(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.state.statuses["di"] = 1
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not bluetooth.connected.is_connected


async def test_default_scenes_written_once(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """Only the missing defaults, into free slots, and nothing deleted."""
    state = bluetooth.state
    for scene_id in (244, 245):
        del state.scenes[scene_id]
    state.scenes[3] = state.scenes[246]  # a user scene must survive
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert sorted(state.scenes) == [3, *range(244, 254)]
    assert hass.data[DATA_STORE].defaults_applied(config_entry.unique_id)

    # Deleted again later: not rewritten on the next start.
    del state.scenes[244]
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert 244 not in state.scenes
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_default_scenes_that_do_not_fit(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    state = bluetooth.state
    for scene_id in range(244, 250):
        del state.scenes[scene_id]
    for scene_id in range(4):  # four user scenes: room for four defaults
        state.scenes[scene_id] = state.scenes[250]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert sorted(state.scenes) == [0, 1, 2, 3, 244, 245, 246, 247, 250, 251, 252, 253]
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"default_scenes_incomplete_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders["scenes"] == "The Joker, Attack on Titan"
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_firmware_5_is_experimental(
    hass: HomeAssistant, bluetooth: Bluetooth
) -> None:
    v3 = FakeV3Strip(StripState(), tokens={TOKEN: 0x42})
    bluetooth.queued = [v3]
    bluetooth.service_info = make_service_info(advertisement_bytes("NL55", (5, 0, 1)))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Nanoleaf Essentials Lightstrip BE3A",
        unique_id=bluetooth.service_info.address,
        data=entry_data(model="NL55", firmware="5.0.1", client_id=0x42),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"experimental_firmware_{entry.entry_id}"
    )
    assert issue.translation_placeholders["issues_url"].startswith("https://")
    assert not entry.runtime_data.supports_scenes

    # Removal unpairs, over a fresh connection.
    bluetooth.queued = [FakeV3Strip(v3.state, tokens={TOKEN: 0x42})]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert v3.disconnect_messages == 1
    assert bluetooth.connected.unpaired
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"experimental_firmware_{entry.entry_id}"
    )


async def test_unpair_failure_is_only_logged(
    hass: HomeAssistant, bluetooth: Bluetooth, caplog
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="v3",
        unique_id="AA:BB:CC:DD:EE:FF",
        data=entry_data(model="NL55", firmware="5.0.1", client_id=0x42),
    )
    entry.add_to_hass(hass)
    bluetooth.connect_error = BleakError("gone")
    await hass.config_entries.async_remove(entry.entry_id)
    assert "Could not unpair" in caplog.text


async def test_legacy_removal_forgets_scene_names(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    store = hass.data[DATA_STORE]
    assert store.defaults_applied(config_entry.unique_id)
    connections = bluetooth.connections
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not store.defaults_applied(config_entry.unique_id)
    # Legacy firmware cannot unpair, so removal does not even connect.
    assert bluetooth.connections == connections


async def test_firmware_update_in_advertisement(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    data = bytearray(bluetooth.service_info.manufacturer_data[0x080B])
    data[4:7] = bytes([1, 7, 0])
    bluetooth.advertise(make_service_info(bytes(data), rssi=-71))
    await hass.async_block_till_done()
    assert init_integration.data[CONF_FIRMWARE] == "1.7.0"
    assert init_integration.runtime_data.rssi == -71


async def test_unrelated_advertisements_are_ignored(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    bluetooth.advertise(make_service_info(b"", company_id=76))
    bluetooth.advertise(make_service_info(b"NL"))
    assert init_integration.data[CONF_FIRMWARE] == "1.6.49"


async def test_v2_strip(hass: HomeAssistant, bluetooth: Bluetooth) -> None:
    bluetooth.queued = [FakeLegacyStrip(bluetooth.state, v2=True)]
    bluetooth.service_info = make_service_info(advertisement_bytes("NL68", (1, 2, 3)))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="NL68",
        unique_id=bluetooth.service_info.address,
        data=entry_data(model="NL68", firmware="1.2.3"),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.protocol == "legacy v2"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unverified_model(hass: HomeAssistant, bluetooth: Bluetooth) -> None:
    """A Matter Essentials sibling sets up like the NL68, with a repair notice."""
    bluetooth.queued = [FakeLegacyStrip(bluetooth.state, v2=True)]
    bluetooth.service_info = make_service_info(advertisement_bytes("NL53", (1, 2, 3)))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Nanoleaf Essentials NL53 BE3A",
        unique_id=bluetooth.service_info.address,
        data=entry_data(model="NL53", firmware="1.2.3"),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.protocol == "legacy v2"
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"unverified_model_{entry.entry_id}"
    )
    assert issue.translation_placeholders["model"] == "NL53"
    assert issue.translation_placeholders["device_report_url"].endswith(
        "template=device_report.yml"
    )

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"unverified_model_{entry.entry_id}")
        is None
    )


async def test_verified_model_has_no_notice(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"unverified_model_{init_integration.entry_id}"
        )
        is None
    )
