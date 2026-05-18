"""Pydantic models for API request/response contracts."""

from datetime import datetime

from pydantic import BaseModel

from .core.models import Device


class ChildResponse(BaseModel):
    id: str
    alias: str | None
    is_on: bool


class DeviceResponse(BaseModel):
    id: str
    name: str
    type: str
    group: str | None
    status: str
    is_on: bool | None
    alias: str | None
    model: str | None
    is_strip: bool
    children: list[ChildResponse] | None
    last_updated: datetime | None

    @classmethod
    def from_device(cls, device: Device) -> 'DeviceResponse':
        info = device.info
        state = device.state
        return cls(
            id=state.id,
            name=info.name,
            type=info.type,
            group=info.group,
            status=state.status.value,
            is_on=state.is_on,
            alias=state.alias,
            model=state.model,
            is_strip=state.is_strip,
            children=[ChildResponse(id=c.id, alias=c.alias, is_on=c.is_on) for c in state.children]
            if state.children else None,
            last_updated=state.last_updated,
        )


class DeviceListResponse(BaseModel):
    devices: list[DeviceResponse]


class ControlRequest(BaseModel):
    is_on: bool
    child_id: str | None = None


class ErrorDetail(BaseModel):
    error: str
    message: str
