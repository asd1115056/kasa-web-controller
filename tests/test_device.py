"""Device control example using python-kasa."""

import asyncio
import os
import sys
from pathlib import Path

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


async def connect_device(host: str) -> object | None:
    """Connect to a single device by IP address."""
    credentials = load_credentials()

    print(f"Connecting to {host}...")

    try:
        device = await Discover.discover_single(host, credentials=credentials)
        await device.update()
        return device
    except Exception as e:
        print(f"Failed to connect: {type(e).__name__}: {e}")
        return None


def print_device_info(device):
    """Print device information."""
    print("\n=== Device Info ===")
    print(f"  Alias: {device.alias}")
    print(f"  Model: {device.model}")
    print(f"  Host: {device.host}")
    print(f"  MAC: {device.mac}")
    print(f"  Is On: {device.is_on}")

    if hasattr(device, "rssi") and device.rssi:
        print(f"  RSSI: {device.rssi}")

    # Check if it's a strip with children
    if hasattr(device, "children") and device.children:
        print(f"\n=== Children ({len(device.children)} outlets) ===")
        for i, child in enumerate(device.children):
            print(f"  [{i}] {child.alias}: {'ON' if child.is_on else 'OFF'}")


async def control_device(device, action: str, child_index: int | None = None):
    """Control device or child outlet."""
    target = device
    target_name = device.alias

    # If child_index specified, target the child
    if child_index is not None:
        if not hasattr(device, "children") or not device.children:
            print("Error: Device has no children (not a strip)")
            return
        if child_index < 0 or child_index >= len(device.children):
            print(f"Error: Invalid child index. Valid range: 0-{len(device.children)-1}")
            return
        target = device.children[child_index]
        target_name = target.alias

    print(f"\nExecuting '{action}' on {target_name}...")

    if action == "on":
        await target.turn_on()
    elif action == "off":
        await target.turn_off()
    elif action == "toggle":
        if target.is_on:
            await target.turn_off()
        else:
            await target.turn_on()
    else:
        print(f"Unknown action: {action}")
        return

    await device.update()
    print(f"Done. {target_name} is now {'ON' if target.is_on else 'OFF'}")


async def main(host: str, action: str | None = None, child_index: int | None = None):
    """Main function."""
    device = await connect_device(host)
    if not device:
        return

    try:
        print_device_info(device)

        if action:
            await control_device(device, action, child_index)
            print_device_info(device)
    finally:
        await device.disconnect()


def print_usage():
    """Print usage information."""
    print("Usage: python test_device.py <host> [action] [child_index]")
    print()
    print("Arguments:")
    print("  host         Device IP address (required)")
    print("  action       on, off, toggle (optional)")
    print("  child_index  Outlet index for strips (optional, 0-based)")
    print()
    print("Examples:")
    print("  python test_device.py 192.168.1.100              # Show device info")
    print("  python test_device.py 192.168.1.100 on           # Turn on")
    print("  python test_device.py 192.168.1.100 off          # Turn off")
    print("  python test_device.py 192.168.1.100 toggle       # Toggle state")
    print("  python test_device.py 192.168.1.100 on 0         # Turn on outlet 0 (strip)")
    print("  python test_device.py 192.168.1.100 toggle 2     # Toggle outlet 2 (strip)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    host = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else None
    child_index = int(sys.argv[3]) if len(sys.argv) > 3 else None

    asyncio.run(main(host, action, child_index))
