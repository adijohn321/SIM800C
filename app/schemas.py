"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SendSmsRequest(BaseModel):
    phone: str = Field(..., min_length=3, max_length=32, examples=["+15551234567"])
    message: str = Field(..., min_length=1, max_length=1600)


class InjectSmsRequest(BaseModel):
    phone: str = Field(..., min_length=3, max_length=32)
    message: str = Field(..., min_length=1, max_length=1600)


class MessageOut(BaseModel):
    id: int
    direction: Literal["inbound", "outbound"]
    phone: str
    body: str
    status: str
    modem_index: Optional[int] = None
    created_at: str
    updated_at: str
    error: Optional[str] = None


class MessageListOut(BaseModel):
    total: int
    items: list[MessageOut]


class ModemStatusOut(BaseModel):
    connected: bool
    mock: bool
    port: str
    signal_rssi: Optional[int] = None
    signal_ber: Optional[int] = None
    network_registered: bool = False
    operator: str = ""
    imei: str = ""
    sim_ready: bool = False
    last_error: str = ""


class SyncResultOut(BaseModel):
    imported: int
    total_on_modem: int


class OkOut(BaseModel):
    ok: bool = True
    detail: str = ""


class PortInfo(BaseModel):
    device: str
    description: str
    hwid: str = ""
    score: int = 0
    match: bool = False
    reason: str = ""


class DetectPortOut(BaseModel):
    device: str
    description: str = ""
    hwid: str = ""
    method: Literal["signature", "probe"]
    score: int = 0
    reason: str = ""
