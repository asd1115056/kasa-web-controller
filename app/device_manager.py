"""Thin facade combining config, backends, and state cache via Device aggregates."""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Literal

from .command_queue import CommandQueue, make_command
from .core.config import ConfigManager
from .core.exceptions import DeviceOfflineError, DeviceOperationError
from .core.models import Device, DeviceState, DeviceStatus, make_offline_state
from .core.registry import PROTOCOLS

logger = logging.getLogger(__name__)

POLL_INTERVAL: float = 60.0  # seconds between polling cycles


class DeviceManager:
    """Manages all devices: config, backends, state cache, and polling."""

    DEFAULT_DEVICES_PATH = Path(__file__).parent.parent / "config" / "devices.json"

    def __init__(self, devices_path: Path | None = None) -> None:
        self._config = ConfigManager(devices_path or self.DEFAULT_DEVICES_PATH)
        self._devices: dict[str, Device] = {}
        self._queue: CommandQueue | None = None
        self._poll_task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()

    async def initialize(self) -> None:
        """Load config → discover → build Device aggregates → probe initial state → start polling."""
        self._config.load()

        for type_name, spec in PROTOCOLS.items():
            sub_devices = {
                info.mac: info
                for info in self._config.devices.values()
                if isinstance(info, spec.model)
            }
            if not sub_devices:
                continue

            logger.info(f"Discovering {type_name} devices ({len(sub_devices)} configured)...")
            ip_map = await spec.discover_all(sub_devices)

            for cfg in sub_devices.values():
                backend = spec.backend()
                backend.ip = ip_map.get(cfg.mac)

                try:
                    state = (
                        await backend.fetch_state(cfg, backend.ip) if backend.ip else None
                    ) or make_offline_state(cfg.id)
                except Exception as e:
                    logger.warning(f"Failed to probe {cfg.name} during init: {e}")
                    state = make_offline_state(cfg.id)

                self._devices[cfg.id] = Device(info=cfg, backend=backend, state=state)
                self._log_status_change(cfg.name, None, state)

        self._queue = CommandQueue(devices=self._devices)
        self._poll_task = asyncio.create_task(self._polling_loop())

        online = sum(1 for d in self._devices.values() if d.state.status == DeviceStatus.ONLINE)
        total = len(self._devices)
        logger.info(f"Initialization complete: {online}/{total} devices online")

    async def shutdown(self) -> None:
        """Cancel polling and close all backend connections."""
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            logger.info("Polling stopped")

        if self._queue:
            await self._queue.shutdown()

        for device in self._devices.values():
            await device.backend.close()

        logger.info("All backend connections closed")

    async def set_device_power(
        self, device_id: str, action: Literal["on", "off"], child_id: str | None = None
    ) -> DeviceState:
        """Submit command to queue, wait for completion, update state cache."""
        if device_id not in self._devices:
            raise ValueError(f"Device {device_id} not found")
        if not self._queue:
            raise RuntimeError("Device manager not initialized")

        cmd = make_command(device_id, action, child_id)
        cmd = self._queue.submit(cmd)
        try:
            state = await self._queue.wait_for_command(cmd)
        except DeviceOfflineError:
            self._update_state(device_id, make_offline_state(device_id, self._devices[device_id].state))
            raise
        self._update_state(device_id, state)
        return state

    async def refresh_device(self, device_id: str) -> DeviceState:
        """Re-discover and probe a single device. Useful for offline recovery."""
        device = self._devices.get(device_id)
        if not device:
            raise ValueError(f"Device {device_id} not found")

        if device.backend.ip:
            logger.info(f"Refreshing {device.info.name} at cached IP {device.backend.ip}")
            state = await device.backend.fetch_state(device.info, device.backend.ip)
            if state:
                self._update_state(device_id, state)
                return state

        logger.info(f"Cached IP unreachable for {device.info.name}, rediscovering...")
        new_ip = await device.backend.find_ip(device.info)
        if new_ip:
            state = await device.backend.fetch_state(device.info, new_ip)
            if state:
                self._update_state(device_id, state)
                return state

        logger.warning(f"Could not reach {device.info.name} during refresh")
        state = make_offline_state(device_id, device.state)
        self._update_state(device_id, state)
        return state

    def get_all_devices(self) -> list[Device]:
        """Get all Device aggregates in config file order."""
        return [
            self._devices[info.id]
            for info in self._config.devices.values()
            if info.id in self._devices
        ]

    def get_device(self, device_id: str) -> Device:
        """Get a single Device aggregate."""
        device = self._devices.get(device_id)
        if device is None:
            raise ValueError(f"Device {device_id} not found")
        return device

    # ── Internal ──────────────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _update_state(self, device_id: str, new_state: DeviceState) -> None:
        """Single write point for the state cache."""
        device = self._devices[device_id]
        previous = device.state
        device.state = new_state
        self._log_status_change(device.info.name, previous, new_state)
        for q in self._subscribers:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass  # subscriber already has a pending notification

    @staticmethod
    def _log_status_change(
        name: str, previous: DeviceState | None, current: DeviceState
    ) -> None:
        if previous is None or previous.status != current.status:
            if current.status == DeviceStatus.ONLINE:
                logger.info(f"{name} is now online")
            else:
                logger.info(f"{name} is now offline")

    async def _polling_loop(self) -> None:
        """Periodically poll each device to keep the state cache fresh."""
        logger.debug(f"Polling started (interval={POLL_INTERVAL}s)")
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            logger.debug("Polling cycle starting")

            for device_id, device in self._devices.items():
                if self._queue and self._queue.has_active_processor(device_id):
                    logger.debug(f"Polling skipping {device.info.name} — processor active")
                    continue
                if not device.backend.ip:
                    logger.debug(f"Polling skipping {device.info.name} — no known IP")
                    continue

                try:
                    state = await device.backend.fetch_state(device.info, device.backend.ip)
                    self._update_state(device_id, state or make_offline_state(device_id, device.state))
                except (DeviceOfflineError, DeviceOperationError, asyncio.TimeoutError, OSError) as e:
                    logger.warning(f"Polling probe failed for {device.info.name}: {e}")
                    self._update_state(device_id, make_offline_state(device_id, device.state))
                except Exception:
                    logger.exception(f"Unexpected error polling {device.info.name}")
                    self._update_state(device_id, make_offline_state(device_id, device.state))

            logger.debug("Polling cycle complete")
