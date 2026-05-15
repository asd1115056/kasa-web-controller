"""MiIO protocol connection layer: UDP discovery, status polling, and power control."""

import asyncio
import logging
import re
import socket
import time
from datetime import datetime, timezone
from functools import partial

from miio.protocol import Message

from ..core.models import ChildState, DeviceOfflineError, DeviceState, DeviceStatus, make_offline_state
from .config import MiioDeviceConfig

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'^[0-9a-fA-F]{32}$')

_MIIO_PORT = 54321
# Standard MiIO UDP hello packet (32 bytes, all-ones placeholder fields)
_HELLO = bytes.fromhex('21310020' + 'ff' * 28)

# WP12 (cuco.plug.wp12) MiOT property map — verified from miot-spec.org
# siid 2 piid 1 = main switch (总控), siid 3–8 piid 1 = outlets 1–6, siid 9 piid 1 = USB
_OUTLET_SIIDS = [3, 4, 5, 6, 7, 8]   # outlets 1–6
_USB_SIID = 9
_MAIN_SIID = 2
_CHILD_IDS = ["1", "2", "3", "4", "5", "6", "usb"]


def _udp_discover_sync(broadcast: str, timeout: float) -> dict[str, str]:
    """Send MiIO hello to broadcast; return {miio_id: ip} for all responders."""
    found: dict[str, str] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        sock.sendto(_HELLO, (broadcast, _MIIO_PORT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
                try:
                    m = Message.parse(data)
                    did = str(int.from_bytes(m.header.value.device_id, byteorder="big"))
                    found[did] = addr[0]
                except Exception:
                    pass  # skip malformed packets
            except socket.timeout:
                break
    except OSError as e:
        logger.warning(f"UDP discover on {broadcast} failed: {e}")
    finally:
        sock.close()
    return found


async def discover_all(
    whitelist: dict[str, MiioDeviceConfig], timeout: float = 3.0
) -> dict[str, str]:
    """Broadcast UDP discover per unique broadcast address; return MAC→IP map."""
    by_broadcast: dict[str, list[MiioDeviceConfig]] = {}
    for cfg in whitelist.values():
        by_broadcast.setdefault(cfg.broadcast, []).append(cfg)

    loop = asyncio.get_running_loop()
    ip_map: dict[str, str] = {}

    for broadcast, cfgs in by_broadcast.items():
        logger.info(f"MiIO discovering on {broadcast}...")
        discovered = await loop.run_in_executor(
            None, partial(_udp_discover_sync, broadcast, timeout)
        )
        for cfg in cfgs:
            if cfg.miio_id in discovered:
                ip = discovered[cfg.miio_id]
                ip_map[cfg.mac] = ip
                logger.info(f"Discovered {cfg.name} at {ip}")

    logger.info(f"MiIO discovery complete: {len(ip_map)}/{len(whitelist)} devices found")
    return ip_map


def _build_get_props(miio_id: str) -> list[dict]:
    """Build get_properties payload for all switch outlets + USB."""
    props = []
    for siid in _OUTLET_SIIDS + [_USB_SIID]:
        props.append({"did": miio_id, "siid": siid, "piid": 1})
    return props


def _fetch_status_sync(ip: str, cfg: MiioDeviceConfig) -> DeviceState:
    """Synchronous status fetch; raises DeviceOfflineError on connection failure."""
    from miio import DeviceException, MiotDevice  # lazy: avoid hard dep at import time

    device = MiotDevice(ip=ip, token=cfg.token)
    try:
        results = device.send("get_properties", _build_get_props(cfg.miio_id))
    except DeviceException as e:
        raise DeviceOfflineError(f"{cfg.name} unreachable: {e}") from e

    # results is a list of dicts: [{did, siid, piid, code, value}, ...]
    values: dict[int, bool] = {}
    for r in results:
        if r.get("code") == 0:
            values[r["siid"]] = bool(r["value"])

    outlet_states = [values.get(siid, False) for siid in _OUTLET_SIIDS]
    usb_state = values.get(_USB_SIID, False)

    children = [
        ChildState(id=child_id, alias=f"Outlet {child_id}", is_on=state)
        for child_id, state in zip(_CHILD_IDS[:6], outlet_states)
    ]
    children.append(ChildState(id="usb", alias="USB", is_on=usb_state))

    return DeviceState(
        id=cfg.id,
        status=DeviceStatus.ONLINE,
        is_on=any(outlet_states) or usb_state,
        alias=cfg.name,
        model="WP12",
        children=tuple(children),
        last_updated=datetime.now(timezone.utc),
    )


async def get_status(ip: str, cfg: MiioDeviceConfig) -> DeviceState:
    """Return current device state. Invalid token → offline state (not raised)."""
    if not TOKEN_RE.match(cfg.token):
        logger.warning(f"{cfg.name}: token is not a valid 32-char hex string; returning offline")
        return make_offline_state(cfg.id)

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, partial(_fetch_status_sync, ip, cfg))
    except DeviceOfflineError:
        return make_offline_state(cfg.id)
    except Exception as e:
        logger.warning(f"{cfg.name} get_status error: {e}")
        return make_offline_state(cfg.id)


def _set_power_sync(
    ip: str, cfg: MiioDeviceConfig, is_on: bool, child_id: str | None
) -> None:
    """Synchronous power set; raises DeviceOfflineError on failure."""
    from miio import DeviceException, MiotDevice  # lazy import

    if child_id is None:
        siid = _MAIN_SIID
    elif child_id in _CHILD_IDS[:6]:
        siid = _OUTLET_SIIDS[_CHILD_IDS.index(child_id)]
    elif child_id == "usb":
        siid = _USB_SIID
    else:
        raise DeviceOfflineError(f"{cfg.name}: unknown child_id '{child_id}'")

    device = MiotDevice(ip=ip, token=cfg.token)
    try:
        device.send(
            "set_properties",
            [{"did": cfg.miio_id, "siid": siid, "piid": 1, "value": is_on}],
        )
    except DeviceException as e:
        raise DeviceOfflineError(f"{cfg.name} set_power failed: {e}") from e


async def set_power(
    ip: str, cfg: MiioDeviceConfig, is_on: bool, child_id: str | None
) -> None:
    """Set outlet power state. Invalid token or failure → DeviceOfflineError."""
    if not TOKEN_RE.match(cfg.token):
        raise DeviceOfflineError(f"{cfg.name}: invalid token format")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_set_power_sync, ip, cfg, is_on, child_id))
