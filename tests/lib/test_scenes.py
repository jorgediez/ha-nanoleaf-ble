"""Simple scenes, the Secretlab defaults and the community conversion."""

from __future__ import annotations

import pytest

from custom_components.nanoleaf_ble.lib import (
    EffectType,
    NanoleafCloudError,
    NanoleafProtocolError,
    PaletteColour,
    Scene,
    app_palette,
    decode_scene,
    default_scenes,
)
from custom_components.nanoleaf_ble.lib.cloud import (
    CommunityClient,
    CommunityScene,
    scene_from_animation,
)
from custom_components.nanoleaf_ble.lib.const import builtin_scene_names
from custom_components.nanoleaf_ble.lib.defaults import _SECRETLAB_DEFAULTS_HEX

# --------------------------------------------------------------------------- format


@pytest.mark.parametrize(("scene_id", "data"), sorted(_SECRETLAB_DEFAULTS_HEX.items()))
def test_defaults_decode_and_encode_byte_for_byte(scene_id, data):
    """The app's own bytes survive a decode and re-encode unchanged."""
    decoded_id, scene = decode_scene(bytes.fromhex(data))
    assert decoded_id == scene_id
    assert scene.encode(scene_id).hex() == data


def test_defaults_per_model():
    assert sorted(default_scenes("NL62")) == [244, 245, 246, 247, 248, 249]
    assert default_scenes("NL55") == {}
    assert builtin_scene_names("NL62")[250] == "Northern Lights"
    assert builtin_scene_names("NL55")[252] == "Blaze"


def test_repeat_counts_round_trip():
    scene = Scene(
        EffectType.FADE,
        (10, 0, 1),
        (PaletteColour(0, 100, 100, repeat=3), PaletteColour(240, 100, 100)),
    )
    data = scene.encode(5)
    assert decode_scene(data) == (5, scene)
    assert data[-4] == 3  # the count byte follows the flagged colour


def test_decode_rejects_garbage():
    with pytest.raises(NanoleafProtocolError, match="without metadata"):
        decode_scene(b"\x02\x01\x00")
    with pytest.raises(NanoleafProtocolError, match="unknown effect"):
        decode_scene(b"\x01\x02\x01\x09")


def test_app_palette_quirk():
    """Three blacks become "black x2, black, black", as the app writes them."""
    black, red = (0, 0, 0), (0, 100, 100)
    palette = app_palette([red, black, black, black])
    assert [c.repeat for c in palette] == [1, 2, 1, 1]
    assert len(app_palette([red] * 9)) == 7


def test_build_fills_defaults_and_validates():
    colours = [PaletteColour(0, 100, 100)]
    scene = Scene.build(EffectType.FLOW, colours, direction=1)
    assert scene.params == (24, 0, 1, 1)
    assert scene.settings["direction"] == 1
    with pytest.raises(ValueError, match="does not take segment"):
        Scene.build(EffectType.FADE, colours, segment=5)
    with pytest.raises(ValueError, match="speed must be"):
        Scene.build(EffectType.FADE, colours, speed=0)
    with pytest.raises(ValueError, match="1 to 7 colours"):
        Scene.build(EffectType.FADE, [])
    with pytest.raises(ValueError, match="out of range"):
        PaletteColour(400, 0, 0)


def test_dict_round_trip_and_fingerprint():
    scene = default_scenes("NL62")[245]
    assert Scene.from_dict(scene.as_dict()) == scene
    assert scene.fingerprint == Scene.from_dict(scene.as_dict()).fingerprint
    assert scene.fingerprint != default_scenes("NL62")[244].fingerprint


# --------------------------------------------------------------------------- community


def test_convert_fade():
    scene, warnings = scene_from_animation(
        {
            "pluginUuid": "b3fd723a-aae8-4c99-bf2b-087159e0ef53",
            "pluginOptions": [
                {"name": "transTime", "value": 300},
                {"name": "delayTime", "value": 5},
                {"name": "loop", "value": False},
            ],
            "palette": [
                {"hue": 10, "saturation": 90, "brightness": 80},
                {"hue": 400, "saturation": 120, "brightness": -5},
            ],
        }
    )
    assert warnings == []
    # The strip field is one byte and the app writes value & 0xFF.
    assert scene.params == (300 & 0xFF, 5, 0)
    assert scene.palette[1] == PaletteColour(360, 100, 0)


def test_convert_unknown_plugin_falls_back_like_the_app():
    """Verified: a featured Radiate (transTime 9) was stored as random [24, 0]."""
    scene, warnings = scene_from_animation(
        {
            "pluginUuid": "radiate",
            "animType": "plugin",
            "pluginOptions": [{"name": "transTime", "value": 9}],
            "colorType": "CCT",
            "palette": [
                {"hue": i * 30, "saturation": 100, "brightness": 100} for i in range(9)
            ],
        }
    )
    assert scene.effect is EffectType.RANDOM
    assert scene.params == (24, 0)
    assert len(scene.palette) == 7
    assert len(warnings) == 3


@pytest.mark.parametrize(
    ("plugin", "effect", "params"),
    [
        ("ba632d3e-9c2b-4413-a965-510c839b3f71", EffectType.RANDOM, (24, 0)),
        ("70b7c636-6bf8-491f-89c1-f4103508d642", EffectType.HIGHLIGHT, (24, 0, 80)),
        ("027842e4-e1d6-4a4c-a731-be74a1ebd4cf", EffectType.FLOW, (24, 0, 2, 1)),
        ("Motion_STRIPES", EffectType.STRIPES, (24, 2, 50)),
    ],
)
def test_convert_each_motion(plugin, effect, params):
    scene, _ = scene_from_animation(
        {
            "pluginUuid": plugin,
            "pluginOptions": [{"name": "linDirection", "value": "up"}],
            "palette": [{"hue": 1, "saturation": 2, "brightness": 3}, "junk"],
        }
    )
    assert scene.effect is effect
    assert scene.params == params


def test_convert_without_colours():
    with pytest.raises(NanoleafCloudError, match="no colours"):
        scene_from_animation({"pluginUuid": "x", "palette": []})


class _Response:
    def __init__(self, status: int, payload, error: Exception | None = None) -> None:
        self.status, self.payload, self.error = status, payload, error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            import aiohttp

            raise aiohttp.ClientError(f"HTTP {self.status}")

    async def json(self, content_type=None):
        return self.payload


class _Session:
    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url, headers or {}))
        return self.responses.pop(0)


SEARCH_REPLY = {
    "items": 2,
    "data": [
        {
            "key": "a2V5",
            "effect_name": "Pumpkin Hallow",
            "creator": {"display_name": "Nanoleaf"},
            "plugin": {"name": "Radiate"},
            "featured": True,
            "downloads": 12,
        },
        {"effect_name": "no key"},
    ],
}


async def test_search():
    session = _Session(_Response(200, SEARCH_REPLY))
    client = CommunityClient(session, "ha-nanoleaf-ble/1.0.0")  # type: ignore[arg-type]
    results = await client.search("pumpkin", featured=True, sort="top")
    assert results == [
        CommunityScene("a2V5", "Pumpkin Hallow", "Nanoleaf", "Radiate", True, 12)
    ]
    method, url, headers = session.calls[0]
    assert (method, url) == ("POST", "https://my.nanoleaf.me/api/v1/effects/search")
    assert headers["User-Agent"] == "ha-nanoleaf-ble/1.0.0"


async def test_download():
    session = _Session(_Response(200, {"animName": "x"}))
    client = CommunityClient(session, "ha-nanoleaf-ble/1.0.0")  # type: ignore[arg-type]
    assert await client.download("a2V5") == {"animName": "x"}
    method, url, headers = session.calls[0]
    assert (method, url) == (
        "GET",
        "https://my.nanoleaf.me/api/v1/effects/download/a2V5",
    )
    assert headers["User-Agent"] == "ha-nanoleaf-ble/1.0.0"


async def test_refused_request_is_an_error():
    """A refusal is reported, not retried under another client's User-Agent."""
    session = _Session(_Response(403, None))
    client = CommunityClient(session, "ha-nanoleaf-ble/1.0.0")  # type: ignore[arg-type]
    with pytest.raises(NanoleafCloudError, match="HTTP 403"):
        await client.download("a2V5")
    assert len(session.calls) == 1


async def test_cloud_errors():
    import aiohttp

    client = CommunityClient(
        _Session(_Response(0, None, aiohttp.ClientError("down"))),  # type: ignore[arg-type]
        "ua",
    )
    with pytest.raises(NanoleafCloudError, match="down"):
        await client.search()
    client = CommunityClient(_Session(_Response(200, [])), "ua")  # type: ignore[arg-type]
    with pytest.raises(NanoleafCloudError, match="not an object"):
        await client.search()
    client = CommunityClient(_Session(_Response(200, [])), "ua")  # type: ignore[arg-type]
    with pytest.raises(NanoleafCloudError, match="not an object"):
        await client.download("k")
    client = CommunityClient(_Session(_Response(500, None)), "ua")  # type: ignore[arg-type]
    with pytest.raises(NanoleafCloudError):
        await client.download("k")
