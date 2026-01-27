"""
Device Manager - MAC-based device management with smart caching.

Manages Kasa devices using MAC addresses as stable identifiers,
with IP address caching to avoid frequent network discovery.

Design principles:
- MAC is device identity, IP is just cache
- Topology can be cached, state is always queried live
- Each operation creates a fresh TCP connection (stateless)
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from kasa import Credentials, Device, DeviceConfig, Discover
from kasa.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

# Default paths - config is at project root, not in app folder
DEFAULT_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Connection settings
CONNECTION_TIMEOUT = 10  # Seconds to wait for device response (default is 5)
CONNECTION_RETRIES = 3   # Number of retry attempts on connection failure
RETRY_DELAY = 0.5        # Seconds between retries
COMMAND_INTERVAL = 0.5   # Minimum seconds between commands to same device


# === Custom Exceptions ===
class DeviceOfflineError(Exception):
    """Device confirmed offline (cannot connect after retries)."""
    pass


class DeviceOperationError(Exception):
    """Operation failed but device may still be online."""
    pass


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase with colons (AA:BB:CC:DD:EE:FF)."""
    clean = mac.upper().replace("-", "").replace(":", "").replace(".", "")
    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


def mac_to_id(mac: str) -> str:
    """Generate a stable device ID from MAC address (8-char hash)."""
    normalized = normalize_mac(mac)
    return hashlib.sha256(normalized.encode()).hexdigest()[:8]


@dataclass
class CacheEntry:
    """
    Cache entry for a device.

    Stores:
    - Connection info (IP, last_seen)
    - Topology info (alias, model, is_strip, children) - hardware facts, rarely change
    - Last known state (for UI reference when offline) - NOT used for control decisions
    """

    # === Connection info ===
    ip: str
    last_seen: datetime

    # === Topology info (hardware facts, cached) ===
    alias: str | None = None
    model: str | None = None
    is_strip: bool = False
    children: list[dict] | None = None  # [{id, alias}, ...]

    # === Last known state (UI reference only) ===
    last_state: dict | None = None  # {is_on, last_updated, children: [{id, is_on}]}


@dataclass
class DeviceInfo:
    """Information about a whitelisted device."""

    mac: str
    name: str
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = mac_to_id(self.mac)


class DeviceManager:
    """
    Manages Kasa devices using MAC addresses with smart IP caching.

    Features:
    - MAC-based device identification (stable across IP changes)
    - In-memory cache (topology + last state for offline UI)
    - Smart discovery: only at startup or on connection failure
    - Per-device locking for concurrent access
    """

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR

        # Paths
        self._env_path = self.config_dir / ".env"
        self._whitelist_path = self.config_dir / "devices.json"

        # State
        self._whitelist: dict[str, DeviceInfo] = {}  # MAC -> DeviceInfo
        self._id_to_mac: dict[str, str] = {}  # ID -> MAC (reverse lookup)
        self._cache: dict[str, CacheEntry] = {}  # MAC -> CacheEntry (in-memory only)
        self._credentials: Credentials | None = None

        # Concurrency control
        self._device_locks: dict[str, asyncio.Lock] = {}  # MAC -> Lock
        self._last_command_time: dict[str, float] = {}  # MAC -> timestamp (rate limiting)

    def _load_credentials(self) -> Credentials | None:
        """Load credentials from config/.env if it exists."""
        if not self._env_path.exists():
            return None

        load_dotenv(self._env_path)
        username = os.getenv("KASA_USERNAME")
        password = os.getenv("KASA_PASSWORD")

        if not username or not password:
            return None

        return Credentials(username=username, password=password)

    def _load_whitelist(self) -> dict[str, DeviceInfo]:
        """Load device whitelist from config/devices.json."""
        if not self._whitelist_path.exists():
            logger.warning(f"Whitelist not found: {self._whitelist_path}")
            return {}

        try:
            with open(self._whitelist_path) as f:
                data = json.load(f)

            whitelist = {}
            self._id_to_mac = {}  # Reset ID mapping

            for device in data.get("devices", []):
                mac = normalize_mac(device["mac"])
                device_id = mac_to_id(mac)
                # name is optional, fallback to device ID
                name = device.get("name") or device_id
                whitelist[mac] = DeviceInfo(mac=mac, name=name, id=device_id)
                self._id_to_mac[device_id] = mac

            logger.info(f"Loaded {len(whitelist)} devices from whitelist")
            return whitelist
        except Exception as e:
            logger.error(f"Failed to load whitelist: {e}")
            return {}

    def _get_lock(self, mac: str) -> asyncio.Lock:
        """Get or create a lock for a device (thread-safe via setdefault)."""
        return self._device_locks.setdefault(mac, asyncio.Lock())

    async def _wait_for_rate_limit(self, mac: str) -> None:
        """Wait if needed to respect per-device command interval."""
        now = time.monotonic()
        last_time = self._last_command_time.get(mac, 0)
        elapsed = now - last_time

        if elapsed < COMMAND_INTERVAL:
            wait_time = COMMAND_INTERVAL - elapsed
            logger.debug(f"Rate limiting {mac}: waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)

        self._last_command_time[mac] = time.monotonic()

    async def initialize(self) -> None:
        """Initialize the device manager (load configs, run initial discovery)."""
        self._credentials = self._load_credentials()
        self._whitelist = self._load_whitelist()

        # Run initial discovery to populate cache
        logger.info("Running initial device discovery...")
        await self.discover_all()

    async def shutdown(self) -> None:
        """Shutdown the device manager (clear state)."""
        self._cache.clear()

    async def _connect_by_ip(self, ip: str) -> Device | None:
        """
        Try to connect to a device by IP address with retry logic.

        Uses Device.connect() instead of discover_single() to skip UDP discovery.
        Retries on transient network failures.
        """
        config = DeviceConfig(
            host=ip,
            credentials=self._credentials,
            timeout=CONNECTION_TIMEOUT,
        )

        for attempt in range(CONNECTION_RETRIES):
            try:
                device = await Device.connect(config=config)
                await device.update()
                return device
            except Exception as e:
                if attempt < CONNECTION_RETRIES - 1:
                    logger.debug(f"Connection to {ip} failed (attempt {attempt + 1}): {e}")
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    logger.debug(f"Failed to connect to {ip} after {CONNECTION_RETRIES} attempts: {e}")

        return None

    async def _discover_device_ip(self, target_mac: str) -> str | None:
        """Discover a device's IP by its MAC address via network scan."""
        target_mac = normalize_mac(target_mac)
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
            on_discovered=on_discovered,
            credentials=self._credentials,
        )

        return found_ip

    async def discover_all(self) -> None:
        """Discover all whitelisted devices and update cache."""
        logger.info("Starting full device discovery...")
        discovered_macs: set[str] = set()

        async def on_discovered(device: Device) -> None:
            device_mac = getattr(device, "mac", None)
            if device_mac:
                try:
                    mac = normalize_mac(device_mac)
                    discovered_macs.add(mac)

                    # Update cache if device is in whitelist
                    if mac in self._whitelist:
                        try:
                            await device.update()
                            self._update_cache_from_device(mac, device)
                            logger.info(
                                f"Found whitelisted device: {device.alias or self._whitelist[mac].name} "
                                f"({device.model}) at {device.host}"
                            )
                        except AuthenticationError:
                            # Device requires different credentials - cache IP but skip state
                            logger.warning(
                                f"Authentication failed for device at {device.host} ({mac}). "
                                "Check KASA_USERNAME and KASA_PASSWORD in config/.env"
                            )
                            # Still cache the IP so we know where the device is
                            self._cache[mac] = CacheEntry(
                                ip=device.host,
                                last_seen=datetime.now(),
                                alias=self._whitelist[mac].name,
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to update device at {device.host} ({mac}): {e}"
                            )
                except ValueError:
                    pass

        await Discover.discover(
            on_discovered=on_discovered,
            credentials=self._credentials,
        )

        # Log summary
        found = len(discovered_macs & set(self._whitelist.keys()))
        total = len(self._whitelist)
        logger.info(f"Discovery complete: {found}/{total} whitelisted devices found")

    def _update_cache_from_device(self, mac: str, device: Device) -> None:
        """Update cache entry from a connected device (topology + state)."""
        now = datetime.now()

        # Build children list for strips
        children_topology = None
        children_state = None
        is_strip = hasattr(device, "children") and len(device.children) > 0

        if is_strip:
            children_topology = [
                {
                    "id": child.id if hasattr(child, "id") else str(i),
                    "alias": child.alias,
                }
                for i, child in enumerate(device.children)
            ]
            children_state = [
                {
                    "id": child.id if hasattr(child, "id") else str(i),
                    "is_on": child.is_on,
                }
                for i, child in enumerate(device.children)
            ]

        self._cache[mac] = CacheEntry(
            ip=device.host,
            last_seen=now,
            alias=device.alias,
            model=device.model,
            is_strip=is_strip,
            children=children_topology,
            last_state={
                "is_on": device.is_on,
                "last_updated": now.isoformat(),
                "children": children_state,
            },
        )

    async def get_device(self, mac: str) -> Device | None:
        """
        Get a connected device by MAC address.

        Strategy:
        1. If cached IP exists, try connecting to it
        2. If connection fails, trigger discovery to find new IP
        3. Return connected device or None if not found

        Note: This method does NOT acquire device lock. Caller should lock if needed.
        """
        mac = normalize_mac(mac)

        if mac not in self._whitelist:
            logger.warning(f"Device {mac} not in whitelist")
            return None

        # Rate limit per device
        await self._wait_for_rate_limit(mac)

        # Try cached IP first
        if mac in self._cache:
            cached = self._cache[mac]
            logger.debug(f"Trying cached IP {cached.ip} for {mac}")

            device = await self._connect_by_ip(cached.ip)
            if device:
                # Verify MAC matches
                device_mac = getattr(device, "mac", None)
                if device_mac and normalize_mac(device_mac) == mac:
                    self._update_cache_from_device(mac, device)
                    return device
                else:
                    # IP belongs to different device now
                    logger.warning(f"IP {cached.ip} no longer belongs to {mac}")
                    await device.disconnect()

        # Cache miss or connection failed - discover
        logger.info(f"Discovering IP for {mac}...")
        ip = await self._discover_device_ip(mac)

        if not ip:
            logger.warning(f"Device {mac} not found on network")
            return None

        # Connect to discovered IP
        device = await self._connect_by_ip(ip)
        if device:
            self._update_cache_from_device(mac, device)
            return device

        logger.warning(f"Device {mac} found at {ip} but connection failed")
        return None

    def _build_device_response(
        self,
        mac: str,
        device: Device | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """
        Build a standardized device response dict.

        Args:
            mac: Device MAC address
            device: Connected device (None if offline)
            error: Error message if any
        """
        info = self._whitelist[mac]
        result: dict[str, Any] = {
            "id": info.id,
            "name": info.name,
            "status": "offline",
        }

        if device:
            is_strip = hasattr(device, "children") and len(device.children) > 0
            result.update({
                "status": "online",
                "alias": device.alias,
                "model": device.model,
                "is_on": device.is_on,
                "is_strip": is_strip,
                "children": [
                    {
                        "id": child.id if hasattr(child, "id") else str(i),
                        "alias": child.alias,
                        "is_on": child.is_on,
                    }
                    for i, child in enumerate(device.children)
                ] if is_strip else [],
            })
        else:
            result["status"] = "offline"
            if mac in self._cache:
                cached = self._cache[mac]
                result.update({
                    "alias": cached.alias,
                    "model": cached.model,
                    "is_strip": cached.is_strip,
                    "children": cached.children or [],
                    "last_state": cached.last_state,
                })

        if error:
            result["error"] = error

        return result

    async def get_all_devices(self) -> list[dict[str, Any]]:
        """
        Get status of all whitelisted devices.

        Returns list of device info dicts with status field.
        For offline devices, returns cached topology + last_state.
        """

        async def fetch_one(mac: str) -> dict[str, Any]:
            try:
                device = await self.get_device(mac)
                return self._build_device_response(mac, device)
            except Exception as e:
                logger.error(f"Error getting device {mac}: {e}")
                return self._build_device_response(mac, None, error=str(e))

        results = await asyncio.gather(*[fetch_one(mac) for mac in self._whitelist])
        return list(results)

    async def get_device_status(self, device_id: str) -> dict[str, Any]:
        """
        Get status of a single device.

        Returns device info dict with status field.
        """
        mac = self.resolve_id(device_id)
        if not mac:
            raise ValueError(f"Device {device_id} not found")

        try:
            device = await self.get_device(mac)
            return self._build_device_response(mac, device)
        except Exception as e:
            logger.error(f"Error getting device {mac}: {e}")
            return self._build_device_response(mac, None, error=str(e))

    async def control_device(
        self,
        device_id: str,
        action: str,
        child_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Control a device (turn on/off).

        Args:
            device_id: Device ID (hash of MAC)
            action: "on" or "off"
            child_id: Optional child outlet ID for power strips

        Returns:
            Dict with success status and updated device state

        Raises:
            ValueError: Invalid device_id, child_id, or action
            DeviceOfflineError: Device confirmed offline
            DeviceOperationError: Operation failed but device may be online
        """
        mac = self.resolve_id(device_id)
        if not mac:
            raise ValueError(f"Device {device_id} not found")

        if action not in ("on", "off"):
            raise ValueError(f"Invalid action: {action}. Use 'on' or 'off'")

        # Acquire device lock to serialize operations
        async with self._get_lock(mac):
            device = await self.get_device(mac)

            if not device:
                raise DeviceOfflineError(f"Device {device_id} is offline")

            try:
                # Determine target (device or child)
                target = device
                if child_id:
                    child_found = False
                    for i, child in enumerate(getattr(device, "children", [])):
                        child_identifier = child.id if hasattr(child, "id") else str(i)
                        if child_identifier == child_id:
                            target = child
                            child_found = True
                            break

                    if not child_found:
                        raise ValueError(f"Child outlet {child_id} not found")

                # Execute action
                if action == "on":
                    await target.turn_on()
                else:
                    await target.turn_off()

                # Refresh state
                await device.update()
                self._update_cache_from_device(mac, device)

            except DeviceOfflineError:
                raise
            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Operation failed on device {device_id}: {e}")
                # Check if device is still reachable
                device = await self.get_device(mac)
                if not device:
                    raise DeviceOfflineError(f"Device {device_id} is offline")
                raise DeviceOperationError(f"Operation failed: {e}")

            # Build response
            result: dict[str, Any] = {
                "success": True,
                "id": device_id,
                "is_on": device.is_on,
            }

            if hasattr(device, "children") and device.children:
                result["children"] = [
                    {
                        "id": child.id if hasattr(child, "id") else str(i),
                        "alias": child.alias,
                        "is_on": child.is_on,
                    }
                    for i, child in enumerate(device.children)
                ]

            return result

    async def refresh_device(self, device_id: str) -> dict[str, Any]:
        """
        Refresh a single device (targeted discover).

        Returns:
            Dict with success status and device info
        """
        mac = self.resolve_id(device_id)
        if not mac:
            raise ValueError(f"Device {device_id} not found")

        # Attempt to reconnect
        logger.info(f"Refreshing device {device_id} ({mac})...")
        device = await self.get_device(mac)

        if device:
            return {
                "success": True,
                "id": device_id,
                "online": True,
                "ip": device.host,
            }
        else:
            return {
                "success": False,
                "id": device_id,
                "online": False,
                "error": "Device not found on network",
            }

    def get_whitelist(self) -> list[DeviceInfo]:
        """Get all whitelisted devices."""
        return list(self._whitelist.values())

    def reload_whitelist(self) -> None:
        """Reload whitelist from config file."""
        self._whitelist = self._load_whitelist()

    def resolve_id(self, device_id: str) -> str | None:
        """Resolve device ID to MAC address. Returns None if not found."""
        return self._id_to_mac.get(device_id)

    def get_device_id(self, mac: str) -> str | None:
        """Get device ID for a MAC address. Returns None if not in whitelist."""
        mac = normalize_mac(mac)
        if mac in self._whitelist:
            return self._whitelist[mac].id
        return None
