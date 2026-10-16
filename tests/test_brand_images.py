"""Validate the shipped brand images.

Since Home Assistant 2026.3 an integration serves its own brand images from a
``brand/`` directory, so a malformed or wrongly sized file is now this repo's
problem rather than something a brands-repository reviewer would catch.

The PNG header is parsed directly, so these tests need no imaging library.
"""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

BRAND = (
    Path(__file__).resolve().parents[1] / "custom_components" / "nanoleaf_ble" / "brand"
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Colour type 6 is truecolour with alpha; 3 is a palette, which may still carry
# transparency through a tRNS chunk.
COLOUR_TYPE_RGBA = 6
COLOUR_TYPE_PALETTE = 3

EXPECTED = {
    "icon.png": 256,
    "icon@2x.png": 512,
}


def read_png_header(path: Path) -> tuple[int, int, int]:
    """Return (width, height, colour_type) from a PNG's IHDR chunk."""
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"
    # 8-byte signature, 4-byte length, 4-byte "IHDR", then the header fields.
    assert data[12:16] == b"IHDR", f"{path.name} does not start with IHDR"
    width, height = struct.unpack(">II", data[16:24])
    colour_type = data[25]
    return width, height, colour_type


@pytest.mark.parametrize(("name", "size"), sorted(EXPECTED.items()))
def test_brand_image_exists(name, size):
    assert (BRAND / name).is_file(), f"{name} is missing from brand/"


@pytest.mark.parametrize(("name", "size"), sorted(EXPECTED.items()))
def test_brand_image_dimensions(name, size):
    """Icons must be exactly 256x256 and 512x512, square."""
    width, height, _ = read_png_header(BRAND / name)
    assert (width, height) == (size, size)


@pytest.mark.parametrize(("name", "size"), sorted(EXPECTED.items()))
def test_brand_image_has_transparency(name, size):
    """Transparency is required for the icon to sit on any background."""
    _, _, colour_type = read_png_header(BRAND / name)
    assert colour_type in (COLOUR_TYPE_RGBA, COLOUR_TYPE_PALETTE)


def test_no_unexpected_files_in_brand_directory():
    """Only filenames Home Assistant recognises are served; others are dead weight."""
    allowed = {
        f"{prefix}{stem}{suffix}.png"
        for prefix in ("", "dark_")
        for stem in ("icon", "logo")
        for suffix in ("", "@2x")
    }
    actual = {p.name for p in BRAND.iterdir() if p.is_file()}
    assert actual <= allowed, f"unrecognised brand files: {actual - allowed}"


def test_brand_images_are_reasonably_small():
    """These ship to every install; keep them from bloating the integration."""
    for path in BRAND.glob("*.png"):
        assert path.stat().st_size < 200_000, (
            f"{path.name} is {path.stat().st_size} bytes"
        )
