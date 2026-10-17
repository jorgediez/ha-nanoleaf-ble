"""The config flow: discovery, pairing, tokens, reauth, reconfigure, options."""

from __future__ import annotations

from bleak.exc import BleakError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nanoleaf_ble.const import (
    CONF_ADVANCED,
    CONF_CLIENT_ID,
    CONF_POLL_INTERVAL,
    CONF_SETUP_CODE,
    CONF_TOKEN,
    DOMAIN,
)
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .common import (
    ADDRESS,
    TOKEN,
    FakeLegacyStrip,
    FakeV3Strip,
    StripState,
    advertisement_bytes,
    make_service_info,
)
from .conftest import TITLE, Bluetooth, entry_data


async def _discover(hass: HomeAssistant, bluetooth: Bluetooth):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=bluetooth.service_info
    )


async def test_discovery_and_pairing(hass: HomeAssistant, bluetooth: Bluetooth) -> None:
    result = await _discover(hass, bluetooth)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"
    assert result["description_placeholders"]["firmware"] == "1.6.49"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "123-45-678", CONF_ADVANCED: {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["result"].unique_id == ADDRESS
    data = result["data"]
    assert data[CONF_ADDRESS] == ADDRESS
    assert bytes.fromhex(data[CONF_TOKEN]) in bluetooth.tokens
    assert data == entry_data(token=data[CONF_TOKEN])
    # The probe disconnects; the entry then opens its own connection.
    assert not bluetooth.clients[0].is_connected
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_existing_token(hass: HomeAssistant, bluetooth: Bluetooth) -> None:
    result = await _discover(hass, bluetooth)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: TOKEN.hex().upper()}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == TOKEN.hex()
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


@pytest.mark.parametrize(
    ("user_input", "error"),
    [
        ({CONF_ADVANCED: {}}, "code_required"),
        ({CONF_SETUP_CODE: "1234", CONF_ADVANCED: {}}, "invalid_setup_code"),
        ({CONF_SETUP_CODE: "87654321", CONF_ADVANCED: {}}, "invalid_auth"),
        ({CONF_ADVANCED: {CONF_TOKEN: "xyz"}}, "invalid_token_format"),
        ({CONF_ADVANCED: {CONF_TOKEN: "00"}}, "invalid_token"),
    ],
)
async def test_pairing_errors(
    hass: HomeAssistant, bluetooth: Bluetooth, user_input, error
) -> None:
    result = await _discover(hass, bluetooth)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_too_many_pairings(hass: HomeAssistant, bluetooth: Bluetooth) -> None:
    bluetooth.queued = [FakeLegacyStrip(bluetooth.state, accepts_pairings=False)]
    result = await _discover(hass, bluetooth)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "12345678", CONF_ADVANCED: {}}
    )
    assert result["errors"] == {"base": "too_many_pairings"}


async def test_cannot_connect_then_recover(
    hass: HomeAssistant, bluetooth: Bluetooth
) -> None:
    result = await _discover(hass, bluetooth)
    bluetooth.connect_error = BleakError("no slots")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "12345678", CONF_ADVANCED: {}}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    bluetooth.connect_error = None
    bluetooth.in_range = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "12345678", CONF_ADVANCED: {}}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unsupported_model(hass: HomeAssistant, bluetooth: Bluetooth) -> None:
    for data in (advertisement_bytes("NL71", (2, 0, 10)), b"NL"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_BLUETOOTH},
            data=make_service_info(data),
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "not_supported"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=make_service_info(b"", company_id=76),
    )
    assert result["reason"] == "not_supported"


async def test_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    result = await _discover(hass, bluetooth)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_firmware_5_needs_the_client_id(
    hass: HomeAssistant, bluetooth: Bluetooth
) -> None:
    bluetooth.service_info = make_service_info(advertisement_bytes("NL55", (5, 0, 1)))
    bluetooth.queued = [FakeV3Strip(StripState(), tokens={TOKEN: 7})]
    result = await _discover(hass, bluetooth)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: TOKEN.hex()}}
    )
    assert result["errors"] == {"base": "client_id_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: TOKEN.hex(), CONF_CLIENT_ID: 7}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLIENT_ID] == 7
    assert result["title"] == "Nanoleaf Essentials Lightstrip BE3A"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_user_step_picks_a_heard_strip(
    hass: HomeAssistant, bluetooth: Bluetooth
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "12345678", CONF_ADVANCED: {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_user_step_without_strips(
    hass: HomeAssistant, bluetooth: Bluetooth
) -> None:
    bluetooth.in_range = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_skips_configured_strips(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["reason"] == "no_devices_found"


async def test_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "11111111", CONF_ADVANCED: {}}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "12345678", CONF_ADVANCED: {}}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] != TOKEN.hex()
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_reconfigure(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    other = bytes.fromhex("aabbccdd")
    bluetooth.tokens.add(other)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    bluetooth.connect_error = BleakError("busy")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: other.hex()}}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    bluetooth.connect_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: other.hex()}}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_TOKEN] == other.hex()
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_reconfigure_while_loaded_uses_one_link(
    hass: HomeAssistant, init_integration: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    """The check closes the entry's link first: two at once break both."""
    coordinator = init_integration.runtime_data
    result = await init_integration.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: "0011223344556677"}}
    )
    assert result["errors"] == {"base": "invalid_token"}
    assert bluetooth.overlaps == 0
    # After a refused check the entry reconnects by itself.
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert coordinator.connected

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADVANCED: {CONF_TOKEN: TOKEN.hex()}}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert bluetooth.overlaps == 0
    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.runtime_data.connected


async def test_reconfigure_out_of_range_uses_stored_details(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    bluetooth.in_range = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SETUP_CODE: "12345678", CONF_ADVANCED: {}}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert init_integration.options == {CONF_POLL_INTERVAL: 30}
    assert init_integration.runtime_data.update_interval.total_seconds() == 30
