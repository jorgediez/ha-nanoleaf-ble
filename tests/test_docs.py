"""The scene guide's examples are valid action data."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

from custom_components.nanoleaf_ble.services import (
    PREVIEW_SCENE_SCHEMA,
    _scene_from_call,
)

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "SCENES.md"
BLOCKS = re.findall(r"```yaml\n(.*?)```", GUIDE.read_text(encoding="utf-8"), re.S)
#: The complete scenes in the guide: the recipes, which take an effect.
SCENES = [yaml.safe_load(block) for block in BLOCKS if "\neffect:" in f"\n{block}"]


def test_the_guide_has_recipes() -> None:
    assert len(SCENES) >= 5


@pytest.mark.parametrize("scene", SCENES, ids=lambda scene: scene["effect"])
def test_recipe_is_valid(scene: dict) -> None:
    data = PREVIEW_SCENE_SCHEMA({"entity_id": "light.strip", **scene})
    _scene_from_call(data)
