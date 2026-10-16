"""Errors raised by the Nanoleaf Bluetooth library."""

from __future__ import annotations


class NanoleafError(Exception):
    """Base class for every error this library raises."""


class NanoleafConnectionError(NanoleafError):
    """The link failed, or the device stopped answering.

    The session is unusable afterwards: both protocols share one cipher state
    between the directions, so a lost message cannot be recovered from.
    Reconnect and log in again.
    """


class NanoleafAuthError(NanoleafError):
    """The device refused the setup code or the auth token."""


class NanoleafTooManyPairingsError(NanoleafAuthError):
    """The device accepts no more controllers (legacy reply 0x86)."""


class NanoleafProtocolError(NanoleafError):
    """The device answered, but not with what the request expected."""


class NanoleafUnsupportedError(NanoleafError):
    """The device or its firmware does not support the request."""


class NanoleafStripFullError(NanoleafError):
    """The device refused to store another scene (12 at most, built-ins included)."""


class NanoleafCloudError(NanoleafError):
    """The Nanoleaf community scene library could not be reached or understood."""
