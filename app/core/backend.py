"""DeviceBackend ABC, BackendPolicy, and Command types."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import ClassVar, Generic, TypeVar

from .models import DeviceInfo, DeviceState


_Cfg = TypeVar("_Cfg", bound=DeviceInfo)


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
    action: str  # "on" | "off"
    child_id: str | None = None
    status: CommandStatus = CommandStatus.QUEUED
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    _future: asyncio.Future[DeviceState] | None = field(default=None, init=False, repr=False)


@dataclass(frozen=True)
class BackendPolicy:
    """Queue-visible behavioral parameters for a DeviceBackend.

    session_timeout: seconds to keep the processor alive after the last command.
        0 = stateless (exit immediately after each command).
    command_interval: minimum seconds between consecutive commands. 0 = no limit.
    command_timeout: hard deadline in seconds for execute_command. 0 = no timeout.
    """

    session_timeout: float = 0.0
    command_interval: float = 0.0
    command_timeout: float = 0.0


class DeviceBackend(ABC, Generic[_Cfg]):
    """Protocol backend interface. One instance per device."""

    policy: ClassVar[BackendPolicy] = BackendPolicy()

    def __init__(self) -> None:
        self.ip: str | None = None

    @abstractmethod
    async def execute_command(self, cmd: Command, cfg: _Cfg) -> DeviceState:
        """Execute a command. Backend owns retry, rediscovery, and connection lifecycle."""

    @abstractmethod
    async def fetch_state(self, cfg: _Cfg, ip: str) -> DeviceState | None:
        """One-shot: connect to ip, verify identity, read state, disconnect.
        Returns None if unreachable or identity mismatch."""

    @abstractmethod
    async def find_ip(self, cfg: _Cfg) -> str | None:
        """Broadcast to locate this device's current IP. Returns IP or None."""

    async def close(self) -> None:
        """Close any open connections on application shutdown."""
