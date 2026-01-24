"""Simple discover example using python-kasa."""

import asyncio
import os
import sys
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv
from kasa import Credentials, Discover

CONFIG_DIR = Path(__file__).parent.parent / "config"
ENV_PATH = CONFIG_DIR / ".env"


def load_credentials() -> Credentials | None:
    """Load credentials from config/.env if it exists."""
    if not ENV_PATH.exists():
        return None

    load_dotenv(ENV_PATH)

    username = os.getenv("KASA_USERNAME")
    password = os.getenv("KASA_PASSWORD")

    if not username or not password:
        return None

    return Credentials(username=username, password=password)


async def discover_devices(raw: bool = False):
    """Discover all Kasa devices on the local network."""
    credentials = load_credentials()

    if credentials:
        print("Using credentials from config/.env")
    else:
        print("No config/.env found, discovering without credentials")

    print("Discovering devices...\n")

    device_count = 0

    async def on_device_discovered(device):
        """Callback for each discovered device."""
        nonlocal device_count
        device_count += 1

        if raw:
            print(f"\n[{device.host}]")
            print("-" * 40)
            print("Device attributes:")
            pprint(vars(device))
            if hasattr(device, "_discovery_info"):
                print("\nDiscovery info:")
                pprint(device._discovery_info)
            print("=" * 60)
        else:
            try:
                await device.update()
                print(f"  IP: {device.host}")
                print(f"  MAC: {device.mac}")
                print(f"  Model: {device.model}")
                print(f"  Alias: {device.alias}")
            except Exception as e:
                print(f"  IP: {device.host}")
                print(f"  MAC: {getattr(device, 'mac', 'Unknown')}")
                print(f"  Model: {getattr(device, 'model', 'Unknown')}")
                print(f"  Error: {type(e).__name__}: {e}")
            print()

    if raw:
        print("=" * 60)

    found_devices = await Discover.discover(
        on_discovered=on_device_discovered,
        credentials=credentials,
    )

    print(f"\nDiscovery complete. Found {device_count} device(s).")

    # Close all device connections
    for device in found_devices.values():
        await device.disconnect()


if __name__ == "__main__":
    raw_mode = len(sys.argv) > 1 and sys.argv[1] == "--raw"
    asyncio.run(discover_devices(raw=raw_mode))
