"""Config flow for Nanoleaf Essentials (Bluetooth).

Strips are found through Bluetooth discovery (or picked from those Home
Assistant has heard), then paired with the 8-digit setup code printed on the
strip. A collapsed section takes an existing auth token instead, for strips
already paired elsewhere (for example with scripts/nanoleaf_cli.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_ADVANCED,
    CONF_CLIENT_ID,
    CONF_FIRMWARE,
    CONF_LOCAL_NAME,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_SETUP_CODE,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)
from .coordinator import (
    NanoleafCoordinator,
    advertisement_from_entry,
    async_probe,
    unique_id_for,
)
from .lib import (
    NANOLEAF_COMPANY_ID,
    AdvertisementInfo,
    Credentials,
    NanoleafAuthError,
    NanoleafError,
    NanoleafTooManyPairingsError,
    parse_advertisement,
)

_LOGGER = logging.getLogger(__name__)

_SETUP_CODE_DIGITS = 8


def _advertisement(
    info: bluetooth.BluetoothServiceInfoBleak,
) -> AdvertisementInfo | None:
    data = info.manufacturer_data.get(NANOLEAF_COMPANY_ID)
    if data is None:
        return None
    try:
        return parse_advertisement(data)
    except ValueError:
        return None


def _title(
    advertisement: AdvertisementInfo, info: bluetooth.BluetoothServiceInfoBleak
) -> str:
    """Name the strip after its product, plus the tag at the end of its name.

    The strip advertises a name such as "Secretlab MAGRGB 40G8"; the tag at its
    end survives a factory reset, while the address does not (verified on the
    NL62). Without a name, the end of the address stands in.
    """
    words = (info.name or "").split()
    if words and info.name != info.address:
        tag = words[-1]
    else:
        tag = info.address.replace(":", "")[-4:]
    return f"{advertisement.product_name} {tag}"


def _pair_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_SETUP_CODE): TextSelector(),
            vol.Required(CONF_ADVANCED): section(
                vol.Schema(
                    {
                        vol.Optional(CONF_TOKEN): TextSelector(),
                        vol.Optional(CONF_CLIENT_ID): NumberSelector(
                            NumberSelectorConfig(
                                min=0, max=65535, mode=NumberSelectorMode.BOX
                            )
                        ),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _parse_input(
    user_input: dict[str, Any], ltpdu_v3: bool
) -> tuple[str | None, Credentials | None, str | None]:
    """Return (setup code, credentials, error key) from the pair form."""
    advanced = user_input.get(CONF_ADVANCED) or {}
    token = "".join(str(advanced.get(CONF_TOKEN) or "").split()).lower()
    if token:
        try:
            raw = bytes.fromhex(token)
        except ValueError:
            return None, None, "invalid_token_format"
        if not raw:
            return None, None, "invalid_token_format"
        client_id = advanced.get(CONF_CLIENT_ID)
        if ltpdu_v3 and client_id is None:
            return None, None, "client_id_required"
        return None, Credentials(raw, int(client_id) if ltpdu_v3 else None), None
    code = "".join(
        ch for ch in str(user_input.get(CONF_SETUP_CODE) or "") if ch.isdigit()
    )
    if not user_input.get(CONF_SETUP_CODE):
        return None, None, "code_required"
    if len(code) != _SETUP_CODE_DIGITS:
        return None, None, "invalid_setup_code"
    return code, None, None


def _error_key(exc: NanoleafError, with_token: bool) -> str:
    if isinstance(exc, NanoleafTooManyPairingsError):
        return "too_many_pairings"
    if isinstance(exc, NanoleafAuthError):
        return "invalid_token" if with_token else "invalid_auth"
    return "cannot_connect"


def _loaded_coordinator(entry: ConfigEntry) -> NanoleafCoordinator | None:
    """Return the entry's coordinator while it holds the strip's link."""
    if entry.state is ConfigEntryState.LOADED:
        return entry.runtime_data
    return None


class NanoleafConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nanoleaf Essentials (Bluetooth)."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}
        self._service_info: bluetooth.BluetoothServiceInfoBleak | None = None
        self._advertisement: AdvertisementInfo | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NanoleafOptionsFlow:
        """Return the options flow."""
        return NanoleafOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a strip found by Bluetooth discovery."""
        advertisement = _advertisement(discovery_info)
        if advertisement is None or not advertisement.supported:
            return self.async_abort(reason="not_supported")
        await self._async_set_strip(discovery_info, advertisement)
        self._service_info = discovery_info
        self._advertisement = advertisement
        self.context["title_placeholders"] = {
            "name": _title(advertisement, discovery_info)
        }
        return await self.async_step_pair()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the strips Home Assistant has heard."""
        if user_input is not None:
            info = self._discovered[user_input[CONF_ADDRESS]]
            advertisement = _advertisement(info)
            assert advertisement is not None  # noqa: S101 - listed only when parsed
            await self._async_set_strip(info, advertisement, raise_on_progress=False)
            self._service_info = info
            self._advertisement = advertisement
            return await self.async_step_pair()

        configured = self._async_current_ids(include_ignore=False)
        for info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            advertisement = _advertisement(info)
            if (
                advertisement is not None
                and advertisement.supported
                and unique_id_for(advertisement, info.address) not in configured
            ):
                self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{_title(_advertisement(info), info)} ({address})"  # type: ignore[arg-type]
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def _async_set_strip(
        self,
        info: bluetooth.BluetoothServiceInfoBleak,
        advertisement: AdvertisementInfo,
        *,
        raise_on_progress: bool = True,
    ) -> None:
        """Set the unique id; abort if the strip is set up already.

        A strip set up before keeps its entry even at a new address (a factory
        reset gives it one): the entry follows it and reloads, and asks to pair
        again if the strip no longer knows the token.
        """
        await self.async_set_unique_id(
            unique_id_for(advertisement, info.address),
            raise_on_progress=raise_on_progress,
        )
        self._abort_if_unique_id_configured(
            updates={CONF_ADDRESS: info.address, CONF_LOCAL_NAME: info.name}
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair with the setup code, or log in with an existing token."""
        assert self._service_info is not None  # noqa: S101 - set by the steps before
        assert self._advertisement is not None  # noqa: S101 - set by the steps before
        info, advertisement = self._service_info, self._advertisement
        errors: dict[str, str] = {}
        if user_input is not None:
            code, credentials, error = _parse_input(user_input, advertisement.ltpdu_v3)
            if error is None:
                try:
                    credentials = await async_probe(
                        self.hass,
                        info.address,
                        advertisement,
                        info.name,
                        credentials or code or "",
                    )
                except NanoleafError as exc:
                    _LOGGER.debug("pairing with %s failed: %s", info.address, exc)
                    error = _error_key(exc, code is None)
            if error is None and credentials is not None:
                return self.async_create_entry(
                    title=_title(advertisement, info),
                    data={
                        CONF_ADDRESS: info.address,
                        CONF_TOKEN: credentials.token.hex(),
                        CONF_CLIENT_ID: credentials.client_id,
                        CONF_MODEL: advertisement.model,
                        CONF_FIRMWARE: advertisement.firmware_version,
                        CONF_LOCAL_NAME: info.name,
                    },
                )
            errors["base"] = error or "unknown"

        return self.async_show_form(
            step_id="pair",
            data_schema=self.add_suggested_values_to_schema(
                _pair_schema(), user_input or {}
            ),
            errors=errors,
            description_placeholders={
                "name": _title(advertisement, info),
                "firmware": advertisement.firmware_version,
            },
        )

    async def _async_step_credentials(
        self,
        step_id: str,
        entry: ConfigEntry,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Shared by reauth and reconfigure: pair again or enter a token."""
        address = entry.data[CONF_ADDRESS]
        advertisement = advertisement_from_entry(dict(entry.data))
        unique_id = entry.unique_id
        last = bluetooth.async_last_service_info(self.hass, address, connectable=True)
        if last is not None and (fresh := _advertisement(last)) is not None:
            advertisement = fresh
            unique_id = unique_id_for(fresh, address)
        local_name = (last.name if last else None) or entry.data.get(
            CONF_LOCAL_NAME, address
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            code, credentials, error = _parse_input(user_input, advertisement.ltpdu_v3)
            if error is None:
                secret = credentials or code or ""
                try:
                    if (coordinator := _loaded_coordinator(entry)) is not None:
                        credentials = await coordinator.async_probe(secret)
                    else:
                        credentials = await async_probe(
                            self.hass, address, advertisement, local_name, secret
                        )
                except NanoleafError as exc:
                    _LOGGER.debug("pairing with %s failed: %s", address, exc)
                    error = _error_key(exc, code is None)
            if error is None and credentials is not None:
                # The strip at the entry's address must still be the same one.
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_TOKEN: credentials.token.hex(),
                        CONF_CLIENT_ID: credentials.client_id,
                        CONF_FIRMWARE: advertisement.firmware_version,
                        CONF_LOCAL_NAME: local_name,
                    },
                )
            errors["base"] = error or "unknown"

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _pair_schema(), user_input or {}
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for new credentials: the strip refused the token (factory reset?)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again."""
        return await self._async_step_credentials(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again, or switch to another token."""
        return await self._async_step_credentials(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )


class NanoleafOptionsFlow(OptionsFlow):
    """Options: the poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set how often the strip is polled for changes made elsewhere."""
        if user_input is not None:
            interval = int(user_input[CONF_POLL_INTERVAL])
            # Applied in place: a reload would drop the strip's link for nothing.
            if (coordinator := _loaded_coordinator(self.config_entry)) is not None:
                coordinator.update_interval = timedelta(seconds=interval)
            return self.async_create_entry(data={CONF_POLL_INTERVAL: interval})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_POLL_INTERVAL): NumberSelector(
                            NumberSelectorConfig(
                                min=MIN_POLL_INTERVAL,
                                max=MAX_POLL_INTERVAL,
                                step=1,
                                unit_of_measurement="s",
                                mode=NumberSelectorMode.BOX,
                            )
                        )
                    }
                ),
                {
                    CONF_POLL_INTERVAL: self.config_entry.options.get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    )
                },
            ),
        )
