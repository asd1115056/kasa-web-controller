"""MiIO device config dataclass and parser."""

from dataclasses import dataclass

from ..core.models import DeviceInfo


@dataclass
class MiioDeviceConfig(DeviceInfo):
    broadcast: str = ""
    token: str = ""
    miio_id: str = ""


def parse_config(raw: dict, mac: str, name: str) -> MiioDeviceConfig:
    return MiioDeviceConfig(
        mac=mac,
        name=name,
        type="miio",
        group=raw.get("group"),
        broadcast=raw.get("broadcast", ""),
        token=raw.get("token", ""),
        miio_id=raw.get("miio_id", ""),
    )
