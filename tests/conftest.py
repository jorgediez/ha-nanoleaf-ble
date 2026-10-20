"""Shared fixtures: a fake strip behind Home Assistant's Bluetooth API."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nanoleaf_ble.const import (
    CONF_CLIENT_ID,
    CONF_FIRMWARE,
    CONF_LOCAL_NAME,
    CONF_MODEL,
    CONF_TOKEN,
    DOMAIN,
)
from custom_components.nanoleaf_ble.lib import default_scenes
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .common import (
    ADDRESS,
    EUI,
    LOCAL_NAME,
    TOKEN,
    FakeLegacyStrip,
    StripState,
    make_ble_device,
    make_service_info,
)

TITLE = "Secretlab MAGRGB 40G8"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Let Home Assistant load the integration from custom_components/."""
    yield


def factory_scenes() -> dict[int, bytes]:
    """Return the NL62's factory scenes, plus the app's defaults."""
    defaults = default_scenes("NL62")
    scenes = {sid: defaults[244].encode(sid) for sid in (250, 251, 252, 253)}
    scenes.update({sid: scene.encode(sid) for sid, scene in defaults.items()})
    return scenes


@pytest.fixture
def strip_state() -> StripState:
    """Return the state of a Secretlab strip as it leaves the app."""
    return StripState(scenes=factory_scenes())


class Bluetooth:
    """What the tests control of Home Assistant's Bluetooth API."""

    def __init__(self, state: StripState) -> None:
        """Start with the NL62 in range."""
        self.state = state
        self.tokens = {TOKEN}
        self.service_info = make_service_info()
        self.in_range = True
        self.callbacks: list[Any] = []
        #: Clients to hand out before falling back to fresh legacy strips.
        self.queued: list[Any] = []
        self.connect_error: Exception | None = None
        self.connections = 0
        #: Connections opened while another was still up. A real strip behind
        #: a proxy breaks both links then (GATT error 131, "Database full").
        self.overlaps = 0
        self.connected: Any = None
        self.clients: list[Any] = []

    def new_strip(self) -> Any:
        """Return the strip reached over a fresh link (a new key exchange)."""
        if self.queued:
            return self.queued.pop(0)
        return FakeLegacyStrip(self.state, tokens=self.tokens)

    def advertise(self, service_info) -> None:
        """Deliver an advertisement to the registered callbacks."""
        self.service_info = service_info
        for callback in list(self.callbacks):
            callback(service_info, None)


@pytest.fixture
def bluetooth(strip_state: StripState, mock_bluetooth: None) -> Generator[Bluetooth]:
    """Patch the Bluetooth API and the connector to reach fake strips.

    Home Assistant's Bluetooth component still starts (bluetooth_adapters
    depends on it), on the harness's mocked adapters.
    """
    bt = Bluetooth(strip_state)

    def ble_device(hass, address, connectable=True):
        return make_ble_device() if bt.in_range else None

    def register(hass, callback, matcher, mode):
        bt.callbacks.append(callback)
        return lambda: bt.callbacks.remove(callback)

    async def connect(client_class, device, name, disconnected_callback=None, **kw):
        if bt.connect_error is not None:
            raise bt.connect_error
        bt.connections += 1
        if any(client.is_connected for client in bt.clients):
            bt.overlaps += 1
        fake = bt.new_strip()
        fake.disconnected_callback = disconnected_callback
        bt.connected = fake
        bt.clients.append(fake)
        return fake

    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            side_effect=ble_device,
        ),
        patch(
            "homeassistant.components.bluetooth.async_last_service_info",
            side_effect=lambda hass, address, connectable=True: (
                bt.service_info if bt.in_range else None
            ),
        ),
        patch(
            "homeassistant.components.bluetooth.async_discovered_service_info",
            side_effect=lambda hass, connectable=True: (
                [bt.service_info] if bt.in_range else []
            ),
        ),
        patch(
            "homeassistant.components.bluetooth.async_register_callback",
            side_effect=register,
        ),
        patch(
            "custom_components.nanoleaf_ble.coordinator.establish_connection",
            side_effect=connect,
        ),
        patch("custom_components.nanoleaf_ble.coordinator.RECONNECT_DELAY", 0),
        # BlueZ's cached history is read over D-Bus, which tests do not have.
        patch(
            "bluetooth_adapters.systems.linux.LinuxAdapters.history",
            new_callable=PropertyMock,
            return_value={},
        ),
        # Without this, the Bluetooth component opens a real Bluetooth socket on
        # hosts whose kernel supports it, such as GitHub's Ubuntu 26 runners, and
        # pytest-socket blocks it. Newer harnesses patch it the same way; the one
        # for 2025.3 does not.
        patch(
            "habluetooth.channels.bluez.MGMTBluetoothCtl.setup",
            AsyncMock(side_effect=OSError),
        ),
    ):
        yield bt


def entry_data(**overrides: Any) -> dict[str, Any]:
    """Return config entry data shaped exactly as the config flow creates it."""
    data = {
        CONF_ADDRESS: ADDRESS,
        CONF_TOKEN: TOKEN.hex(),
        CONF_CLIENT_ID: None,
        CONF_MODEL: "NL62",
        CONF_FIRMWARE: "1.6.49",
        CONF_LOCAL_NAME: LOCAL_NAME,
    }
    data.update(overrides)
    return data


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return the NL62's config entry."""
    return MockConfigEntry(domain=DOMAIN, title=TITLE, unique_id=EUI, data=entry_data())


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, bluetooth: Bluetooth
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration, and unload it afterwards to stop its timers."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def mock_community() -> Generator[AsyncMock]:
    """Patch the community client the integration creates."""
    client = AsyncMock()
    with patch(
        "custom_components.nanoleaf_ble.coordinator.CommunityClient",
        return_value=client,
    ):
        yield client
