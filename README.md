# Nanoleaf Essentials (Bluetooth) — Home Assistant integration

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]
[![validate][validate-badge]][validate-url]
[![tests][tests-badge]][tests-url]
[![license][license-badge]](LICENSE)

> **Scenes, effects and local control for Nanoleaf Essentials over Bluetooth.**
> Works through your ESPHome Bluetooth proxies. Local control, no cloud needed,
> no re-pairing.

Controls **Nanoleaf Essentials** lightstrips and bulbs, and the **Secretlab
MAGRGB** strip, from Home Assistant over Bluetooth LE, through a local adapter
or an ESPHome Bluetooth proxy. It pairs alongside the Nanoleaf app with the
setup code printed on the strip, keeps one connection open so commands land in
about 200 ms, and polls that connection to pick up changes made in the app.

Developed and verified on a **Secretlab MAGRGB (NL62)**, firmware 1.6.49.

> **Testers wanted.** If you have an **Essentials Lightstrip (NL55 or NL68)**,
> an **Essentials A19 bulb (NL45 or NL67)**, or a Matter Essentials **BR30,
> GU10, Downlight or Ceiling lamp**, please tell us how it goes — a
> [device report][device-report] with the diagnostics attached takes two
> minutes, and is how support for your model gets confirmed. Questions and
> impressions are welcome in the [community forum thread][forum].

Home Assistant can already reach Essentials lights through **HomeKit
Controller** or **Matter**. What this integration adds is what those paths
don't give you: the strip's **multi-colour scenes as effects**, scenes of your
own and from the **Nanoleaf community library**, stored on the strip or played
from Home Assistant without taking a slot — and no Thread border router.

## Features

| Capability | How |
|---|---|
| Discovery | Bluetooth advertisement (company 0x080B): the strip shows up under *Discovered* |
| Pairing | the 8-digit setup code on the strip, alongside the Nanoleaf app (no factory reset) |
| On/off, brightness, colour | `lb/0/oo`, `pb`, `hu` + `sa`; colour temperature where the model has it |
| Scenes on the strip as effects | named like in the app, playable from Home Assistant |
| A scene library in Home Assistant | your own scenes and community scenes, shared by all strips, previewed without using a slot |
| Community scenes | search, preview, save and store scenes from the Nanoleaf library, keeping their names |
| Scene slots | stores and removes scenes, keeps track of the 12-scene limit |
| Default scenes | names the built-in scenes, and writes the app's Secretlab defaults back when you ask |
| Restore on/off after power loss | a switch: after a power cut, a strip that was off stays off |
| Identify | a button that blinks the strip |
| Diagnostics | connection, firmware, hardware, serial number, model, protocol, signal strength |

### Compatibility

| Model | Product | Status |
|---|---|---|
| NL62 | Secretlab MAGRGB | **Tested** (firmware 1.6.49) |
| NL55 | Essentials Lightstrip | Should work — **testers wanted** |
| NL68 | Essentials Lightstrip (Matter) | Should work — **testers wanted** |
| NL45 | Essentials A19 bulb | Should work — **testers wanted** |
| NL67 | Essentials A19 bulb (Matter) | **Unverified**: same protocol as the NL68, never reported on |
| NL53, NL54, NL65, NL70 | Essentials BR30, GU10, Downlight, Ceiling lamp (Matter) | **Unverified**: same protocol as the NL68, never reported on |
| Any of the above on firmware **5.0.0 or later** | | **Experimental**: on/off, brightness, colour and identify only (see below) |

Unverified models are discovered and set up like the others, with a repair
notice that asks for a [device report][device-report]. The app's code does not
say which of NL53, NL54, NL65 and NL70 is which product, so those keep their
model code in the device name.

Other Nanoleaf devices, Sense+ switches and buttons included, are not supported.
If you have a **1D device**
(NL71, NL72, NL73, NL75, NL77 — string lights, outdoor and Wi-Fi strips), they
speak a close relative of this protocol and we would love help getting them
working: open a [device report][device-report] or see
[CONTRIBUTING.md](CONTRIBUTING.md). The light panels (Shapes, Canvas,
Elements, Lines) are Wi-Fi devices with their own core integration.

### Known limits

* **Changes made in the Nanoleaf app appear within the poll interval** (15 s by
  default). The strip reports nothing by itself, so it is read on a timer over
  the open connection. Commands from Home Assistant are immediate.
* **Each strip uses one Bluetooth connection, all the time.** An ESPHome proxy
  has three connection slots by default; a strip keeps one of them. Plan your
  proxies accordingly (`bluetooth_proxy: active: true` and, if needed, more
  `connection_slots`).
* **Firmware 5.0.0 and later is experimental.** It speaks a newer protocol
  (LTPDUv3) that is implemented from the app's code but has never been tested on
  a real strip, and stores scenes in a different format that is not implemented.
  Scenes and Restore on/off after power loss are therefore not available there. A
  repair notice says so.
* **Legacy firmware cannot unpair.** Below firmware 5 the strip has no command
  to forget a controller, so removing the integration leaves its token valid on
  the strip until a factory reset. (On firmware 5 removal unpairs.) The token is
  only usable by whoever has it.
* **A strip holds 12 scenes, built-in ones included.** The Secretlab strip
  arrives with ten, which leaves two slots. Library scenes play without a slot.
* **The factory scenes (IDs 250–254) are never deleted**, because there is no
  known way to put one back. The app-written Secretlab defaults (244–249) may
  be deleted to make room: *Restore default scenes* writes them back.
* **Scene names live in Home Assistant**, as they live in the app: the strip
  stores numbers only. Scenes stored by the app appear as "Scene N" (a stored
  scene with ID 0 plays, but is reported as no effect: the strip reports 0 for
  "nothing playing").
* **Community motions the strip cannot play** (Wheel, Burst, Radiate, …) become
  *Random* with default timing — exactly what the Nanoleaf app does.

## Installation

Requires **Home Assistant 2025.3 or newer** and a Bluetooth adapter or an
ESPHome Bluetooth proxy that can make active connections. There are no external
Python dependencies.

**An ESPHome proxy must scan actively.** The strip sends the data that
identifies it only in its *scan response*, which a scanner receives only when it
asks for it. A passively scanning proxy hears the strip but cannot tell what it
is, so the strip is never discovered. In Home Assistant, set the proxy's
**Bluetooth scanning mode** option to **Active (uses more device battery,
fastest updates)**; the default, *Auto*, did not discover the strip in testing.
This is separate from `bluetooth_proxy: active: true` in the proxy's YAML, which
lets it make connections; both are needed.

### HACS (custom repository)

[![Open your Home Assistant instance and open this repository in HACS.][my-hacs-badge]][my-hacs-url]

Or by hand:

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/jorgediez/ha-nanoleaf-ble` as an **Integration**
3. Install **Nanoleaf Essentials (Bluetooth)**, then restart Home Assistant

### Manual

Copy `custom_components/nanoleaf_ble/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Strips in range show up under *Settings → Devices & Services → Discovered*.
Click **Add**, then enter the **8-digit setup code** printed on the strip or
its box (the code next to the QR code; dashes are optional). Pairing does not
disturb the Nanoleaf app or its pairing.

[![Open your Home Assistant instance and start setting up the integration.][my-setup-badge]][my-setup-url]

You can also start from *Add Integration → Nanoleaf Essentials (Bluetooth)* and
pick a strip Home Assistant has heard.

**Already paired the strip with another tool?** Open **Advanced: use an
existing auth token** on the pairing form, leave the code empty and paste the
token in hex (for example from `scripts/nanoleaf_cli.py pair`). Firmware 5
also needs the client ID that came with it.

**Reconfigure** (*⋮ → Reconfigure*) pairs again or switches to another token.
If the strip is factory-reset, it comes back with a new Bluetooth address and
forgets every pairing. Home Assistant recognises it by its device ID, which a
reset keeps: the existing entry moves to the new address and asks you to pair
again, rather than the strip showing up as a new device. The strip's name in
Home Assistant ends with the tag in its advertised name (*40G8* in
*Secretlab MAGRGB 40G8*), which a reset keeps too.

### Options

| Option | Default | |
|---|---|---|
| Poll interval | 15 s (5–300) | How often the strip is read for changes made elsewhere. Shorter notices the app sooner; each poll is about 0.6 s of radio time. |

## Entities

| Entity | |
|---|---|
| **Light** | on/off, brightness, HS colour (and colour temperature where the model has one), effects: the scenes on the strip, then the library scenes |
| **Restore on/off after power loss** (switch, config) | the app's *Restore last ON/OFF state*. On: after a power cut the strip comes back on or off, as it was. Off: it always comes back on. Either way a strip that was on resumes its colour or scene. A change made in the app shows up within 5 polls (75 s by default). |
| **Identify** (button, diagnostic) | blinks the strip; needs no pairing |
| **Restore default scenes** (button, config) | Secretlab only: writes back the app's default scenes that are missing |
| **Community search** (text, config) | setting it searches the Nanoleaf community library |
| **Community scene** (select, config) | the search results |
| **Preview / Save to library / Add to strip** (buttons, config) | act on the selected community scene |
| **Scene to manage** (select, config) | which scene the buttons below act on: every scene on the strip, then in the library, each marked with where it lives: `[strip]`, `[library]` or `[strip + library]`. Picking one plays nothing. It shows *unknown* until you pick one, and never follows the scene playing, so the buttons only act on a scene you chose. In automations, use the option with its marker, e.g. `Aurora [library]` |
| **Add to strip / Remove from strip / Delete from library** (buttons, config) | act on the scene to manage |
| **Free scene slots**, **Scenes on strip** (sensors) | of the 12 |
| **Connection** (binary sensor, diagnostic) | on while Home Assistant holds the Bluetooth link; turns off the moment the link drops, and stays available while the other entities are not |
| **Firmware**, **Hardware version**, **Serial number**, **Model**, **Protocol**, **Signal strength** (diagnostic sensors) | signal strength is disabled by default |

The scene and community entities are **configuration** entities: on the
device page they sit in the *Configuration* card, apart from the light, and
auto-generated dashboards leave them out. To play a scene, use the light's
effect; *Scene to manage* is only for storing, removing and deleting scenes.

Brightness dims a running scene without stopping it; a colour replaces it.
Turning the light off and on again resumes whatever was playing.

### Scenes: on the strip, or in the library

A scene **on the strip** lives in one of its 12 slots: it plays from the app,
from Home Assistant, and after a power cut. A scene **in the library** lives in
Home Assistant, is shared by all your strips, and plays as a *preview* — no slot
needed, but the app does not know about it. You can move a library scene onto a
strip at any time (*Add to strip*), and take it off again.

* **Storing a scene plays it.** The strip starts a scene as soon as it is
  stored; Home Assistant sends only the store command, then reads the strip
  1.5 s later so the light shows the scene playing. After *Restore default
  scenes*, that is the last one written.
* **The app shows stored scenes as "Effect N".** Names live in Home Assistant,
  not on the strip, so the app cannot know them.
* **Adding a community scene to the strip also saves it to the library.** The
  library copy keeps its name if the strip is ever removed and added again, lets
  you take it off the strip and put it back without downloading it again, and
  makes it available to your other strips. To keep it on the strip only, press
  *Delete from library* afterwards: the strip keeps its copy and its name, until
  the entry is removed.

Effect names follow the app: the built-in scenes by their names
(Northern Lights, Cloud9, …), scenes the integration stored by the names you
gave them. If the app later stores a different scene under the same number, the
integration notices from the scene's content and stops using the old name.

**Default scenes.** After pairing, the Nanoleaf app writes six default scenes to
Secretlab strips (Cyberpunk 2077, VALORANT, Assassin's Creed, Dark Knight, The
Joker, Attack on Titan). Home Assistant doesn't write anything to a strip by
itself, not even these: if Home Assistant was the first to pair, or some were
deleted, press *Restore default scenes* (or call `restore_default_scenes`). It
writes the missing ones into free slots and never deletes anything to make
room; if they don't all fit, it writes what fits and says the strip is full.

## Actions

```yaml
# Create a scene in the library only (no strip needed)
action: nanoleaf_ble.create_scene
data:
  name: Test Fade
  effect: fade
  colours: [[255, 0, 0], [0, 0, 255]]
  speed: 10

# Create a scene in the library, store it on a strip and play it
action: nanoleaf_ble.create_scene
target:
  entity_id: light.secretlab_magrgb_be3a
data:
  name: Sunset
  effect: flow            # fade, random, highlight, flow or stripes
  colours:
    - [255, 80, 0]
    - [255, 0, 90]
    - {hue: 270, saturation: 80, brightness: 60, repeat: 2}
  speed: 40               # tenths of a second
  direction: right
  add_to_strip: true
  play: true

# Play a scene without saving it
action: nanoleaf_ble.preview_scene
target:
  entity_id: light.secretlab_magrgb_be3a
data:
  effect: fade
  colours: [[255, 0, 0], [0, 0, 255]]
  speed: 10

# Search the community library (returns the results)
action: nanoleaf_ble.search_community_scenes
data:
  query: aurora
  sort: top
response_variable: found

# Save the first result, keeping its name, and store it on the strip
action: nanoleaf_ble.save_community_scene
target:
  entity_id: light.secretlab_magrgb_be3a
data:
  key: "{{ found.scenes[0].key }}"
  add_to_strip: true
```

| Action | Strips | What it does |
|---|---|---|
| `create_scene` | optional | adds a scene to the library; `add_to_strip` and `play` need strips |
| `preview_scene` | required | plays a scene without saving it |
| `add_scene_to_strip` | required | stores a library scene on strips |
| `remove_scene_from_strip` | required | deletes a scene from strips (never a factory scene) |
| `rename_scene` | — | renames a library scene and its copies on strips |
| `delete_library_scene` | — | deletes a library scene; copies on strips stay |
| `search_community_scenes` | — | returns `scenes: [{key, name, creator, motion, featured, downloads}]` |
| `preview_community_scene` | required | plays a community scene without saving it |
| `save_community_scene` | optional | saves to the library (optionally `name`), and with `add_to_strip` stores it on the strips |
| `restore_default_scenes` | required | writes back the Secretlab defaults that are missing |

*Required* strips go in `target:`. For the two *optional* ones, give them as
`entity_id` under `data:` (the **Strips** field in the UI), or as a `target:`;
both work.

For every effect, the colour formats, what speed and delay mean, and ready-made
recipes, see **[docs/SCENES.md](docs/SCENES.md)**. In short:

Colours are `[red, green, blue]` (0–255) or
`{hue: 0-360, saturation: 0-100, brightness: 0-100, repeat: N}`; `repeat` plays
that colour N times in the cycle. Speed and delay are in tenths of a second
(1–255). Each effect takes only its own settings:

| Effect | Settings (defaults) |
|---|---|
| fade | speed (24), delay (0), loop (on) |
| random | speed, delay |
| highlight | speed, delay, main_colour_probability (80 %) |
| flow | speed, delay, direction (left), loop |
| stripes | speed, direction, segment (50) |

### Example automations

```yaml
# Northern Lights at sunset, dimmed
triggers:
  - trigger: sun
    event: sunset
actions:
  - action: light.turn_on
    target:
      entity_id: light.secretlab_magrgb_be3a
    data:
      effect: Northern Lights
      brightness_pct: 40

# Flash red while the alarm is triggered, then go back to a warm white
triggers:
  - trigger: state
    entity_id: alarm_control_panel.home
    to: triggered
actions:
  - action: nanoleaf_ble.preview_scene
    target:
      entity_id: light.secretlab_magrgb_be3a
    data:
      effect: fade
      colours: [[255, 0, 0], [0, 0, 0]]
      speed: 3
  - wait_for_trigger:
      - trigger: state
        entity_id: alarm_control_panel.home
        from: triggered
  - action: light.turn_on
    target:
      entity_id: light.secretlab_magrgb_be3a
    data:
      hs_color: [30, 40]
```

## Troubleshooting

* **The strip is not discovered.** It must be powered and in range of an
  adapter or proxy that can connect. An ESPHome proxy needs both
  `bluetooth_proxy: active: true` and its **Bluetooth scanning mode** set to
  **Active**: the strip identifies itself only in its scan response (see
  [Installation](#installation)). Discovery also stops once the strip is set
  up.
* **"Could not connect".** All connection slots of the nearest proxy may be in
  use, or the strip is out of range. The strip sometimes refuses a connection
  right after another one; the integration waits and retries on its own.
* **"The strip refused the setup code".** Check the 8 digits on the strip's
  label or box. Some strips limit how many controllers they accept ("accepts no
  more controllers"); only a factory reset frees those slots.
* **A scene stored by the app shows as "Scene N".** The strip stores numbers,
  not names; the app keeps its names to itself.
* **"The strip is full".** Remove a scene first, or play scenes from the library
  (which takes no slot).

When reporting a problem, attach the integration's diagnostics: *Settings →
Devices & Services → Nanoleaf Essentials (Bluetooth) → ⋮ → Download
diagnostics*. The Bluetooth address, auth token, serial number and device ID
are redacted. A debug log helps too; enable it from the same menu, or with:

```yaml
logger:
  logs:
    custom_components.nanoleaf_ble: debug
```

## Removal

1. *Settings → Devices & Services → Nanoleaf Essentials (Bluetooth)*, then
   **⋮ → Delete** on each strip.
2. If you installed through HACS, remove **Nanoleaf Essentials (Bluetooth)**
   there; otherwise delete `config/custom_components/nanoleaf_ble/`.
3. Restart Home Assistant.

What is left on the strip: scenes you stored on it stay there (they keep
working from the app, under "Scene N"). On firmware 5 the integration unpairs
itself; on older firmware the strip keeps accepting its token until a factory
reset, since that firmware has no unpair command. The scene library is kept in
Home Assistant's storage (`.storage/nanoleaf_ble`) until you delete it.

## Icons

Entity and action icons ship in `icons.json`. The brand tile is served from
`custom_components/nanoleaf_ble/brand/` (Home Assistant **2026.3** and later; on
older releases the tile shows the default placeholder). The artwork is a
**generic LED strip, not the Nanoleaf or Secretlab logo**: a community
integration should not imply an official association. Regenerate it with
`python scripts/make_brand_images.py` (needs `pillow`).

## Protocol

The strips speak an undocumented Bluetooth protocol, reconstructed from the
Nanoleaf Android app and verified on hardware. It is described in full in
**[docs/PROTOCOL.md](docs/PROTOCOL.md)**: discovery, the two pairing and
encryption schemes, the command language, scenes, the community library, device
information and power-loss recovery, with what has and has not been tested.

The protocol code lives in `custom_components/nanoleaf_ble/lib/`, with no Home
Assistant imports, so it can be used on its own:

```bash
pip install bleak cryptography
python scripts/nanoleaf_cli.py scan
python scripts/nanoleaf_cli.py pair DF:31:88:26:BE:3A 12345678
python scripts/nanoleaf_cli.py scene DF:31:88:26:BE:3A "Northern Lights"
```

`scripts/esphome_proxy.py` runs the same commands through an ESPHome Bluetooth
proxy.

## Contributing

Reports from models other than the NL62 are the most useful thing you can send,
and firmware 5 and 1D devices need testers. See [CONTRIBUTING.md](CONTRIBUTING.md)
for how to report, and how to work on the code. For questions, ideas and
setups, there is a [thread on the Home Assistant community forum][forum].

## Disclaimer

This is an unofficial, community project. It is not affiliated with or endorsed
by Nanoleaf or Secretlab. All trademarks belong to their respective owners. Use
at your own risk.

## Credits

* [roysjosh/nanoleaf-ltpdu][ltpdu], which explored the same protocol family over
  Thread.

## License

[MIT](LICENSE)

[device-report]: https://github.com/jorgediez/ha-nanoleaf-ble/issues/new?template=device_report.yml
[ltpdu]: https://github.com/roysjosh/nanoleaf-ltpdu
[forum]: https://community.home-assistant.io/t/nanoleaf-essentials-over-bluetooth-scenes-community-scenes-and-local-control-incl-secretlab-magrgb/1029610
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/jorgediez/ha-nanoleaf-ble
[release-url]: https://github.com/jorgediez/ha-nanoleaf-ble/releases
[validate-badge]: https://github.com/jorgediez/ha-nanoleaf-ble/actions/workflows/validate.yml/badge.svg
[validate-url]: https://github.com/jorgediez/ha-nanoleaf-ble/actions/workflows/validate.yml
[tests-badge]: https://github.com/jorgediez/ha-nanoleaf-ble/actions/workflows/tests.yml/badge.svg
[tests-url]: https://github.com/jorgediez/ha-nanoleaf-ble/actions/workflows/tests.yml
[license-badge]: https://img.shields.io/badge/license-MIT-blue.svg
[my-hacs-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[my-hacs-url]: https://my.home-assistant.io/redirect/hacs_repository/?owner=jorgediez&repository=ha-nanoleaf-ble&category=integration
[my-setup-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
[my-setup-url]: https://my.home-assistant.io/redirect/config_flow_start/?domain=nanoleaf_ble
