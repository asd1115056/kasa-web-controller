"""Connect to a MiIO device by IP and token, print status, optionally set power.

Uses python-miio MiotDevice with the WP12 MiOT property map.
Run this to validate the siid/piid mapping and token before adding the device
to devices.json.

Usage:
    python tests/miio/test_device.py <ip> <token> <miio_id> [on|off] [child_id]

Arguments:
    ip        Device IP address
    token     32-char hex token
    miio_id   Device DID (from test_discover.py output)
    on|off    Action to perform (optional)
    child_id  Outlet to control: 1-6 or 'usb' (optional, omit for main switch)

Examples:
    python tests/miio/test_device.py 192.168.1.50 aabbccddeeff00112233445566778899 12345678
    python tests/miio/test_device.py 192.168.1.50 aabbccddeeff00112233445566778899 12345678 on
    python tests/miio/test_device.py 192.168.1.50 aabbccddeeff00112233445566778899 12345678 off 3
    python tests/miio/test_device.py 192.168.1.50 aabbccddeeff00112233445566778899 12345678 on usb
"""

import sys

# WP12 MiOT property map — verified from miot-spec.org (cuco.plug.wp12)
_MAIN_SIID = 2
_OUTLET_SIIDS = [3, 4, 5, 6, 7, 8]   # outlets 1–6
_USB_SIID = 9
_CHILD_IDS = ["1", "2", "3", "4", "5", "6", "usb"]


def _make_get_props(miio_id: str) -> list[dict]:
    """Build get_properties payload for main switch + all outlets + USB."""
    siids = [_MAIN_SIID] + _OUTLET_SIIDS + [_USB_SIID]
    return [{"did": miio_id, "siid": s, "piid": 1} for s in siids]


def get_status(ip: str, token: str, miio_id: str) -> dict:
    """Fetch device state via get_properties; return {siid: bool} map."""
    from miio import DeviceException, MiotDevice

    device = MiotDevice(ip=ip, token=token)
    try:
        results = device.send("get_properties", _make_get_props(miio_id))
    except DeviceException as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    return {r["siid"]: bool(r["value"]) for r in results if r.get("code") == 0}


def set_power(ip: str, token: str, miio_id: str, is_on: bool, child_id: str | None) -> None:
    """Set power via set_properties."""
    from miio import DeviceException, MiotDevice

    if child_id is None:
        siid = _MAIN_SIID
    elif child_id in _CHILD_IDS[:6]:
        siid = _OUTLET_SIIDS[_CHILD_IDS.index(child_id)]
    elif child_id == "usb":
        siid = _USB_SIID
    else:
        print(f"ERROR: unknown child_id '{child_id}'. Valid: 1-6, usb")
        sys.exit(1)

    device = MiotDevice(ip=ip, token=token)
    try:
        device.send(
            "set_properties",
            [{"did": miio_id, "siid": siid, "piid": 1, "value": is_on}],
        )
    except DeviceException as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def print_status(values: dict) -> None:
    def fmt(v: bool) -> str:
        return "ON " if v else "OFF"

    print("\n=== Device Status ===")
    main = values.get(_MAIN_SIID)
    if main is not None:
        print(f"  Main switch:  {fmt(main)}")
    print()
    for i, siid in enumerate(_OUTLET_SIIDS, 1):
        v = values.get(siid)
        state = fmt(v) if v is not None else "N/A"
        print(f"  Outlet {i}:      {state}   (siid={siid})")
    usb = values.get(_USB_SIID)
    usb_state = fmt(usb) if usb is not None else "N/A"
    print(f"  USB:          {usb_state}   (siid={_USB_SIID})")
    print()


def main(ip: str, token: str, miio_id: str,
         action: str | None = None, child_id: str | None = None) -> None:
    print(f"Connecting to {ip} (DID={miio_id})...\n")

    values = get_status(ip, token, miio_id)
    print_status(values)

    if action:
        is_on = action == "on"
        target = f"child_id='{child_id}'" if child_id else "main switch"
        print(f"Setting {target} → {'ON' if is_on else 'OFF'}...")
        set_power(ip, token, miio_id, is_on, child_id)

        print("Reading back state...\n")
        values = get_status(ip, token, miio_id)
        print_status(values)


def print_usage() -> None:
    print(__doc__)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print_usage()
        sys.exit(1)

    _ip = sys.argv[1]
    _token = sys.argv[2]
    _miio_id = sys.argv[3]
    _action = sys.argv[4] if len(sys.argv) > 4 else None
    _child_id = sys.argv[5] if len(sys.argv) > 5 else None

    if _action and _action not in ("on", "off"):
        print(f"ERROR: action must be 'on' or 'off', got '{_action}'")
        sys.exit(1)

    main(_ip, _token, _miio_id, _action, _child_id)
