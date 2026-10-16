"""Parse the manufacturer data Nanoleaf devices advertise (company 0x080B)."""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    LTPDU_V3_FIRMWARE,
    MATTER_ESSENTIALS_MODELS,
    ONE_D_MODEL_PREFIXES,
    PARTNER_MODEL_PREFIX,
    SENSE_PLUS_MODELS,
    SUPPORTED_MODELS,
)

#: The app ignores manufacturer data shorter than this.
MIN_LENGTH = 10
#: Offset of the device ID (EUI-64).
_DEVICE_ID = slice(11, 19)
#: Partner devices (NLM*) carry their real model here instead of a pair ID.
_PARTNER_SUB_MODEL_OFFSET = 19
_SUB_MODEL_OFFSET = 21


def _text(data: bytes) -> str:
    return data.decode("ascii", errors="replace").strip("\x00 ")


@dataclass(frozen=True, slots=True)
class AdvertisementInfo:
    """What a Nanoleaf advertisement says about the device."""

    advertised_model: str
    firmware: tuple[int, int, int]
    hardware: str
    paired: bool | None = None
    device_id: str | None = None
    pair_id: str | None = None
    sub_model: str | None = None

    @property
    def model(self) -> str:
        """Return the real model: partner and 1D devices name it in the sub-model."""
        return self.sub_model or self.advertised_model

    @property
    def firmware_version(self) -> str:
        """Return the firmware as major.minor.patch."""
        return ".".join(str(part) for part in self.firmware)

    @property
    def ltpdu_v3(self) -> bool:
        """Return whether the device speaks LTPDUv3 rather than the legacy protocol."""
        return self.firmware >= LTPDU_V3_FIRMWARE

    @property
    def matter(self) -> bool:
        """Return whether this is a Matter Essentials device (512-bit SRP group)."""
        return self.model in MATTER_ESSENTIALS_MODELS

    @property
    def v2(self) -> bool:
        """Return whether the legacy protocol uses the "v2" variant.

        The app's isMatterDevice(): Matter Essentials, 1D and Sense+ use the
        binary key salts and send plain LTPDU (with the method TLV) instead of
        wrapping it in CoAP.
        """
        model = self.model
        return (
            model in MATTER_ESSENTIALS_MODELS
            or model in SENSE_PLUS_MODELS
            or model.startswith(ONE_D_MODEL_PREFIXES)
        )

    @property
    def supported(self) -> bool:
        """Return whether this library is meant to drive the model."""
        return self.model in SUPPORTED_MODELS

    @property
    def product_name(self) -> str:
        """Return the product name the app shows, or the model code."""
        return SUPPORTED_MODELS.get(self.model, f"Nanoleaf {self.model}")


def parse_advertisement(data: bytes) -> AdvertisementInfo:
    """Decode manufacturer data, after the 2-byte company ID.

    Raise ValueError for data too short to be a Nanoleaf advertisement.
    """
    if len(data) < MIN_LENGTH:
        raise ValueError(f"manufacturer data too short ({len(data)} bytes)")
    model = _text(data[0:4])
    hardware = ".".join(str(part) for part in data[7:10])
    if hardware == "0.3.6":
        # The app shows this hardware revision as 1.0.4.
        hardware = "1.0.4"
    info: dict = {
        "advertised_model": model,
        "firmware": (data[4], data[5], data[6]),
        "hardware": hardware,
    }
    if len(data) > MIN_LENGTH:
        info["paired"] = bool(data[10] & 0x02)
    if len(data) >= _DEVICE_ID.stop:
        info["device_id"] = data[_DEVICE_ID].hex()
    if model.startswith(PARTNER_MODEL_PREFIX):
        info["sub_model"] = _text(data[_PARTNER_SUB_MODEL_OFFSET:]) or None
    else:
        if len(data) >= _SUB_MODEL_OFFSET:
            info["pair_id"] = data[_PARTNER_SUB_MODEL_OFFSET:_SUB_MODEL_OFFSET].hex()
        info["sub_model"] = _text(data[_SUB_MODEL_OFFSET:]) or None
    return AdvertisementInfo(**info)
