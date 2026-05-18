"""Domain entities for devices and their state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from .utils import mac_to_id

if TYPE_CHECKING:
    from .backend import DeviceBackend


class DeviceStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"


@dataclass
class DeviceInfo:
    """Base class for all device configurations. Protocol-agnostic fields only."""

    mac: str
    name: str
    type: str  # "kasa" | "miio"
    id: str = ""
    group: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = mac_to_id(self.mac)


@dataclass(frozen=True)
class ChildState:
    """State of a single child outlet on a power strip."""

    id: str
    alias: str | None
    is_on: bool


@dataclass(frozen=True)
class DeviceState:
    """Immutable snapshot of device-reported state. is_on is None when OFFLINE."""

    id: str
    status: DeviceStatus
    is_on: bool | None = None
    alias: str | None = None
    model: str | None = None
    children: tuple[ChildState, ...] | None = None
    last_updated: datetime | None = None

    @property
    def is_strip(self) -> bool:
        return self.children is not None


@dataclass
class Device:
    """Aggregate: per-device config, backend, and current state in one place."""

    info: DeviceInfo
    backend: DeviceBackend
    state: DeviceState


def make_offline_state(
    device_id: str, previous: DeviceState | None = None
) -> DeviceState:
    """Build an offline snapshot, preserving topology from previous if available."""
    return DeviceState(
        id=device_id,
        status=DeviceStatus.OFFLINE,
        alias=previous.alias if previous else None,
        model=previous.model if previous else None,
        children=previous.children if previous else None,
        last_updated=previous.last_updated if previous else None,
    )
