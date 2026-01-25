"""
Device Manager - MAC-based device management with smart caching.

Manages Kasa devices using MAC addresses as stable identifiers,
with IP address caching to avoid frequent network discovery.
"""

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from kasa import Credentials, Device, Discover

logger = logging.getLogger(__name__)

# Default paths - config is at project root, not in app folder
DEFAULT_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Cooldown period before retrying discovery for offline devices
OFFLINE_COOLDOWN = timedelta(minutes=5)
MAX_DISCOVERY_RETRIES = 3  # Number of failures before cooldown


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
    """Cache entry for a device's IP address."""

    ip: str
    last_seen: datetime

    def to_dict(self) -> dict:
        return {
            "ip": self.ip,
            "last_seen": self.last_seen.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        return cls(
            ip=data["ip"],
            last_seen=datetime.fromisoformat(data["last_seen"]),
        )


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
    - Persistent IP cache to avoid frequent discovery
    - Smart discovery: only when cache miss or connection failure
    - Periodic background discovery (configurable interval)
    """

    def __init__(
        self,
        config_dir: Path | None = None,
        discovery_interval: timedelta = timedelta(hours=4),
    ):
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self.discovery_interval = discovery_interval

        # Paths
        self._env_path = self.config_dir / ".env"
        self._whitelist_path = self.config_dir / "devices.json"
        self._cache_path = self.config_dir / "cache.json"

        # State
        self._whitelist: dict[str, DeviceInfo] = {}  # MAC -> DeviceInfo
        self._id_to_mac: dict[str, str] = {}  # ID -> MAC (reverse lookup)
        self._cache: dict[str, CacheEntry] = {}  # MAC -> CacheEntry
        self._last_discovery: datetime | None = None
        self._credentials: Credentials | None = None
        self._connected_devices: dict[str, Device] = {}  # MAC -> Device
        self._offline_until: dict[str, datetime] = {}  # MAC -> retry_after time
        self._discovery_failures: dict[str, int] = {}  # MAC -> failure count

        # Background task
        self._discovery_task: asyncio.Task | None = None

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

    def _load_cache(self) -> dict[str, CacheEntry]:
        """Load IP cache from config/cache.json."""
        if not self._cache_path.exists():
            return {}

        try:
            with open(self._cache_path) as f:
                data = json.load(f)

            self._last_discovery = (
                datetime.fromisoformat(data["last_discovery"])
                if data.get("last_discovery")
                else None
            )

            cache = {}
            for mac, entry in data.get("devices", {}).items():
                cache[normalize_mac(mac)] = CacheEntry.from_dict(entry)

            logger.info(f"Loaded {len(cache)} devices from cache")
            return cache
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            return {}

    def _save_cache(self) -> None:
        """Save IP cache to config/cache.json."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)

            data = {
                "last_discovery": (
                    self._last_discovery.isoformat() if self._last_discovery else None
                ),
                "devices": {mac: entry.to_dict() for mac, entry in self._cache.items()},
            }

            with open(self._cache_path, "w") as f:
                json.dump(data, f, indent=2)

            logger.debug("Cache saved")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    async def initialize(self) -> None:
        """Initialize the device manager (load configs, start background tasks)."""
        self._credentials = self._load_credentials()
        self._whitelist = self._load_whitelist()
        self._cache = self._load_cache()

        # Start background discovery task
        self._discovery_task = asyncio.create_task(self._background_discovery_loop())

    async def shutdown(self) -> None:
        """Shutdown the device manager (save cache, disconnect devices)."""
        # Cancel background task
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass

        # Disconnect all devices
        for device in self._connected_devices.values():
            try:
                await device.disconnect()
            except Exception:
                pass

        self._connected_devices.clear()
        self._save_cache()

    async def _background_discovery_loop(self) -> None:
        """Background task that periodically discovers devices."""
        while True:
            try:
                # Wait until next discovery is needed
                if self._last_discovery:
                    next_discovery = self._last_discovery + self.discovery_interval
                    wait_seconds = (next_discovery - datetime.now()).total_seconds()
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)

                # Perform discovery
                await self.discover_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background discovery error: {e}")
                await asyncio.sleep(60)  # Wait before retry on error

    async def _connect_by_ip(self, ip: str) -> Device | None:
        """Try to connect to a device by IP address."""
        try:
            device = await Discover.discover_single(ip, credentials=self._credentials)
            await device.update()
            return device
        except Exception as e:
            logger.debug(f"Failed to connect to {ip}: {e}")
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

    async def discover_all(self, force: bool = False) -> None:
        """
        Discover all whitelisted devices and update cache.

        Args:
            force: If True, discover even if interval hasn't elapsed.
        """
        now = datetime.now()

        # Clear offline cooldown and failure counts on force discover
        if force:
            self._offline_until.clear()
            self._discovery_failures.clear()

        if not force and self._last_discovery:
            elapsed = now - self._last_discovery
            if elapsed < self.discovery_interval:
                logger.debug(f"Skipping discovery, last run {elapsed} ago")
                return

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
                        self._cache[mac] = CacheEntry(ip=device.host, last_seen=now)
                        logger.info(
                            f"Found whitelisted device: {self._whitelist[mac].name} "
                            f"({mac}) at {device.host}"
                        )
                except ValueError:
                    pass

        await Discover.discover(
            on_discovered=on_discovered,
            credentials=self._credentials,
        )

        self._last_discovery = now
        self._save_cache()

        # Log summary
        found = len(discovered_macs & set(self._whitelist.keys()))
        total = len(self._whitelist)
        logger.info(f"Discovery complete: {found}/{total} whitelisted devices found")

    def _handle_discovery_failure(self, mac: str, reason: str) -> None:
        """Handle discovery/connection failure with retry counting."""
        failures = self._discovery_failures.get(mac, 0) + 1
        self._discovery_failures[mac] = failures

        if failures >= MAX_DISCOVERY_RETRIES:
            self._offline_until[mac] = datetime.now() + OFFLINE_COOLDOWN
            self._discovery_failures[mac] = 0  # Reset for next cycle
            logger.warning(
                f"Device {mac} {reason} (attempt {failures}/{MAX_DISCOVERY_RETRIES}), "
                f"cooldown for {OFFLINE_COOLDOWN}"
            )
        else:
            logger.warning(
                f"Device {mac} {reason} (attempt {failures}/{MAX_DISCOVERY_RETRIES})"
            )
        return None

    async def get_device(self, mac: str) -> Device | None:
        """
        Get a connected device by MAC address.

        Strategy:
        1. If cached IP exists, try connecting to it
        2. If connection fails, trigger discovery to find new IP
        3. Return connected device or None if not found
        """
        mac = normalize_mac(mac)

        if mac not in self._whitelist:
            logger.warning(f"Device {mac} not in whitelist")
            return None

        # Try cached IP first
        if mac in self._cache:
            cached = self._cache[mac]
            logger.debug(f"Trying cached IP {cached.ip} for {mac}")

            device = await self._connect_by_ip(cached.ip)
            if device:
                # Verify MAC matches
                device_mac = getattr(device, "mac", None)
                if device_mac and normalize_mac(device_mac) == mac:
                    self._connected_devices[mac] = device
                    # Update last_seen and reset failure count
                    self._cache[mac] = CacheEntry(
                        ip=cached.ip, last_seen=datetime.now()
                    )
                    self._discovery_failures.pop(mac, None)
                    self._save_cache()
                    return device
                else:
                    # IP belongs to different device now
                    logger.warning(f"IP {cached.ip} no longer belongs to {mac}")
                    await device.disconnect()

        # Check if device is in offline cooldown
        if mac in self._offline_until:
            if datetime.now() < self._offline_until[mac]:
                logger.debug(f"Device {mac} in offline cooldown, skipping discovery")
                return None
            else:
                del self._offline_until[mac]  # Cooldown expired
                self._discovery_failures.pop(mac, None)  # Reset retries

        # Cache miss or connection failed - discover
        logger.info(f"Discovering IP for {mac}...")
        ip = await self._discover_device_ip(mac)

        if not ip:
            return self._handle_discovery_failure(mac, "not found on network")

        # Connect to discovered IP
        device = await self._connect_by_ip(ip)
        if device:
            self._connected_devices[mac] = device
            self._cache[mac] = CacheEntry(ip=ip, last_seen=datetime.now())
            self._discovery_failures.pop(mac, None)  # Reset on success
            self._save_cache()
            return device

        # Discovery found IP but connection failed - also count as failure
        return self._handle_discovery_failure(mac, f"found at {ip} but connection failed")

    async def get_all_devices(self) -> list[dict[str, Any]]:
        """
        Get status of all whitelisted devices.

        Returns list of device info dicts, including offline devices.
        """
        results = []

        for mac, info in self._whitelist.items():
            device_data: dict[str, Any] = {
                "id": info.id,
                "name": info.name,
                "online": False,
            }

            try:
                device = await self.get_device(mac)
                if device:
                    await device.update()
                    device_data.update(
                        {
                            "online": True,
                            "alias": device.alias,
                            "model": device.model,
                            "is_on": device.is_on,
                            "is_strip": (
                                hasattr(device, "children") and len(device.children) > 0
                            ),
                        }
                    )

                    if device_data["is_strip"]:
                        device_data["children"] = [
                            {
                                "id": (
                                    child.id if hasattr(child, "id") else str(i)
                                ),
                                "alias": child.alias,
                                "is_on": child.is_on,
                            }
                            for i, child in enumerate(device.children)
                        ]
                    else:
                        device_data["children"] = []
            except Exception as e:
                logger.error(f"Error getting device {mac}: {e}")
                device_data["error"] = str(e)

            results.append(device_data)

        return results

    async def control_device(
        self,
        device_id: str,
        action: str,
        child_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Control a device (turn on/off or cycle).

        Args:
            device_id: Device ID (hash of MAC)
            action: "on", "off", or "cycle"
            child_id: Optional child outlet ID for power strips

        Returns:
            Dict with success status and updated device state
        """
        mac = self.resolve_id(device_id)
        if not mac:
            raise ValueError(f"Device {device_id} not found")

        device = await self.get_device(mac)

        if not device:
            raise ValueError(f"Device {device_id} is offline")

        await device.update()

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
        elif action == "off":
            await target.turn_off()
        elif action == "cycle":
            await target.turn_off()
            await asyncio.sleep(3)
            await target.turn_on()
        else:
            raise ValueError(f"Invalid action: {action}. Use 'on', 'off', or 'cycle'")

        # Refresh state
        await device.update()

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
