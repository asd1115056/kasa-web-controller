"""
Kasa Web Controller - FastAPI Backend

Provides REST API for controlling TP-Link Kasa smart devices.
Uses ID-based device identification with smart IP caching.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .device_manager import DeviceManager

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
class ToggleRequest(BaseModel):
    action: str
    child_id: Optional[str] = None


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
async def list_devices():
    """Get all whitelisted devices with their current status."""
    if not device_manager:
        raise HTTPException(status_code=503, detail="Device manager not initialized")

    try:
        devices = await device_manager.get_all_devices()
        return {"devices": devices}
    except Exception as e:
        logger.error(f"Failed to list devices: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list devices: {str(e)}")


@app.post("/api/device/{device_id}/toggle")
async def toggle_device(device_id: str, request: ToggleRequest):
    """Toggle a device or child outlet on/off or perform a power cycle."""
    if not device_manager:
        raise HTTPException(status_code=503, detail="Device manager not initialized")

    try:
        result = await device_manager.control_device(
            device_id=device_id,
            action=request.action,
            child_id=request.child_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to control device {device_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Control failed: {str(e)}")


@app.post("/api/discover")
async def force_discover():
    """Force a full device discovery to update IP cache."""
    if not device_manager:
        raise HTTPException(status_code=503, detail="Device manager not initialized")

    try:
        await device_manager.discover_all(force=True)
        return {"success": True, "message": "Discovery completed"}
    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        raise HTTPException(status_code=500, detail=f"Discovery failed: {str(e)}")


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
