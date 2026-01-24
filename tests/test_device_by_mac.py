"""Find and control device by MAC address."""

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


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase with colons."""
    # Remove common separators and convert to uppercase
    clean = mac.upper().replace("-", "").replace(":", "").replace(".", "")
    # Format as XX:XX:XX:XX:XX:XX
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


async def find_device_by_mac(target_mac: str):
    """Find a device by its MAC address."""
    credentials = load_credentials()
    target_mac = normalize_mac(target_mac)

    print(f"Searching for device with MAC: {target_mac}")
    print("Discovering devices...\n")

    found_device = None

    async def on_device_discovered(device):
        nonlocal found_device
        device_mac = getattr(device, "mac", None)
        if device_mac:
            normalized = normalize_mac(device_mac)
            if normalized == target_mac:
                found_device = device
                print(f"Found! IP: {device.host}")

    await Discover.discover(
        on_discovered=on_device_discovered,
        credentials=credentials,
    )

    return found_device


def print_device_info(device):
    """Print device information."""
    print("\n=== Device Info ===")
    print(f"  Alias: {device.alias}")
    print(f"  Model: {device.model}")
    print(f"  Host: {device.host}")
    print(f"  MAC: {device.mac}")
    print(f"  Is On: {device.is_on}")

    if hasattr(device, "children") and device.children:
        print(f"\n=== Children ({len(device.children)} outlets) ===")
        for i, child in enumerate(device.children):
            print(f"  [{i}] {child.alias}: {'ON' if child.is_on else 'OFF'}")


async def control_device(device, action: str, child_index: int | None = None):
    """Control device or child outlet."""
    target = device
    target_name = device.alias

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


async def main(mac: str, action: str | None = None, child_index: int | None = None):
    """Main function."""
    device = await find_device_by_mac(mac)

    if not device:
        print(f"\nDevice with MAC {normalize_mac(mac)} not found.")
        return

    try:
        await device.update()
        print_device_info(device)

        if action:
            await control_device(device, action, child_index)
            print_device_info(device)
    finally:
        await device.disconnect()


def print_usage():
    """Print usage information."""
    print("Usage: python test_device_by_mac.py <mac> [action] [child_index]")
    print()
    print("Arguments:")
    print("  mac          Device MAC address (required)")
    print("               Formats: AA:BB:CC:DD:EE:FF, AA-BB-CC-DD-EE-FF, AABBCCDDEEFF")
    print("  action       on, off, toggle (optional)")
    print("  child_index  Outlet index for strips (optional, 0-based)")
    print()
    print("Examples:")
    print("  python test_device_by_mac.py AA:BB:CC:DD:EE:FF")
    print("  python test_device_by_mac.py AA-BB-CC-DD-EE-FF on")
    print("  python test_device_by_mac.py AABBCCDDEEFF toggle")
    print("  python test_device_by_mac.py AA:BB:CC:DD:EE:FF on 0    # Strip outlet 0")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    mac = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else None
    child_index = int(sys.argv[3]) if len(sys.argv) > 3 else None

    asyncio.run(main(mac, action, child_index))
