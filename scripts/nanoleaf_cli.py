"""Talk to a Nanoleaf Essentials strip directly, without Home Assistant.

Uses the integration's own protocol library (custom_components/nanoleaf_ble/lib),
which has no Home Assistant imports. Through an ESPHome Bluetooth proxy, run
the same commands with scripts/esphome_proxy.py.

    pip install bleak cryptography aiohttp

    python scripts/nanoleaf_cli.py scan
    python scripts/nanoleaf_cli.py cloud-search "<words>" [--featured] [--top|--recent]
    python scripts/nanoleaf_cli.py <command> <ADDRESS> [args]

Commands:
    identify                      blink (no pairing needed)
    pair 12345678                 pair with the 8-digit setup code; stores the token
    on | off | state
    brightness 50                 0..100 %
    hs 240 100                    hue 0..360, saturation 0..100
    ct 2700                       Kelvin (not on the NL62)
    info                          hardware / firmware / serial / EUI
    retain [on|off]               power-loss recovery
    scenes | current              stored scenes | what plays (00 none, ff preview)
    scene <id|name>               play a stored scene, e.g. scene "Northern Lights"
    addscene <id> <fade|random|highlight|flow|stripes> <p1,p2,..> h,s,b[xN] ...
    delscene <id> [--force]       factory scenes (250..254) need --force
    rawscene <id>                 a stored scene's bytes
    preview <type> <p1,..> h,s,b[xN] ...   play without storing (xN = N copies)
    cloud-preview <key> | cloud-clone <key> <id>
    shell                         one connection; commands from stdin
    unpair --yes                  firmware 5 only; legacy firmware cannot unpair

Times are in tenths of a second. Credentials go to ./nanoleaf_creds.json, which
holds auth tokens: keep it private (it is git-ignored).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import shlex
import sys
import time

# Import the library as a top-level package, without Home Assistant.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "nanoleaf_ble")
)

import aiohttp
from bleak import BleakClient, BleakScanner
from lib import (
    NANOLEAF_COMPANY_ID,
    CommunityClient,
    Credentials,
    EffectType,
    NanoleafDevice,
    NanoleafError,
    PaletteColour,
    Scene,
    builtin_scene_names,
    parse_advertisement,
    scene_from_animation,
)

CREDS_FILE = Path("nanoleaf_creds.json")
USER_AGENT = "ha-nanoleaf-ble-cli (+https://github.com/jorgediez/ha-nanoleaf-ble)"
EFFECTS = {e.name.lower(): e for e in EffectType}


def load_creds() -> dict:
    return json.loads(CREDS_FILE.read_text()) if CREDS_FILE.exists() else {}


def save_creds(address: str, credentials: Credentials | None) -> None:
    creds = load_creds()
    if credentials is None:
        creds.pop(address, None)
    else:
        creds[address] = {
            "token": credentials.token.hex(),
            "client_id": credentials.client_id,
        }
    CREDS_FILE.write_text(json.dumps(creds, indent=2))
    if os.name != "nt":
        CREDS_FILE.chmod(0o600)


def colours(args: list[str]) -> list[PaletteColour]:
    out = []
    for arg in args:
        hsb, _, repeat = arg.partition("x")
        hue, saturation, brightness = (int(x) for x in hsb.split(","))
        out.append(PaletteColour(hue, saturation, brightness, int(repeat or 1)))
    return out


def scene_from_args(args: list[str]) -> Scene:
    params = tuple(int(x) for x in args[1].split(","))
    return Scene(EFFECTS[args[0]], params, tuple(colours(args[2:])))


class Strip:
    """A strip found by address, reconnecting when the link drops."""

    def __init__(self, address: str) -> None:
        self.address = address.upper()
        self.ble_device = None
        self.advertisement = None
        self.name = self.address
        self.client = None
        self.device: NanoleafDevice | None = None

    async def find(self) -> None:
        found = asyncio.Event()

        def seen(device, adv) -> None:
            data = adv.manufacturer_data.get(NANOLEAF_COMPANY_ID)
            if device.address.upper() == self.address and data:
                self.ble_device = device
                self.advertisement = parse_advertisement(data)
                self.name = adv.local_name or device.name or self.address
                found.set()

        async with BleakScanner(detection_callback=seen):
            await asyncio.wait_for(found.wait(), 20)

    async def connect(self, login: bool = True) -> NanoleafDevice:
        if (
            self.device is not None
            and not self.device.broken
            and self.client.is_connected
        ):
            return self.device
        if self.ble_device is None:
            await self.find()
        for attempt in range(3):
            try:
                self.client = BleakClient(self.ble_device)
                await self.client.connect()
                self.device = NanoleafDevice(
                    self.client, self.advertisement, name=self.name
                )
                if login:
                    cred = load_creds().get(self.address)
                    if not cred:
                        raise SystemExit("not paired; run `pair` first")
                    await self.device.login(
                        Credentials(bytes.fromhex(cred["token"]), cred.get("client_id"))
                    )
                return self.device
            except Exception as exc:
                await self.close()
                if attempt == 2:
                    raise
                print(f"(connect attempt {attempt + 1} failed: {exc}; retrying)")
                # The strip answers GATT 133 to an immediate reconnect.
                await asyncio.sleep(2)
        raise RuntimeError("unreachable")

    async def close(self) -> None:
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.disconnect()


async def community() -> tuple[aiohttp.ClientSession, CommunityClient]:
    session = aiohttp.ClientSession()
    return session, CommunityClient(session, USER_AGENT)


async def dispatch(strip: Strip, command: str, rest: list[str]) -> None:
    device = await strip.connect()
    names = builtin_scene_names(strip.advertisement.model)
    if command in ("on", "off"):
        await device.set_light(on=command == "on")
    elif command == "brightness":
        await device.set_light(brightness=int(rest[0]))
    elif command == "hs":
        await device.set_light(hue=int(rest[0]), saturation=int(rest[1]))
    elif command == "ct":
        await device.set_light(colour_temperature=int(rest[0]))
    elif command == "state":
        print(
            await device.get_state(),
            "current scene:",
            hex(await device.current_scene()),
        )
    elif command == "info":
        print(await device.device_information())
    elif command == "retain":
        if rest:
            await device.set_power_loss_recovery(rest[0] in ("on", "1", "true"))
        print("restore after power loss:", await device.get_power_loss_recovery())
    elif command == "scenes":
        for scene_id in await device.list_scenes():
            scene = await device.get_scene(scene_id)
            print(
                scene_id,
                names.get(scene_id, "?"),
                scene.effect.name.lower(),
                scene.settings,
                [c.as_dict() for c in scene.palette],
            )
    elif command == "current":
        print(hex(await device.current_scene()))
    elif command == "scene":
        arg = " ".join(rest)
        by_name = {v.lower(): k for k, v in names.items()}
        await device.activate_scene(int(arg) if arg.isdigit() else by_name[arg.lower()])
    elif command == "addscene":
        await device.add_scene(int(rest[0]), scene_from_args(rest[1:]))
    elif command == "delscene":
        await device.delete_scene(int(rest[0]), allow_protected="--force" in rest)
    elif command == "rawscene":
        print((await device.get_scene(int(rest[0]))).encode(int(rest[0])).hex())
    elif command == "preview":
        await device.preview_scene(scene_from_args(rest))
    elif command in ("cloud-preview", "cloud-clone"):
        session, client = await community()
        async with session:
            scene, warnings = scene_from_animation(await client.download(rest[0]))
        for warning in warnings:
            print("warning:", warning)
        if command == "cloud-preview":
            await device.preview_scene(scene)
        else:
            await device.add_scene(int(rest[1]), scene)
            await device.activate_scene(int(rest[1]))
    elif command == "unpair":
        if "--yes" not in rest:
            raise SystemExit("unpair removes this client's pairing; add --yes")
        if strip.advertisement.ltpdu_v3:
            await device.unpair()
        else:
            print(
                "legacy firmware has no unpair; only forgetting the token locally "
                "(the strip keeps accepting it until a factory reset)"
            )
        save_creds(strip.address, None)
    else:
        raise SystemExit(f"unknown command {command}")


async def shell(strip: Strip) -> None:
    loop = asyncio.get_running_loop()
    print("shell ready; commands as in the CLI without the address (quit to exit)")
    while (
        line := (await loop.run_in_executor(None, sys.stdin.readline)).strip() or None
    ):
        if line.startswith("#"):
            continue
        parts = shlex.split(line)
        if parts[0] in ("quit", "exit"):
            break
        if parts[0] == "sleep":
            await asyncio.sleep(float(parts[1]))
            continue
        started = time.perf_counter()
        try:
            await dispatch(strip, parts[0], parts[1:])
        except (NanoleafError, KeyError, ValueError, IndexError) as exc:
            print("error:", exc)
        print(f"({(time.perf_counter() - started) * 1000:.0f} ms)")


async def main(argv: list[str]) -> None:
    if argv and argv[0] == "cloud-search":
        query = " ".join(a for a in argv[1:] if not a.startswith("--"))
        sort = "top" if "--top" in argv else "recent" if "--recent" in argv else None
        session, client = await community()
        async with session:
            for result in await client.search(
                query, featured="--featured" in argv, sort=sort
            ):
                print(
                    f"{result.name!r:40} by {result.creator:20} {result.plugin:12} "
                    f"dl={result.downloads:<5} key={result.key}"
                )
        return
    if not argv or argv[0] == "scan":

        def seen(device, adv) -> None:
            if (data := adv.manufacturer_data.get(NANOLEAF_COMPANY_ID)) and len(
                data
            ) >= 10:
                print(
                    device.address,
                    adv.local_name,
                    parse_advertisement(data),
                    data.hex(),
                )

        async with BleakScanner(detection_callback=seen):
            await asyncio.sleep(10)
        return

    command, address, *rest = argv
    strip = Strip(address)
    try:
        if command == "identify":
            await (await strip.connect(login=False)).identify()
        elif command == "pair":
            device = await strip.connect(login=False)
            credentials = await device.pair(rest[0])
            save_creds(strip.address, credentials)
            print(
                "paired; token",
                credentials.token.hex(),
                "client id",
                credentials.client_id,
            )
        elif command == "shell":
            await shell(strip)
        else:
            await dispatch(strip, command, rest)
    finally:
        await strip.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
