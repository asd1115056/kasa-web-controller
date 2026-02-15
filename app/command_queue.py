"""
Per-device command queue with short-term persistent connections.

Connection strategy:
- Processor connects on first command (not at startup)
- Consecutive commands reuse the same connection
- Disconnects after IDLE_DISCONNECT_SECONDS of inactivity
- Next command restarts the processor

Error handling (retry + discover):
- Operation fails -> reconnect to cached IP -> retry
- Still fails -> discover new IP -> connect -> retry
- Still fails -> mark command FAILED, notify offline
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from kasa import Device

from .config import ConfigManager
from .connection import (
    COMMAND_INTERVAL,
    build_device_state,
    connect_device,
    discover_device_ip,
)
from .models import (
    Command,
    CommandStatus,
    DeviceOfflineError,
    DeviceState,
)

logger = logging.getLogger(__name__)

IDLE_DISCONNECT_SECONDS = 30


class CommandQueue:
    """Per-device command queue with short-term persistent connections."""

    def __init__(
        self,
        config: ConfigManager,
        ip_cache: dict[str, str],
        on_state_update: Callable[[str, DeviceState], None],
    ):
        self._config = config
        self._ip_cache = ip_cache  # MAC -> IP, shared with DeviceManager
        self._on_state_update = on_state_update

        # Per-device queues and processor tasks
        self._queues: dict[str, asyncio.Queue[Command]] = {}
        self._processors: dict[str, asyncio.Task] = {}
        self._last_command_time: dict[str, float] = {}

    def submit(self, command: Command) -> Command:
        """Submit a command. Returns the Command (may be deduplicated).

        Dedup: if same device + same child_id + same action is still QUEUED,
        return the existing Command (callers share the same event).
        """
        device_id = command.device_id

        # Ensure queue exists
        if device_id not in self._queues:
            self._queues[device_id] = asyncio.Queue()

        queue = self._queues[device_id]

        # Check for dedup: scan queue items
        for existing in queue._queue:
            if (
                existing.status == CommandStatus.QUEUED
                and existing.device_id == device_id
                and existing.child_id == command.child_id
                and existing.action == command.action
            ):
                logger.debug(
                    f"Dedup: reusing command {existing.id} for {device_id} "
                    f"action={command.action} child={command.child_id}"
                )
                return existing

        queue.put_nowait(command)

        # Start processor if not running
        if device_id not in self._processors or self._processors[device_id].done():
            self._processors[device_id] = asyncio.create_task(
                self._process_queue(device_id)
            )

        return command

    async def wait_for_command(
        self, command: Command, timeout: float = 30.0
    ) -> Command:
        """Wait for a command to complete."""
        try:
            await asyncio.wait_for(command._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            command.status = CommandStatus.FAILED
            command.error = "Command timed out"
            command._event.set()
        return command

    def has_active_processor(self, device_id: str) -> bool:
        """Check if a device has an active (running) processor."""
        task = self._processors.get(device_id)
        return task is not None and not task.done()

    async def shutdown(self):
        """Cancel all processor tasks and disconnect."""
        for task in self._processors.values():
            task.cancel()

        for task in self._processors.values():
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._processors.clear()
        self._queues.clear()

    # === Internal ===

    async def _process_queue(self, device_id: str):
        """Command processing loop for a single device."""
        queue = self._queues[device_id]
        device: Device | None = None

        mac = self._config.resolve_id(device_id)
        if not mac:
            logger.error(f"Processor: unknown device_id {device_id}")
            return

        device_info = self._config.whitelist[mac]

        try:
            while True:
                # Wait for next command with idle timeout
                try:
                    cmd = await asyncio.wait_for(
                        queue.get(), timeout=IDLE_DISCONNECT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # Idle timeout - disconnect and exit processor
                    if device:
                        logger.info(
                            f"Idle timeout for {device_info.name}, disconnecting"
                        )
                        await device.disconnect()
                    return

                cmd.status = CommandStatus.PROCESSING

                # Rate limiting
                await self._wait_for_rate_limit(device_id)

                try:
                    state, device = await self._execute_with_retry(
                        device_id, cmd, device
                    )
                    cmd.status = CommandStatus.COMPLETED
                    cmd.completed_at = datetime.now()
                    cmd.result = state
                    self._on_state_update(device_id, state)
                except DeviceOfflineError as e:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = str(e)
                    # Get previous state for topology preservation
                    offline_state = build_device_state(device_info, None)
                    self._on_state_update(device_id, offline_state)
                    device = None
                except Exception as e:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = str(e)
                    logger.error(
                        f"Unexpected error processing command for {device_info.name}: {e}"
                    )
                finally:
                    cmd._event.set()

        except asyncio.CancelledError:
            # Shutdown - disconnect if connected
            if device:
                try:
                    await device.disconnect()
                except Exception:
                    pass
            raise

    async def _execute_with_retry(
        self,
        device_id: str,
        command: Command,
        device: Device | None,
    ) -> tuple[DeviceState, Device | None]:
        """Execute a command with retry + discover fallback.

        1. Try with existing connection
        2. Fail -> disconnect -> reconnect to cached IP -> retry
        3. Fail -> discover new IP -> connect -> retry
        4. All fail -> raise DeviceOfflineError

        Returns (new DeviceState, surviving Device or None).
        """
        mac = self._config.resolve_id(device_id)
        device_info = self._config.whitelist[mac]

        # Step 1: Try with existing connection
        if device:
            try:
                result = await self._execute_command(device, command)
                await device.update()
                state = build_device_state(device_info, device)
                # Update IP cache
                self._ip_cache[mac] = device.host
                return state, device
            except Exception as e:
                logger.warning(
                    f"Command failed on existing connection for {device_info.name}: {e}"
                )
                try:
                    await device.disconnect()
                except Exception:
                    pass
                device = None

        # Step 2: Reconnect to cached IP
        cached_ip = self._ip_cache.get(mac)
        if cached_ip:
            logger.info(f"Retrying {device_info.name} at cached IP {cached_ip}...")
            device, error = await connect_device(cached_ip, device_info.credentials)
            if device:
                # Verify MAC
                device_mac = getattr(device, "mac", None)
                if device_mac and normalize_mac_safe(device_mac) != mac:
                    logger.warning(f"IP {cached_ip} no longer belongs to {mac}")
                    await device.disconnect()
                    device = None
                else:
                    try:
                        result = await self._execute_command(device, command)
                        await device.update()
                        state = build_device_state(device_info, device)
                        self._ip_cache[mac] = device.host
                        return state, device
                    except Exception as e:
                        logger.warning(
                            f"Retry at cached IP failed for {device_info.name}: {e}"
                        )
                        try:
                            await device.disconnect()
                        except Exception:
                            pass
                        device = None

        # Step 3: Discover new IP
        logger.info(f"Discovering new IP for {device_info.name}...")
        new_ip = await discover_device_ip(device_info)
        if new_ip:
            device, error = await connect_device(new_ip, device_info.credentials)
            if device:
                try:
                    result = await self._execute_command(device, command)
                    await device.update()
                    state = build_device_state(device_info, device)
                    self._ip_cache[mac] = new_ip
                    return state, device
                except Exception as e:
                    logger.warning(
                        f"Command at discovered IP failed for {device_info.name}: {e}"
                    )
                    try:
                        await device.disconnect()
                    except Exception:
                        pass

        # All attempts failed
        raise DeviceOfflineError(
            f"Device {device_info.name} is offline (all retry attempts failed)"
        )

    async def _execute_command(self, device: Device, command: Command) -> None:
        """Execute a single on/off command on a device."""
        target = device
        if command.child_id:
            child_found = False
            for i, child in enumerate(getattr(device, "children", [])):
                child_identifier = child.id if hasattr(child, "id") else str(i)
                if child_identifier == command.child_id:
                    target = child
                    child_found = True
                    break

            if not child_found:
                raise ValueError(f"Child outlet {command.child_id} not found")

        if command.action == "on":
            await target.turn_on()
        else:
            await target.turn_off()

    async def _wait_for_rate_limit(self, device_id: str) -> None:
        """Wait if needed to respect per-device command interval."""
        now = time.monotonic()
        last_time = self._last_command_time.get(device_id, 0)
        elapsed = now - last_time

        if elapsed < COMMAND_INTERVAL:
            wait_time = COMMAND_INTERVAL - elapsed
            logger.debug(f"Rate limiting {device_id}: waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)

        self._last_command_time[device_id] = time.monotonic()


def normalize_mac_safe(mac: str) -> str | None:
    """Normalize MAC, returning None on invalid input."""
    try:
        from .models import normalize_mac
        return normalize_mac(mac)
    except ValueError:
        return None


def make_command(
    device_id: str, action: str, child_id: str | None = None
) -> Command:
    """Create a new Command with a unique ID."""
    return Command(
        id=uuid.uuid4().hex[:8],
        device_id=device_id,
        action=action,
        child_id=child_id,
    )
