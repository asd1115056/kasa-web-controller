"""Kasa protocol backend with persistent TCP connection and self-managed session timer."""

import asyncio
import logging

from kasa import Device

from ..core.backend import BackendPolicy, Command, DeviceBackend
from ..core.exceptions import DeviceOfflineError
from ..core.models import DeviceState
from .config import KasaDeviceConfig
from ..core.utils import normalize_mac
from .connection import (
    build_device_state,
    connect_device,
    discover_device_ip,
)

logger = logging.getLogger(__name__)


class KasaBackend(DeviceBackend[KasaDeviceConfig]):
    """Kasa backend: persistent TCP connection, self-managed idle timer."""

    policy = BackendPolicy(session_timeout=60.0, command_interval=0.5, command_timeout=25.0)

    def __init__(self) -> None:
        super().__init__()
        self._connection: Device | None = None
        self._close_task: asyncio.Task | None = None

    # ── Public ABC methods ────────────────────────────────────────────────────

    async def execute_command(self, cmd: Command, cfg: KasaDeviceConfig) -> DeviceState:
        """Execute command, reusing or re-establishing the persistent TCP connection."""
        try:
            return await asyncio.wait_for(self._run_command(cmd, cfg), timeout=self.policy.command_timeout or None)
        except asyncio.TimeoutError:
            await self._close_connection()
            raise DeviceOfflineError(f"{cfg.name} did not respond within {self.policy.command_timeout:.0f}s")
        except asyncio.CancelledError:
            await self._close_connection()
            raise

    async def _run_command(self, cmd: Command, cfg: KasaDeviceConfig) -> DeviceState:
        # Cancel the idle-close timer before touching the connection so it cannot
        # disconnect mid-command between await points.
        if self._close_task and not self._close_task.done():
            self._close_task.cancel()
            self._close_task = None

        # Try existing open connection first.
        conn = self._connection
        if conn:
            try:
                await self._execute_action(conn, cmd)
                await conn.update()
                state = build_device_state(cfg, conn)
                self.ip = conn.host
                self._reset_close_timer(cfg.name)
                logger.info(f"Command '{cmd.action}' on {cfg.name} succeeded (existing connection)")
                return state
            except Exception as e:
                logger.warning(f"Command failed on existing connection for {cfg.name}: {e}")
                await self._close_connection()

        # Try cached IP.
        if self.ip:
            logger.debug(f"Connecting to {cfg.name} at cached IP {self.ip}")
            device = await self._connect_verified(self.ip, cfg)
            if device:
                try:
                    await self._execute_action(device, cmd)
                    await device.update()
                    state = build_device_state(cfg, kasa_device=device)
                    self.ip = device.host
                    self._connection = device
                    self._reset_close_timer(cfg.name)
                    logger.info(f"Command '{cmd.action}' on {cfg.name} succeeded (cached IP {self.ip})")
                    return state
                except Exception as e:
                    logger.warning(f"Command failed at cached IP {self.ip} for {cfg.name}: {e}")
                    await self._safe_disconnect(device)

        raise DeviceOfflineError(f"{cfg.name} is offline — use refresh to rediscover")

    async def fetch_state(self, cfg: KasaDeviceConfig, ip: str) -> DeviceState | None:
        """One-shot: connect, verify MAC, read state, disconnect."""
        logger.debug(f"Fetching state for {cfg.name} at {ip}")
        device, error = await connect_device(ip, cfg.credentials)
        if not device:
            logger.warning(f"Cannot reach {cfg.name} at {ip}: {error}")
            return None
        try:
            if not self._mac_matches(device, cfg.mac, cfg.name):
                return None
            state = build_device_state(cfg, kasa_device=device)
            self.ip = device.host
            logger.debug(f"State fetched for {cfg.name}: {'on' if state.is_on else 'off'}")
            return state
        finally:
            await self._safe_disconnect(device)

    async def find_ip(self, cfg: KasaDeviceConfig) -> str | None:
        """Broadcast to locate this device's current IP."""
        logger.debug(f"Broadcasting to find {cfg.name} ({cfg.mac})")
        ip = await discover_device_ip(cfg)
        if ip:
            self.ip = ip
            logger.info(f"Discovered {cfg.name} at {ip}")
        else:
            logger.warning(f"Broadcast discovery found no result for {cfg.name}")
        return ip

    async def close(self) -> None:
        """Cancel the idle timer and close the TCP connection immediately."""
        if self._close_task and not self._close_task.done():
            self._close_task.cancel()
        await self._close_connection()

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _connect_verified(
        self, ip: str, cfg: KasaDeviceConfig
    ) -> Device | None:
        """Connect to ip and verify MAC. Returns device on success, None otherwise."""
        device, error = await connect_device(ip, cfg.credentials)
        if not device:
            logger.debug(f"Connection to {cfg.name} at {ip} failed: {error}")
            return None
        if not self._mac_matches(device, cfg.mac, cfg.name):
            await self._safe_disconnect(device)
            return None
        return device

    def _mac_matches(self, device: Device, expected_mac: str, name: str) -> bool:
        device_mac = getattr(device, "mac", None)
        if not device_mac:
            return True  # cannot verify, assume ok
        try:
            if normalize_mac(device_mac) != expected_mac:
                logger.warning(
                    f"MAC mismatch for {name}: expected {expected_mac}, got {device_mac}"
                )
                return False
        except ValueError:
            logger.warning(f"Unparseable MAC from device at {device.host}: {device_mac!r}")
            return False
        return True

    def _reset_close_timer(self, device_name: str) -> None:
        if self._close_task and not self._close_task.done():
            self._close_task.cancel()
        if self.policy.session_timeout > 0:
            self._close_task = asyncio.create_task(
                self._idle_close(device_name)
            )

    async def _idle_close(self, device_name: str) -> None:
        try:
            await asyncio.sleep(self.policy.session_timeout)
            logger.info(f"Session idle for {self.policy.session_timeout}s — closing connection to {device_name}")
            await self._close_connection()
        except asyncio.CancelledError:
            pass

    async def _close_connection(self) -> None:
        if self._connection:
            await self._safe_disconnect(self._connection)
            self._connection = None

    @staticmethod
    async def _safe_disconnect(device: Device) -> None:
        try:
            await device.disconnect()
        except Exception:
            pass

    async def _execute_action(self, device: Device, cmd: Command) -> None:
        target = device
        if cmd.child_id:
            for child in device.children:
                if child.device_id == cmd.child_id:
                    target = child
                    break
            else:
                raise ValueError(f"Child outlet {cmd.child_id} not found on {device.host}")

        if cmd.action == "on":
            await target.turn_on()
        else:
            await target.turn_off()
