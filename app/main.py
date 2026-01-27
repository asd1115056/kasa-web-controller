"""
Kasa Web Controller - FastAPI Backend

Provides REST API for controlling TP-Link Kasa smart devices.
Uses ID-based device identification with smart IP caching.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .device_manager import DeviceManager, DeviceOfflineError, DeviceOperationError

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global device manager instance
device_manager: DeviceManager | None = None


# === Pydantic Models ===
class ControlRequest(BaseModel):
    action: str  # "on" or "off"
    child_id: str | None = None  # For power strips


def get_device_manager() -> DeviceManager:
    """Dependency to get the device manager instance."""
    if not device_manager:
        raise HTTPException(status_code=503, detail="Device manager not initialized")
    return device_manager


# === Application Lifecycle ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle - initialize and shutdown DeviceManager."""
    global device_manager

    logger.info("Starting Kasa Web Controller...")
    device_manager = DeviceManager()
    await device_manager.initialize()

    yield

    logger.info("Shutting down Kasa Web Controller...")
    if device_manager:
        await device_manager.shutdown()


# === FastAPI App ===
app = FastAPI(
    title="Kasa Web Controller",
    description="Control TP-Link Kasa smart devices via ID-based identification",
    lifespan=lifespan,
)


# === API Endpoints ===
@app.get("/api/devices")
def list_devices(dm: DeviceManager = Depends(get_device_manager)):
    """Get cached status of all devices (lightweight, for polling)."""
    return {"devices": dm.get_cached_status()}


@app.get("/api/devices/sync")
async def sync_devices(dm: DeviceManager = Depends(get_device_manager)):
    """Sync all devices - connects to each device to get live status."""
    try:
        devices = await dm.get_all_devices()
        return {"devices": devices}
    except Exception as e:
        logger.error(f"Failed to sync devices: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to sync devices: {str(e)}")


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str, dm: DeviceManager = Depends(get_device_manager)):
    """Get a single device's status."""
    try:
        return await dm.get_device_status(device_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get device {device_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get device: {str(e)}")


@app.patch("/api/devices/{device_id}")
async def control_device(
    device_id: str,
    request: ControlRequest,
    dm: DeviceManager = Depends(get_device_manager),
):
    """Control a device (on/off)."""
    try:
        result = await dm.control_device(
            device_id=device_id,
            action=request.action,
            child_id=request.child_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DeviceOfflineError as e:
        raise HTTPException(
            status_code=503,
            detail={"error": "offline", "message": str(e)},
        )
    except DeviceOperationError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": "operation_failed", "message": str(e)},
        )
    except Exception as e:
        logger.error(f"Failed to control device {device_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Control failed: {str(e)}")


@app.post("/api/devices/{device_id}/refresh")
async def refresh_device(device_id: str, dm: DeviceManager = Depends(get_device_manager)):
    """Refresh a single device (targeted discover)."""
    try:
        return await dm.refresh_device(device_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to refresh device {device_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")


# === Static Files & Root ===
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")


@app.get("/")
async def root():
    """Serve the main HTML page."""
    return FileResponse(PROJECT_ROOT / "static/index.html")


# === Entry Point ===
def run():
    """Entry point for the application."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
