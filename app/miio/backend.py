"""MiIO protocol backend — stateless UDP, no persistent connection."""

import logging

from ..core.models import (
    BackendPolicy,
    Command,
    DeviceBackend,
    DeviceOfflineError,
    DeviceState,
    DeviceStatus,
)
from .config import MiioDeviceConfig
from . import connection

logger = logging.getLogger(__name__)


class MiioBackend(DeviceBackend[MiioDeviceConfig]):
    """MiIO backend: stateless UDP, exits immediately after each command."""

    policy = BackendPolicy(session_timeout=0.0, command_interval=0.0, command_timeout=25.0)

    def __init__(self) -> None:
        super().__init__()

    async def execute_command(self, cmd: Command, cfg: MiioDeviceConfig) -> DeviceState:
        if not self.ip:
            raise DeviceOfflineError(f"{cfg.name}: IP unknown, device not yet discovered")
        await connection.set_power(self.ip, cfg, cmd.action == "on", cmd.child_id)
        return await connection.get_status(self.ip, cfg)

    async def fetch_state(self, cfg: MiioDeviceConfig, ip: str) -> DeviceState | None:
        state = await connection.get_status(ip, cfg)
        if state.status == DeviceStatus.ONLINE:
            self.ip = ip
            return state
        return None

    async def find_ip(self, cfg: MiioDeviceConfig) -> str | None:
        results = await connection.discover_all({cfg.mac: cfg})
        ip = results.get(cfg.mac)
        if ip:
            self.ip = ip
            logger.info(f"Discovered {cfg.name} at {ip}")
        else:
            logger.warning(f"Broadcast discovery found no result for {cfg.name}")
        return ip
