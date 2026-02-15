"""
Configuration management - loads device whitelist and provides ID resolution.
"""

import json
import logging
from pathlib import Path

from kasa import Credentials

from .models import DeviceInfo, normalize_mac, mac_to_id

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).parent.parent / "config"


class ConfigManager:
    """Loads and manages the device whitelist configuration."""

    def __init__(self, config_dir: Path | None = None):
        self._config_dir = config_dir or DEFAULT_CONFIG_DIR
        self._whitelist_path = self._config_dir / "devices.json"
        self._whitelist: dict[str, DeviceInfo] = {}  # MAC -> DeviceInfo
        self._id_to_mac: dict[str, str] = {}  # ID -> MAC

    def load(self) -> dict[str, DeviceInfo]:
        """Load device whitelist from config/devices.json."""
        if not self._whitelist_path.exists():
            logger.warning(f"Whitelist not found: {self._whitelist_path}")
            self._whitelist = {}
            self._id_to_mac = {}
            return self._whitelist

        try:
            with open(self._whitelist_path) as f:
                data = json.load(f)

            whitelist: dict[str, DeviceInfo] = {}
            id_to_mac: dict[str, str] = {}

            for device in data.get("devices", []):
                mac = normalize_mac(device["mac"])
                device_id = mac_to_id(mac)
                name = device.get("name") or device_id
                target = device["target"]

                credentials = None
                username = device.get("username")
                password = device.get("password")
                if username and password:
                    credentials = Credentials(username=username, password=password)

                whitelist[mac] = DeviceInfo(
                    mac=mac,
                    name=name,
                    target=target,
                    id=device_id,
                    credentials=credentials,
                )
                id_to_mac[device_id] = mac
                logger.debug(
                    f"Loaded device: {name} ({mac}) target={target} "
                    f"auth={'yes' if credentials else 'no'}"
                )

            self._whitelist = whitelist
            self._id_to_mac = id_to_mac
            logger.info(f"Loaded {len(whitelist)} devices from whitelist")
            return self._whitelist
        except Exception as e:
            logger.error(f"Failed to load whitelist: {e}")
            self._whitelist = {}
            self._id_to_mac = {}
            return self._whitelist

    def resolve_id(self, device_id: str) -> str | None:
        """Resolve device ID to MAC address. Returns None if not found."""
        return self._id_to_mac.get(device_id)

    def get_device_id(self, mac: str) -> str | None:
        """Get device ID for a MAC address. Returns None if not in whitelist."""
        mac = normalize_mac(mac)
        if mac in self._whitelist:
            return self._whitelist[mac].id
        return None

    @property
    def whitelist(self) -> dict[str, DeviceInfo]:
        return self._whitelist
