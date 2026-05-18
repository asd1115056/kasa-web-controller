"""Protocol-agnostic per-device command queue."""

import asyncio
import collections
import logging
import time
import uuid
from datetime import datetime

from .core.backend import Command, CommandStatus
from .core.exceptions import DeviceOfflineError
from .core.models import Device, DeviceState

logger = logging.getLogger(__name__)


class CommandQueue:
    """Per-device command queue. Delegates execution to DeviceBackend."""

    def __init__(self, devices: dict[str, Device]) -> None:
        self._devices = devices

        self._queues: dict[str, asyncio.Queue[Command]] = {}
        self._pending: dict[str, collections.deque[Command]] = {}
        self._processors: dict[str, asyncio.Task] = {}
        self._last_command_time: dict[str, float] = {}

    def submit(self, command: Command) -> Command:
        """Submit a command, returning the canonical Command (may be deduplicated).

        If an identical command (same device, action, child_id) is already QUEUED,
        the existing one is returned so callers share the same completion event.
        """
        device_id = command.device_id

        if device_id not in self._queues:
            self._queues[device_id] = asyncio.Queue()
            self._pending[device_id] = collections.deque()

        for existing in self._pending[device_id]:
            if (
                existing.status == CommandStatus.QUEUED
                and existing.device_id == device_id
                and existing.child_id == command.child_id
                and existing.action == command.action
            ):
                logger.debug(
                    f"Dedup: reusing command {existing.id} for device {device_id} "
                    f"action={command.action} child={command.child_id}"
                )
                return existing

        self._queues[device_id].put_nowait(command)
        self._pending[device_id].append(command)
        logger.debug(f"Queued command {command.id} for device {device_id} action={command.action}")

        if device_id not in self._processors or self._processors[device_id].done():
            self._processors[device_id] = asyncio.create_task(
                self._process_queue(device_id)
            )
            logger.debug(f"Started processor for device {device_id}")

        return command

    async def wait_for_command(self, command: Command) -> DeviceState:
        """Wait for a command to complete, returning its DeviceState or raising on failure."""
        if command._future is None:
            raise RuntimeError(f"Command {command.id} has no future attached")
        return await command._future

    def has_active_processor(self, device_id: str) -> bool:
        """Return True if a processor task is currently running for this device."""
        task = self._processors.get(device_id)
        return task is not None and not task.done()

    async def shutdown(self) -> None:
        """Cancel all running processor tasks and wait for them to finish."""
        tasks = [t for t in self._processors.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._processors.clear()

    async def _process_queue(self, device_id: str) -> None:
        """Command processing loop for a single device."""
        if device_id not in self._queues:
            return

        queue = self._queues[device_id]
        device = self._devices.get(device_id)
        if not device:
            logger.error(f"Processor: unknown device_id {device_id}")
            self._processors.pop(device_id, None)
            return

        cfg = device.info
        backend = device.backend

        logger.debug(f"Processor running for device {device_id} (session_timeout={backend.policy.session_timeout}s)")

        try:
            while True:
                if backend.policy.session_timeout:
                    # Stateful: hold processor open so the backend can reuse its connection.
                    try:
                        cmd = await asyncio.wait_for(
                            queue.get(), timeout=backend.policy.session_timeout
                        )
                    except asyncio.TimeoutError:
                        logger.debug(f"Processor idle timeout for device {device_id}, exiting")
                        break
                else:
                    # Stateless: drain queue then exit immediately.
                    try:
                        cmd = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                pending = self._pending.get(device_id)
                if pending:
                    try:
                        pending.remove(cmd)
                    except ValueError:
                        pass

                cmd.status = CommandStatus.PROCESSING
                logger.debug(f"Processing command {cmd.id} for device {device_id} action={cmd.action}")
                await self._wait_for_rate_limit(device_id, backend.policy.command_interval)

                if cmd._future is None:
                    raise RuntimeError(f"Command {cmd.id} has no future attached")
                try:
                    state = await backend.execute_command(cmd, cfg)
                    cmd.status = CommandStatus.COMPLETED
                    cmd.completed_at = datetime.now()
                    if not cmd._future.done():
                        cmd._future.set_result(state)
                    logger.debug(f"Command {cmd.id} completed for device {device_id}")
                except DeviceOfflineError as e:
                    cmd.status = CommandStatus.FAILED
                    if not cmd._future.done():
                        cmd._future.set_exception(e)
                    logger.info(f"Device {device_id} is offline: {e}")
                except Exception as e:
                    cmd.status = CommandStatus.FAILED
                    if not cmd._future.done():
                        cmd._future.set_exception(e)
                    logger.error(f"Unexpected error processing command {cmd.id} for device {device_id}: {e}")

        finally:
            self._processors.pop(device_id, None)
            logger.debug(f"Processor exited for device {device_id}")
            # Restart if commands arrived while we were winding down.
            if device_id in self._queues and not self._queues[device_id].empty():
                logger.debug(f"Commands pending — restarting processor for device {device_id}")
                self._processors[device_id] = asyncio.create_task(
                    self._process_queue(device_id)
                )

    async def _wait_for_rate_limit(self, device_id: str, interval: float) -> None:
        if not interval:
            return
        now = time.monotonic()
        elapsed = now - self._last_command_time.get(device_id, 0)
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._last_command_time[device_id] = time.monotonic()


def make_command(
    device_id: str, action: str, child_id: str | None = None
) -> Command:
    """Create a new Command with a unique ID."""
    cmd = Command(
        id=uuid.uuid4().hex[:8],
        device_id=device_id,
        action=action,
        child_id=child_id,
    )
    cmd._future = asyncio.get_running_loop().create_future()
    return cmd
