# Contributing

Thanks for looking. Reports from models other than the Secretlab NL62 are the
most useful thing anyone can send: the integration talks to an undocumented
protocol, reconstructed from the Nanoleaf app and verified on one strip.

## Reporting a device, or a problem

Bugs, and "it works / doesn't work on my strip", are best as an
[issue](https://github.com/jorgediez/ha-nanoleaf-ble/issues/new/choose): there is
a form for bugs and one for telling us how the integration does on your device.
Questions are welcome there too.

What makes a report actionable:

- The integration's diagnostics (*Settings → Devices & Services → Nanoleaf
  Essentials (Bluetooth) → ⋮ → Download diagnostics*). The Bluetooth address,
  auth token, serial number and device ID are redacted. They include the model,
  firmware and protocol, which is what tells devices apart.
- A debug log covering the moment it went wrong, with
  `custom_components.nanoleaf_ble: debug` in your `logger` settings. See
  [Troubleshooting](README.md#troubleshooting).
- For a device that is not discovered or not supported: the raw advertisement
  (the manufacturer data for company 0x080B), from *Settings → System →
  Hardware → ⋮ → Bluetooth → Advertisement monitor*.

A security problem should not go in an issue: see [SECURITY.md](SECURITY.md) for
how to report it privately.

## Help wanted

- **NL55, NL68 and NL45 owners**: a device report, even "everything works",
  confirms support.
- **Firmware 5.0.0 and later**: the LTPDUv3 protocol is implemented from the
  app's code but has never met a real device. A debug log of pairing is gold.
  Its scene format (Effects V2) is not implemented yet.
- **1D devices** (NL71, NL72, NL73, NL75, NL77): they use the same transport
  with their own scene commands, which are not covered.
  [docs/PROTOCOL.md](docs/PROTOCOL.md) §14 lists what is known.

`scripts/nanoleaf_cli.py` talks to a device directly, without Home Assistant,
and `scripts/esphome_proxy.py` does the same through an ESPHome Bluetooth proxy.
They are the quickest way to probe a new model.

## What belongs here

This integration controls Nanoleaf Essentials lights over Bluetooth LE, locally.
The only cloud it talks to is the optional community scene library.

Changes that fit:

- More of what the devices report or accept over Bluetooth.
- Making an existing feature work on another Essentials model or firmware.
- Documentation of device behaviour, in docs/PROTOCOL.md.

Changes that belong elsewhere:

- Nanoleaf light panels (Shapes, Canvas, Elements, Lines) are Wi-Fi devices with
  Home Assistant's core `nanoleaf` integration.
- Thread or Matter control is Home Assistant's HomeKit Controller and Matter
  integrations.

**If a change is more than a small fix, open an issue first.** A short
description of what you want and how you'd do it takes minutes and can save you
days.

## Working on the code

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest --cov
```

The Home Assistant test harness only runs on Linux and macOS. On Windows, use
Docker (`scripts/test-docker.sh`, which runs the same two Home Assistant
versions as CI) or WSL.

No hardware is needed: `tests/common.py` has fake strips that speak both
protocols at the GATT level (the key exchange, the encryption and the command
language), and the integration is tested inside Home Assistant against them.

Keep `custom_components/nanoleaf_ble/lib/` free of Home Assistant imports and
use relative imports there, so it can become a package of its own; a test
enforces both.

A pull request is expected to:

- Pass `ruff check`, `ruff format --check` and the test suite. CI runs these
  against the oldest supported Home Assistant and the newest, plus HACS and
  `hassfest`, and needs a maintainer to approve the first run on a fork.
- Come with tests.
- Update the docs it affects: the README for what users see, docs/PROTOCOL.md for
  what was learned about the protocol.
- Explain, in the commit message, why the change is the way it is — particularly
  anything that works around how a device behaves.

Keep a pull request to one subject. Two unrelated improvements are two pull
requests, and they'll both move faster.

## Style

Follow [Home Assistant's own conventions](https://developers.home-assistant.io/docs/development_guidelines)
and the code already here: entity names and errors translated, no blocking calls
in the event loop, comments that say why rather than what.

## Releasing

1. Bump `version` in `custom_components/nanoleaf_ble/manifest.json` and in
   `pyproject.toml`. A test checks they match.
2. Publish a GitHub release tagged `vX.Y.Z`, with user-facing notes. HACS
   installs from that tag.
3. The **Release** workflow checks the tag matches both files, then runs the
   tests and the HACS and hassfest validation against the tagged code.

## License

Contributions are under the [MIT License](LICENSE), like the rest of the project.
