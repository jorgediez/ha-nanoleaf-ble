# Security policy

## Reporting a vulnerability

Please report it privately, through
[Report a vulnerability](https://github.com/jorgediez/ha-nanoleaf-ble/security/advisories/new)
on this repository's Security tab. Don't open a public issue. Only the
maintainer sees the report, and no email address is needed.

Describe what you found, how to reproduce it, and which versions of the
integration and Home Assistant you saw it on.

## What counts

- Diagnostics that contain what they are meant to leave out: the Bluetooth
  address, the auth token or client ID, the serial number or the device ID
  (EUI), or scene names.
- The auth token reaching anywhere other than the config entry and the strip
  (logs, entity attributes, the community API).
- Anything that lets someone control the strip through Home Assistant beyond
  what the integration's entities and actions offer.
- A way to make the integration run code, or crash Home Assistant, from what a
  strip advertises or answers, or from what the community library returns.

Not a vulnerability in this integration:

- **Identify works without pairing.** Anyone in Bluetooth range can make the
  strip blink; that is how the device works.
- **Legacy firmware cannot unpair.** Below firmware 5 the strip has no command
  to forget a controller, so a token stays valid until a factory reset, whatever
  the integration does.
- The strips' own protocol and its key derivation (SHA-1, a fixed salt, the
  setup code as the only secret) are the manufacturer's design.

## Supported versions

Only the latest release gets fixes.

## What to expect

This is a volunteer project. Reports are read and fixed on a best-effort basis,
and you'll hear back in the report itself, where any fix and its release are
coordinated before details are made public.
