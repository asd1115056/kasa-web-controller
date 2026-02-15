"""
Shared data types and exceptions for Kasa Web Controller.
"""

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

from kasa import Credentials


# === Custom Exceptions ===
class DeviceOfflineError(Exception):
    """Device confirmed offline (cannot connect after retries)."""
    pass


class DeviceOperationError(Exception):
    """Operation failed but device may still be online."""
    pass


# === Utility Functions ===
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


# === Device Configuration ===
@dataclass
class DeviceInfo:
    """Information about a whitelisted device."""

    mac: str
    name: str
    target: str  # Broadcast address for discovery (e.g., "192.168.1.255")
    id: str = ""
    credentials: Credentials | None = None

    def __post_init__(self):
        if not self.id:
            self.id = mac_to_id(self.mac)


# === Device State ===
@dataclass
class ChildState:
    """State of a single child outlet on a power strip."""

    id: str
    alias: str
    is_on: bool


@dataclass
class DeviceState:
    """Device state snapshot.

    - status="online": is_on, alias, model, children are live data
    - status="offline": is_on=None (untrustworthy), alias/model/is_strip/children
      retain last known topology for UI display
    """

    id: str
    name: str
    status: Literal["online", "offline"]
    is_on: bool | None = None
    alias: str | None = None
    model: str | None = None
    is_strip: bool = False
    children: list[ChildState] | None = None
    last_updated: str | None = None  # ISO format


# === Command (internal, not exposed in API) ===
class CommandStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Command:
    """A device control command submitted to the queue."""

    id: str
    device_id: str
    action: str  # "on" / "off"
    child_id: str | None = None
    status: CommandStatus = CommandStatus.QUEUED
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    result: DeviceState | None = None
    error: str | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event)
