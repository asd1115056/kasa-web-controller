"""
Connection and discovery utilities (stateless functions).

Handles establishing connections and discovering devices on the network.
Does NOT manage connection lifecycle - that's command_queue.py's job.
"""

import asyncio
import logging
from datetime import datetime

from kasa import Credentials, Device, DeviceConfig, Discover
from kasa.exceptions import AuthenticationError

from .models import ChildState, DeviceInfo, DeviceState, normalize_mac

logger = logging.getLogger(__name__)

# Connection settings
CONNECTION_TIMEOUT = 10
CONNECTION_RETRIES = 3
RETRY_DELAY = 0.5
COMMAND_INTERVAL = 0.5


async def connect_device(
    ip: str, credentials: Credentials | None = None
) -> tuple[Device | None, str | None]:
    """Try to connect to a device by IP address.

    Strategy (no-auth first):
    1. Try without credentials
    2. If auth required and credentials provided, retry with credentials
    3. If still fails, return (None, error_reason)

    Returns:
        (device, error_reason) - if successful, error_reason is None.
    """
    last_error: str | None = None

    # Step 1: Try without credentials
    logger.debug(f"Connecting to {ip} without credentials...")
    config_no_auth = DeviceConfig(
        host=ip,
        credentials=None,
        timeout=CONNECTION_TIMEOUT,
    )

    for attempt in range(CONNECTION_RETRIES):
        try:
            device = await Device.connect(config=config_no_auth)
            await device.update()
            logger.debug(f"Connected to {ip} without credentials")
            return device, None
        except AuthenticationError as e:
            logger.debug(f"Device at {ip} requires authentication")
            last_error = f"{type(e).__name__}: {e}"
            break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < CONNECTION_RETRIES - 1:
                logger.debug(f"Connection to {ip} failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(RETRY_DELAY)

    # Step 2: Try with credentials if provided
    if credentials:
        logger.debug(f"Connecting to {ip} with credentials...")
        config_with_auth = DeviceConfig(
            host=ip,
            credentials=credentials,
            timeout=CONNECTION_TIMEOUT,
        )

        for attempt in range(CONNECTION_RETRIES):
            try:
                device = await Device.connect(config=config_with_auth)
                await device.update()
                logger.debug(f"Connected to {ip} with credentials")
                return device, None
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < CONNECTION_RETRIES - 1:
                    logger.debug(
                        f"Connection to {ip} with auth failed (attempt {attempt + 1}): {e}"
                    )
                    await asyncio.sleep(RETRY_DELAY)

    return None, last_error


async def discover_device_ip(device_info: DeviceInfo) -> str | None:
    """Discover a single device's IP by its MAC address via broadcast."""
    target_mac = device_info.mac
    found_ip: str | None = None

    async def on_discovered(device: Device) -> None:
        nonlocal found_ip
        device_mac = getattr(device, "mac", None)
        if device_mac:
            try:
                if normalize_mac(device_mac) == target_mac:
                    found_ip = device.host
                    logger.info(f"Discovered {target_mac} at {found_ip}")
            except ValueError:
                pass

    await Discover.discover(
        target=device_info.target,
        on_discovered=on_discovered,
    )

    return found_ip


async def discover_all(
    whitelist: dict[str, DeviceInfo],
) -> dict[str, str]:
    """Discover all whitelisted devices, return MAC -> IP mapping.

    Groups devices by target to minimize discovery calls.
    """
    logger.info("Starting full device discovery...")

    # Group devices by target
    targets: dict[str, list[DeviceInfo]] = {}
    for info in whitelist.values():
        targets.setdefault(info.target, []).append(info)

    result: dict[str, str] = {}

    for target, devices in targets.items():
        logger.info(f"Discovering on target {target}...")
        device_macs = {d.mac for d in devices}

        async def on_discovered(device: Device) -> None:
            device_mac = getattr(device, "mac", None)
            if device_mac:
                try:
                    mac = normalize_mac(device_mac)
                    if mac in device_macs:
                        result[mac] = device.host
                        name = whitelist[mac].name
                        logger.info(f"Found device: {name} at {device.host}")
                except ValueError:
                    pass

        await Discover.discover(
            target=target,
            on_discovered=on_discovered,
        )

    found = len(result)
    total = len(whitelist)
    logger.info(f"Discovery complete: {found}/{total} whitelisted devices found")
    return result


def build_device_state(
    device_info: DeviceInfo,
    device: Device | None,
    previous_state: DeviceState | None = None,
) -> DeviceState:
    """Build a DeviceState snapshot from a Device object.

    - device is not None: online state with live data
    - device is None: offline state with is_on=None, topology preserved from
      previous_state for UI display
    """
    now = datetime.now().isoformat()

    if device:
        is_strip = hasattr(device, "children") and len(device.children) > 0
        children = None
        if is_strip:
            children = [
                ChildState(
                    id=child.id if hasattr(child, "id") else str(i),
                    alias=child.alias,
                    is_on=child.is_on,
                )
                for i, child in enumerate(device.children)
            ]

        return DeviceState(
            id=device_info.id,
            name=device_info.name,
            status="online",
            is_on=device.is_on,
            alias=device.alias,
            model=device.model,
            is_strip=is_strip,
            children=children,
            last_updated=now,
        )

    # Offline: preserve topology from previous state, clear is_on
    return DeviceState(
        id=device_info.id,
        name=device_info.name,
        status="offline",
        is_on=None,
        alias=previous_state.alias if previous_state else None,
        model=previous_state.model if previous_state else None,
        is_strip=previous_state.is_strip if previous_state else False,
        children=previous_state.children if previous_state else None,
        last_updated=previous_state.last_updated if previous_state else None,
    )
