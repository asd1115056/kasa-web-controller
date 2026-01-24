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

    found_devices = await Discover.discover(credentials=credentials)

    if not found_devices:
        print("No devices found.")
        return

    print(f"Found {len(found_devices)} device(s):\n")

    if raw:
        print("=" * 60)

    try:
        for ip, device in found_devices.items():
            if raw:
                print(f"\n[{ip}]")
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
                    print(f"  IP: {ip}")
                    print(f"  MAC: {device.mac}")
                    print(f"  Model: {device.model}")
                    print(f"  Alias: {device.alias}")
                except Exception as e:
                    print(f"  IP: {ip}")
                    print(f"  MAC: {getattr(device, 'mac', 'Unknown')}")
                    print(f"  Model: {getattr(device, 'model', 'Unknown')}")
                    print(f"  Error: {type(e).__name__}: {e}")
                print()
    finally:
        # Close all device connections to avoid "Unclosed client session" warnings
        for device in found_devices.values():
            await device.disconnect()


if __name__ == "__main__":
    raw_mode = len(sys.argv) > 1 and sys.argv[1] == "--raw"
    asyncio.run(discover_devices(raw=raw_mode))
