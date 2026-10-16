"""Run scripts/nanoleaf_cli.py through an ESPHome Bluetooth proxy.

    pip install bleak cryptography aiohttp bleak-esphome habluetooth
    NL_PROXY=192.168.1.60 NL_PSK=<api encryption key> \
        python scripts/esphome_proxy.py <cli command> [address] [args]

The proxy needs `bluetooth_proxy: active: true`. Commands are the CLI's; this
only swaps plain bleak for Home Assistant's Bluetooth stack talking through the
proxy (as verified on an NL62).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys

from bleak_esphome import APIConnectionManager
import habluetooth
from habluetooth import BluetoothManager, HaBleakClientWrapper, HaBleakScannerWrapper

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nanoleaf_cli

logging.basicConfig(level=os.environ.get("NL_LOG", "WARNING"))


async def main(argv: list[str]) -> None:
    manager = BluetoothManager()
    habluetooth.set_manager(manager)
    try:
        await manager.async_setup()
    except Exception as exc:
        print("manager setup warning:", exc)
    connection = APIConnectionManager(
        {"address": os.environ["NL_PROXY"], "noise_psk": os.environ.get("NL_PSK")}
    )
    await asyncio.wait_for(connection.start(), 20)
    print("proxy connected")
    nanoleaf_cli.BleakClient = HaBleakClientWrapper
    nanoleaf_cli.BleakScanner = HaBleakScannerWrapper
    try:
        await nanoleaf_cli.main(argv)
    finally:
        await connection.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
