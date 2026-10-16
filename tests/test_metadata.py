"""Checks on the files that describe the integration rather than run it."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

REPO = Path(__file__).resolve().parents[1]


def test_the_version_is_declared_once():
    """The release workflow refuses a tag that disagrees with either file.

    Catching a mismatch here keeps it from surfacing only after a release is
    published.
    """
    manifest = json.loads(
        (REPO / "custom_components" / "nanoleaf_ble" / "manifest.json").read_text(
            "utf-8"
        )
    )
    project = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))

    assert manifest["version"] == project["project"]["version"]
