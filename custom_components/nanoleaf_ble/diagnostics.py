"""Diagnostics for Nanoleaf Essentials (Bluetooth).

Meant for reports from models other than the NL62 this was built on, so it
carries what tells models and firmware apart: the advertised model, firmware
and protocol, what the strip answered to the capability probes, and the state
the last poll read.

It leaves out what identifies a user or would let someone control the strip:
the Bluetooth address, auth token, client ID, serial number and EUI are
redacted. Scene names are left out (the user chose them); the scene IDs and
the size of the library are enough to debug with.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_CLIENT_ID, CONF_LOCAL_NAME, CONF_TOKEN
from .coordinator import NanoleafConfigEntry

TO_REDACT = {CONF_ADDRESS, CONF_TOKEN, CONF_CLIENT_ID, CONF_LOCAL_NAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NanoleafConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    advertisement = coordinator.advertisement
    info = coordinator.device_information
    data = coordinator.data
    error = coordinator.last_exception

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "advertisement": {
            "advertised_model": advertisement.advertised_model,
            "model": advertisement.model,
            "firmware": advertisement.firmware_version,
            "hardware": advertisement.hardware,
            "paired_flag": advertisement.paired,
            "ltpdu_v3": advertisement.ltpdu_v3,
            "v2": advertisement.v2,
        },
        "device_information": None
        if info is None
        else {
            "hardware_version": info.hardware_version,
            "firmware_version": info.firmware_version,
            # Other fields by name only: some are identifiers.
            "other_fields": sorted(info.extra),
        },
        "capabilities": {
            "protocol": coordinator.protocol,
            "scenes": coordinator.supports_scenes,
            "colour_temperature": coordinator.supports_colour_temperature,
            "power_loss_recovery": coordinator.supports_power_loss_recovery,
        },
        "connection": {
            "connected": coordinator.connected,
            "rssi": coordinator.rssi,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            # str(), not repr(): a translated error's repr is only its key.
            "last_exception": f"{type(error).__name__}: {error}" if error else None,
        },
        "state": None
        if data is None
        else {
            "on": data.light.on,
            "brightness": data.light.brightness,
            "hue": data.light.hue,
            "saturation": data.light.saturation,
            "colour_temperature": data.light.colour_temperature,
            "current_scene": data.current_scene,
            "scene_ids": list(data.scene_ids),
            "free_slots": data.free_slots,
            "power_loss_recovery": data.power_loss_recovery,
            "named_scene_ids": sorted(
                coordinator.store.strip_scenes(coordinator.address)
            ),
        },
        "library_size": len(coordinator.store.library),
    }
