# Creating scenes

This guide explains how to build your own scenes with the
`nanoleaf_ble.create_scene` and `nanoleaf_ble.preview_scene` actions: which
effects there are, how to give colours, and what speed, delay and the other
settings mean. For where scenes are kept (on the strip or in the library) and
the other scene actions, see the [README](../README.md#scenes-on-the-strip-or-in-the-library).

Scenes work on legacy firmware (below 5.0.0) only. Strips on firmware 5 and
later use a different scene format that the integration does not support yet.

- [What a scene is made of](#what-a-scene-is-made-of)
- [Trying a scene out](#trying-a-scene-out)
- [Colours](#colours)
- [Speed and delay](#speed-and-delay)
- [The effects](#the-effects)
- [Recipes](#recipes)
- [When an action is refused](#when-an-action-is-refused)

## What a scene is made of

A scene has three parts:

| Part | Field | What it is |
|---|---|---|
| **Effect** | `effect` | How the colours move: `fade`, `random`, `highlight`, `flow` or `stripes`. The UI calls it *Motion*. |
| **Colours** | `colours` | The palette: 1 to 7 colours, played in the order given. |
| **Settings** | `speed`, `delay`, `loop`, `direction`, `segment`, `main_colour_probability` | They tune the effect. Each effect takes only some of them (see [The effects](#the-effects)), and every one is optional. |

`create_scene` also takes a `name`, and `add_to_strip` and `play`, which
decide where the scene goes. `preview_scene` takes the same three parts and
plays them straight away without saving anything.

## Trying a scene out

The quickest way to tune a scene is **Developer tools → Actions**:

1. Pick **Nanoleaf Essentials (Bluetooth): Preview scene** and your strip.
2. Switch to YAML mode and paste a scene, such as one of the [recipes](#recipes).
3. Press **Perform action**. The strip plays it at once, and nothing is saved.
4. Change a value and perform the action again, until you like the result.
5. Then switch to **Create scene**, keep the same fields, and add a `name`.
   Add `add_to_strip: true` to store it on the strip as well.

## Colours

Give each colour in one of two forms, and mix them freely in one palette:

```yaml
colours:
  - [255, 80, 0]                                   # red, green, blue: 0-255 each
  - {hue: 300, saturation: 100, brightness: 60}    # hue 0-360, the others 0-100
  - {hue: 210, saturation: 30, brightness: 100, repeat: 3}
```

- **`[red, green, blue]`**: the usual RGB values, 0 to 255. The strip stores
  colours as hue, saturation and brightness, so the integration converts them.
  Expect small rounding.
- **`{hue, saturation, brightness}`**: hue on the colour wheel in degrees
  (0 red, 60 yellow, 120 green, 180 cyan, 240 blue, 300 magenta). Saturation
  runs from 0 (white) to 100 (full colour), and brightness from 0 (off) to
  100. `brightness` is optional and defaults to 100.
- **Each colour has its own brightness.** A colour at brightness 0, such as
  `[0, 0, 0]`, turns that part of the effect off. The built-in *Dark Knight*
  uses it for its black. The light's own brightness then dims the whole scene
  on top of that, without stopping it.
- **`repeat: N`** plays that colour N times in a row, 1 to 255. It is the total
  number of copies, not extra ones: in a fade with speed 10, red with
  `repeat: 3` followed by blue gives 3 s of red and 1 s of blue (measured on
  video). Repeats don't count against the limit of 7 colours, so use them to
  give a colour more weight.
- **At most 7 entries.** That is what the app sends, and the integration
  enforces it.

## Speed and delay

Both are **times, in tenths of a second**, from 0 to 255. They are not rates.

| Setting | What it is | Range | Default |
|---|---|---|---|
| `speed` | How long the change from one colour to the next takes. **A higher value is slower.** | 1–255 | 24 (2.4 s) |
| `delay` | How long each colour is held before the next change starts. | 0–255 | 0 |

Some useful values:

| Value | Time |
|---|---|
| 3 | 0.3 s: a quick flash |
| 10 | 1 s |
| 24 | 2.4 s: the app's default |
| 50 | 5 s: slow and calm |
| 255 | 25.5 s: the slowest |

These were measured by filming the strip. A fade with speed 50 and delay 30
took about 5 s per change and held each colour for about 3 s.

## The effects

Each effect takes only the settings in its row. Giving it another one is
refused (see [When an action is refused](#when-an-action-is-refused)).

| Effect | Settings it takes, with defaults |
|---|---|
| `fade` | `speed` (24), `delay` (0), `loop` (on) |
| `random` | `speed` (24), `delay` (0) |
| `highlight` | `speed` (24), `delay` (0), `main_colour_probability` (80) |
| `flow` | `speed` (24), `delay` (0), `direction` (left), `loop` (on) |
| `stripes` | `speed` (24), `direction` (left), `segment` (50) |

All of this was checked by watching the strip.

### fade

The whole strip shows one colour at a time and fades into the next, in palette
order. `speed` is the length of each fade, and `delay` the hold on each colour.

- `loop` (default on): after the last colour, fade back to the first and go
  round again, forever. With `loop: false` the strip plays the palette once
  and **stays on the last colour**.

### random

Like `fade`, the whole strip changes colour at once, but the next colour is
picked at random from the palette, so there is no set order. `speed` and
`delay` work as in `fade`.

### highlight

The whole strip changes colour at once, and the **first colour in the palette
is the main one**: it comes up far more often than the others. The rest of the
palette adds the occasional accent.

- `main_colour_probability` (0–100 %, default 80): the chance that each newly
  picked colour is the main one. Measured: 90 gave the main colour about 90 %
  of the time.

### flow

The palette travels along the strip, so several colours show at once, blending
into each other where they meet: red, then yellow, then green, then blue, then
pink, then red again, for a red-green-blue palette. Three of the Secretlab
default scenes (*Assassin's Creed*, *Dark Knight*, *Attack on Titan*) are flows.

- `direction`: which way the colours travel.
  - `left` (default): from the end where the cable connects towards the far
    end.
  - `right`: from the far end towards the cable end.
  - `up` and `down`: the app offers them for other Nanoleaf shapes. On a
    strip they both work like `right`.
- `loop` makes no visible difference: a flow keeps going either way.

### stripes

The strip is split into blocks of the palette's colours, and the blocks move
along it. Behind the strip's diffuser, neighbouring blocks blend into
gradients. *Northern Lights* and two of the Secretlab defaults (*VALORANT*,
*The Joker*) are stripes.

- `segment` (1–255, default 50): the length of each colour block.
  - 5: thin bands of red and blue alternating quickly.
  - 50: a block of red followed by a block of blue.
  - 200: one colour fills the whole strip for a few seconds, then the next
    one moves in and takes over.
- `speed`: how fast the blocks move. As in the other effects, a higher value
  is slower: 5 is far faster than 100. Northern Lights uses 20.
- `direction`: as in `flow`.
- `repeat` on a colour makes its blocks longer.
- No `delay` and no `loop`.

## Recipes

Each one works with `preview_scene` as it is. For `create_scene`, add a `name`.
The first three are the definitions the Nanoleaf app uses, written out as
action data.

```yaml
# Cyberpunk 2077 (Secretlab default): a 1 s fade between yellow and cyan
effect: fade
colours:
  - {hue: 55, saturation: 81, brightness: 99}
  - {hue: 193, saturation: 90, brightness: 85}
speed: 10
```

```yaml
# Assassin's Creed (Secretlab default): red flowing through white
effect: flow
colours:
  - {hue: 0, saturation: 100, brightness: 100}
  - {hue: 0, saturation: 0, brightness: 98}
  - {hue: 0, saturation: 0, brightness: 98}
speed: 8
direction: left
```

```yaml
# VALORANT (Secretlab default): red and white stripes, with a dark band
effect: stripes
colours:
  - {hue: 240, saturation: 8, brightness: 100}
  - {hue: 355, saturation: 79, brightness: 90}
  - {hue: 355, saturation: 79, brightness: 90}
  - [0, 0, 0]
speed: 18
direction: right
segment: 24
```

```yaml
# Close to Northern Lights: narrow stripes over seven full colours
effect: stripes
colours:
  - {hue: 227, saturation: 100}
  - {hue: 182, saturation: 100}
  - {hue: 125, saturation: 100}
  - {hue: 62, saturation: 100}
  - {hue: 31, saturation: 100}
  - {hue: 2, saturation: 100}
  - {hue: 307, saturation: 100}
speed: 20
segment: 20
```

```yaml
# Candle: mostly warm orange, now and then a deeper red or a pale yellow
effect: highlight
colours:
  - [255, 120, 20]
  - [200, 40, 0]
  - [255, 200, 120]
speed: 8
delay: 2
main_colour_probability: 75
```

```yaml
# Slow sunrise: 5 s fades with a 3 s hold on each colour
effect: fade
colours:
  - [120, 20, 0]
  - [255, 80, 0]
  - [255, 160, 60]
  - [255, 230, 180]
speed: 50
delay: 30
```

```yaml
# Alarm: a fast red flash
effect: fade
colours: [[255, 0, 0], [0, 0, 0]]
speed: 3
```

## When an action is refused

The action reports the problem, and nothing is sent to the strip:

| Message | Cause |
|---|---|
| `Invalid scene: fade does not take direction` | A setting the effect doesn't use. Remove it, or pick an effect from [the table](#the-effects) that takes it. |
| `Invalid scene: a scene needs 1 to 7 colours` | The palette is empty or too long. Use `repeat` to give a colour more weight without another entry. |
| `invalid colour …` | An RGB value is outside 0–255, a hue is outside 0–360, a saturation or brightness is outside 0–100, or a mapping has no `hue` or `saturation`. |
| A value out of range | `speed` and `segment` take 1–255, `delay` takes 0–255, and `main_colour_probability` takes 0–100. |
| `… is full: it holds 12 scenes, built-in ones included` | `add_to_strip` found no free slot. Remove a scene with *Remove from strip* first, or keep this one in the library and play it from there. |
