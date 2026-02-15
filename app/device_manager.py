"""
Device Manager - thin facade combining ConfigManager + CommandQueue.

Manages device state cache and coordinates control operations.
"""

import asyncio
import logging
from pathlib import Path

from .command_queue import CommandQueue, make_command
from .config import ConfigManager
from .connection import (
    build_device_state,
    connect_device,
    discover_all,
    discover_device_ip,
)
from .models import (
    CommandStatus,
    DeviceOfflineError,
    DeviceOperationError,
    DeviceState,
)

logger = logging.getLogger(__name__)

HEALTH_CHECK_INTERVAL = 60


class DeviceManager:
    """Combines ConfigManager + CommandQueue. Manages state cache."""

    def __init__(self, config_dir: Path | None = None):
        self._config = ConfigManager(config_dir)
        self._ip_cache: dict[str, str] = {}  # MAC -> IP
        self._states: dict[str, DeviceState] = {}  # device_id -> DeviceState
        self._queue: CommandQueue | None = None
        self._health_task: asyncio.Task | None = None

    async def initialize(self):
        """Load config -> discover all -> connect+update+disconnect -> build initial cache."""
        self._config.load()

        # Initialize CommandQueue
        self._queue = CommandQueue(
            config=self._config,
            ip_cache=self._ip_cache,
            on_state_update=self._on_state_update,
        )

        # Discover all devices
        ip_mapping = await discover_all(self._config.whitelist)
        self._ip_cache.update(ip_mapping)

        # Connect to each discovered device to get initial state, then disconnect
        for mac, ip in ip_mapping.items():
            device_info = self._config.whitelist[mac]
            device, error = await connect_device(ip, device_info.credentials)
            if device:
                state = build_device_state(device_info, device)
                self._states[device_info.id] = state
                await device.disconnect()
                logger.info(
                    f"Initialized {device_info.name} ({device.model}) at {ip}"
                )
            else:
                logger.warning(
                    f"Found {device_info.name} at {ip} but connection failed: {error}"
                )

        # Create offline states for undiscovered devices
        for mac, info in self._config.whitelist.items():
            if info.id not in self._states:
                self._states[info.id] = build_device_state(info, None)

        online = sum(1 for s in self._states.values() if s.status == "online")
        total = len(self._states)
        logger.info(f"Initialization complete: {online}/{total} devices online")

        # Start health check
        self._health_task = asyncio.create_task(self._health_check_loop())

    async def shutdown(self):
        """Stop health check and command queue."""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        if self._queue:
            await self._queue.shutdown()

        logger.info("Device manager shut down")

    # === Control (for API) ===

    async def control_device(
        self, device_id: str, action: str, child_id: str | None = None
    ) -> DeviceState:
        """Submit command to queue and wait for completion.

        Returns DeviceState on success.
        Raises DeviceOfflineError, DeviceOperationError, or ValueError.
        """
        mac = self._config.resolve_id(device_id)
        if not mac:
            raise ValueError(f"Device {device_id} not found")

        if action not in ("on", "off"):
            raise ValueError(f"Invalid action: {action}. Use 'on' or 'off'")

        # Validate child_id if provided
        if child_id is not None:
            current = self._states.get(device_id)
            if current and current.children:
                child_ids = {c.id for c in current.children}
                if child_id not in child_ids:
                    raise ValueError(f"Child outlet {child_id} not found")

        cmd = make_command(device_id, action, child_id)
        cmd = self._queue.submit(cmd)
        cmd = await self._queue.wait_for_command(cmd)

        if cmd.status == CommandStatus.COMPLETED:
            return cmd.result

        # Command failed
        error_msg = cmd.error or "Unknown error"
        if "offline" in error_msg.lower():
            raise DeviceOfflineError(error_msg)
        if "timed out" in error_msg.lower():
            raise DeviceOperationError(error_msg)
        raise DeviceOperationError(error_msg)

    # === State queries (zero I/O, from cache) ===

    def get_all_states(self) -> list[DeviceState]:
        """Get all device states from cache."""
        return list(self._states.values())

    def get_device_state(self, device_id: str) -> DeviceState:
        """Get a single device state from cache."""
        state = self._states.get(device_id)
        if not state:
            raise ValueError(f"Device {device_id} not found")
        return state

    # === Management ===

    async def refresh_device(self, device_id: str) -> DeviceState:
        """Bypass queue: discover -> connect -> update -> disconnect -> update cache.

        Used for offline device recovery.
        """
        mac = self._config.resolve_id(device_id)
        if not mac:
            raise ValueError(f"Device {device_id} not found")

        device_info = self._config.whitelist[mac]
        previous = self._states.get(device_id)

        # Try cached IP first
        cached_ip = self._ip_cache.get(mac)
        if cached_ip:
            device, _ = await connect_device(cached_ip, device_info.credentials)
            if device:
                state = build_device_state(device_info, device)
                self._states[device_id] = state
                self._ip_cache[mac] = device.host
                await device.disconnect()
                return state

        # Discover new IP
        new_ip = await discover_device_ip(device_info)
        if new_ip:
            device, _ = await connect_device(new_ip, device_info.credentials)
            if device:
                state = build_device_state(device_info, device)
                self._states[device_id] = state
                self._ip_cache[mac] = new_ip
                await device.disconnect()
                return state

        # Still offline
        offline_state = build_device_state(device_info, None, previous)
        self._states[device_id] = offline_state
        return offline_state

    # === Internal ===

    def _on_state_update(self, device_id: str, state: DeviceState):
        """Callback from CommandQueue when a command completes or fails."""
        # Preserve topology on offline transition
        if state.status == "offline":
            previous = self._states.get(device_id)
            if previous and state.alias is None:
                state = build_device_state(
                    self._config.whitelist[self._config.resolve_id(device_id)],
                    None,
                    previous,
                )
        self._states[device_id] = state

    async def _health_check_loop(self):
        """Periodically check devices without active processors."""
        while True:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            try:
                await self._run_health_check()
            except Exception as e:
                logger.warning(f"Health check failed: {e}")

    async def _run_health_check(self):
        """Connect -> update -> disconnect for idle devices."""
        checked = 0
        online = 0

        for mac, info in self._config.whitelist.items():
            # Skip devices with active command processors
            if self._queue and self._queue.has_active_processor(info.id):
                continue

            previous = self._states.get(info.id)
            cached_ip = self._ip_cache.get(mac)
            if not cached_ip:
                continue

            device, _ = await connect_device(cached_ip, info.credentials)
            if device:
                state = build_device_state(info, device)
                self._states[info.id] = state
                self._ip_cache[mac] = device.host
                await device.disconnect()
                online += 1
            else:
                # Mark offline, preserve topology
                offline_state = build_device_state(info, None, previous)
                self._states[info.id] = offline_state

            checked += 1

        logger.debug(f"Health check: {online}/{checked} devices online")
