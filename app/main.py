"""SmartPlug Hub - FastAPI backend with per-device command queue and multi-protocol support."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .device_manager import DeviceManager
from .core.exceptions import DeviceOfflineError, DeviceOperationError
from .core.models import DeviceStatus
from .schemas import ControlRequest, DeviceListResponse, DeviceResponse, ErrorDetail

PROJECT_ROOT = Path(__file__).parent.parent

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

device_manager: DeviceManager | None = None


def _err(error: str, message: str) -> dict:
    return ErrorDetail(error=error, message=message).model_dump()


# === Dependency ===
def get_device_manager() -> DeviceManager:
    if not device_manager:
        raise HTTPException(
            status_code=503,
            detail=_err("service_unavailable", "Device manager not initialized"),
        )
    return device_manager


# === Lifecycle ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    global device_manager

    logger.info("Starting SmartPlug Hub...")
    device_manager = DeviceManager()
    await device_manager.initialize()

    yield

    logger.info("Shutting down SmartPlug Hub...")
    if device_manager:
        await device_manager.shutdown()


# === App ===
app = FastAPI(
    title="SmartPlug Hub",
    description="Multi-protocol web controller for smart plugs and power strips",
    lifespan=lifespan,
)


# === API v1 Endpoints ===
@app.get("/api/v1/devices", response_model=DeviceListResponse)
def list_devices(dm: DeviceManager = Depends(get_device_manager)):
    """Get cached status of all devices (zero I/O)."""
    return DeviceListResponse(devices=[DeviceResponse.from_device(d) for d in dm.get_all_devices()])


@app.get("/api/v1/devices/{device_id}", response_model=DeviceResponse)
def get_device(device_id: str, dm: DeviceManager = Depends(get_device_manager)):
    """Get a single device's cached status."""
    try:
        return DeviceResponse.from_device(dm.get_device(device_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=_err("not_found", str(e)))


@app.patch("/api/v1/devices/{device_id}", response_model=DeviceResponse)
async def control_device(
    device_id: str,
    request: ControlRequest,
    dm: DeviceManager = Depends(get_device_manager),
):
    """Control a device (on/off). Blocks until operation completes."""
    action = "on" if request.is_on else "off"
    try:
        await dm.set_device_power(device_id=device_id, action=action, child_id=request.child_id)
        return DeviceResponse.from_device(dm.get_device(device_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=_err("invalid_request", str(e)))
    except DeviceOfflineError as e:
        raise HTTPException(status_code=503, detail=_err("offline", str(e)))
    except DeviceOperationError as e:
        if "timed out" in str(e).lower():
            raise HTTPException(status_code=504, detail=_err("timeout", str(e)))
        raise HTTPException(status_code=502, detail=_err("operation_failed", str(e)))


@app.post("/api/v1/devices/{device_id}/refresh")
async def refresh_device(
    device_id: str, dm: DeviceManager = Depends(get_device_manager)
):
    """Refresh a single device (discover + connect). For offline recovery."""
    try:
        await dm.refresh_device(device_id)
        device = dm.get_device(device_id)
        code = 200 if device.state.status == DeviceStatus.ONLINE else 503
        return JSONResponse(
            content=DeviceResponse.from_device(device).model_dump(mode='json'),
            status_code=code,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=_err("not_found", str(e)))


_SSE_HEARTBEAT = 30.0  # keepalive interval when no state changes occur


@app.get("/api/v1/events")
async def device_events(dm: DeviceManager = Depends(get_device_manager)):
    """SSE stream: push on state change, heartbeat comment when idle."""
    q = dm.subscribe()

    def _snapshot() -> str:
        return DeviceListResponse(
            devices=[DeviceResponse.from_device(d) for d in dm.get_all_devices()]
        ).model_dump_json()

    async def generator():
        try:
            yield f"data: {_snapshot()}\n\n"
            while True:
                try:
                    await asyncio.wait_for(q.get(), timeout=_SSE_HEARTBEAT)
                    yield f"data: {_snapshot()}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            dm.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# === Static Files & Root ===
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")


@app.get("/")
async def root():
    return FileResponse(PROJECT_ROOT / "static/index.html")
