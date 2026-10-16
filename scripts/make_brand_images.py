"""Generate the integration's brand images.

Since Home Assistant 2026.3 an integration can ship its own brand images in a
``brand/`` directory inside the integration, and they override the CDN with no
configuration and no pull request to the central brands repository:

    custom_components/nanoleaf_ble/brand/
        icon.png       256x256
        icon@2x.png    512x512

The images follow the brands repository specification: PNG, 1:1 for icons,
transparent, and trimmed of empty edges.

This draws a **generic LED strip with a Bluetooth badge, not the Nanoleaf or
Secretlab logo**: a community integration should not imply an official
association. No logo.png (a wordmark) is generated for the same reason.

No dark_ variants are needed: the palette reads on light and dark backgrounds,
and Home Assistant falls back to the non-prefixed image when a dark_ one is
absent.

    pip install pillow
    python scripts/make_brand_images.py
"""

from __future__ import annotations

import colorsys
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "nanoleaf_ble" / "brand"
)

STRIP = (55, 65, 81, 255)  # slate: the strip's body
BADGE = (0, 130, 252, 255)  # Bluetooth blue
RUNE = (255, 255, 255, 255)
SUPERSAMPLE = 4
LEDS = 9


def _wave(t: float) -> tuple[float, float]:
    """Return a point along the strip, t in 0..1, in 0..100 units."""
    x = 10 + 80 * t
    y = 62 - 16 * math.sin(t * math.pi * 1.6 - 0.3)
    return x, y


def draw_icon(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = s / 100.0

    def px(x: float, y: float) -> tuple[float, float]:
        return x * unit, y * unit

    # The strip: a thick ribbon along a gentle wave, drawn as overlapping
    # discs so the bends have no seams.
    r = 7.5 * unit
    for i in range(401):
        x, y = px(*_wave(i / 400))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=STRIP)

    # LEDs, a rainbow along the strip.
    for index in range(LEDS):
        x, y = px(*_wave(0.04 + 0.92 * index / (LEDS - 1)))
        red, green, blue = colorsys.hsv_to_rgb(index / LEDS, 0.85, 1.0)
        r = 4.2 * unit
        draw.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=(int(red * 255), int(green * 255), int(blue * 255), 255),
        )

    # A Bluetooth badge, top right.
    cx, cy, radius = 74 * unit, 24 * unit, 17 * unit
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=BADGE)
    h, w = 11 * unit, 5.5 * unit
    rune = [
        (cx - w, cy - h / 2),
        (cx + w, cy + h / 2),
        (cx, cy + h),
        (cx, cy - h),
        (cx + w, cy - h / 2),
        (cx - w, cy + h / 2),
    ]
    draw.line(rune, fill=RUNE, width=int(2.6 * unit), joint="curve")

    return _trim_to_square(image, size)


def _trim_to_square(image: Image.Image, size: int, margin: float = 0.02) -> Image.Image:
    """Crop away empty edges, then recentre on a square canvas."""
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    side = max(image.size)
    padded = int(side * (1 + margin * 2))
    canvas = Image.new("RGBA", (padded, padded), (0, 0, 0, 0))
    canvas.alpha_composite(
        image, ((padded - image.width) // 2, (padded - image.height) // 2)
    )
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        path = OUT / name
        draw_icon(size).save(path, "PNG", optimize=True)
        print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    main()
