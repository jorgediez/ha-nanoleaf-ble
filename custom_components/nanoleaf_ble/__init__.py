"""The Nanoleaf Essentials (Bluetooth) integration."""

from __future__ import annotations

import logging

from bleak.exc import BleakError

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_LOCAL_NAME,
    DATA_STORE,
    DEVICE_REPORT_URL,
    DOMAIN,
    ISSUES_URL,
)
from .coordinator import (
    NanoleafConfigEntry,
    NanoleafCoordinator,
    advertisement_from_entry,
    async_connect,
    credentials_from_entry,
)
from .lib import NanoleafError
from .library import SceneStore
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Load the shared scene library and register the actions."""
    store = SceneStore(hass)
    await store.async_load()
    hass.data[DATA_STORE] = store
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NanoleafConfigEntry) -> bool:
    """Set up a strip from a config entry."""
    coordinator = NanoleafCoordinator(hass, entry, hass.data[DATA_STORE])
    await coordinator.async_start()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_disconnect()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(
        hass.data[DATA_STORE].async_add_listener(coordinator.async_update_listeners)
    )

    issue_id = f"experimental_firmware_{entry.entry_id}"
    if coordinator.ltpdu_v3:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="experimental_firmware",
            translation_placeholders={
                "name": entry.title,
                "firmware": coordinator.advertisement.firmware_version,
                # Hassfest keeps URLs out of translations.
                "issues_url": ISSUES_URL,
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    issue_id = f"unverified_model_{entry.entry_id}"
    if coordinator.advertisement.unverified:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unverified_model",
            translation_placeholders={
                "name": entry.title,
                "model": coordinator.model,
                "device_report_url": DEVICE_REPORT_URL,
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Writing up to six scenes takes a few seconds; don't hold up the start.
    entry.async_create_background_task(
        hass,
        coordinator.async_apply_default_scenes(),
        f"{DOMAIN} default scenes {entry.entry_id}",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NanoleafConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_disconnect()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: NanoleafConfigEntry) -> None:
    """Unpair from the strip where the firmware can, and forget its scene names.

    Only firmware 5 has a device-side unpair. Legacy firmware keeps accepting
    the token until the strip is factory-reset; nothing can be done about it.
    """
    ir.async_delete_issue(hass, DOMAIN, f"experimental_firmware_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, f"unverified_model_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, f"default_scenes_incomplete_{entry.entry_id}")
    address = entry.unique_id or ""
    if (store := hass.data.get(DATA_STORE)) is not None:
        store.async_remove_device(address)

    advertisement = advertisement_from_entry(dict(entry.data))
    if not advertisement.ltpdu_v3:
        return
    client = None
    try:
        client, device = await async_connect(
            hass, address, advertisement, entry.data.get(CONF_LOCAL_NAME) or address
        )
        await device.login(credentials_from_entry(dict(entry.data)))
        await device.unpair()
        _LOGGER.info("Unpaired from %s", entry.title)
    except NanoleafError as exc:
        _LOGGER.warning(
            "Could not unpair from %s (%s); its pairing stays on the device",
            entry.title,
            exc,
        )
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, OSError) as exc:
                _LOGGER.debug("disconnect failed: %s", exc)
