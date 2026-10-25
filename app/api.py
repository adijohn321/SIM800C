"""REST API routes for SMS management and modem control."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import database as db
from app.config import get_settings
from app.schemas import (
    DetectPortOut,
    InjectSmsRequest,
    MessageListOut,
    MessageOut,
    ModemStatusOut,
    OkOut,
    PortInfo,
    SendSmsRequest,
    SyncResultOut,
)
from app.services import import_modem_inbox
from app.port_detect import PortDetectionError, detect_port, is_auto, scan_ports
from app.sim800c import Sim800CError, get_modem

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["api"])


async def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def _modem_status_out() -> ModemStatusOut:
    modem = get_modem()
    s = await asyncio.to_thread(modem.refresh_status)
    return ModemStatusOut(
        connected=s.connected,
        mock=s.mock,
        port=s.port,
        signal_rssi=s.signal_rssi,
        signal_ber=s.signal_ber,
        network_registered=s.network_registered,
        operator=s.operator,
        imei=s.imei,
        sim_ready=s.sim_ready,
        last_error=s.last_error,
    )


@router.get("/health", response_model=OkOut)
async def health() -> OkOut:
    return OkOut(ok=True, detail="SIM800C SMS Manager is running")


@router.get("/modem/status", response_model=ModemStatusOut, dependencies=[Depends(verify_api_key)])
async def modem_status() -> ModemStatusOut:
    return await _modem_status_out()


@router.post("/modem/connect", response_model=ModemStatusOut, dependencies=[Depends(verify_api_key)])
async def modem_connect() -> ModemStatusOut:
    modem = get_modem()
    try:
        await asyncio.to_thread(modem.connect)
    except Sim800CError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await _modem_status_out()


@router.post("/modem/disconnect", response_model=OkOut, dependencies=[Depends(verify_api_key)])
async def modem_disconnect() -> OkOut:
    get_modem().disconnect()
    return OkOut(ok=True, detail="Disconnected")


@router.get("/modem/ports", response_model=list[PortInfo], dependencies=[Depends(verify_api_key)])
async def list_ports() -> list[PortInfo]:
    """List available serial ports, scored by how likely they host the SIM800C."""
    try:
        candidates = scan_ports()
    except PortDetectionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return [
        PortInfo(
            device=c.device,
            description=c.description,
            hwid=c.hwid,
            score=c.score,
            match=c.is_match,
            reason=c.reason,
        )
        for c in candidates
    ]


@router.post("/modem/detect", response_model=DetectPortOut, dependencies=[Depends(verify_api_key)])
async def detect_modem_port() -> DetectPortOut:
    """Detect which serial port the SIM800C is plugged into.

    Uses the USB signature when it is unambiguous, otherwise probes the
    remaining ports with an ``AT`` command. The detected port becomes the
    modem's port when it is currently disconnected.
    """
    settings = get_settings()
    modem = get_modem()

    exclude: list[str] = []
    if not modem.mock and modem.status.connected and not is_auto(modem.port):
        exclude.append(modem.port)  # never probe the port we are talking to

    try:
        candidate, method = await asyncio.to_thread(
            detect_port,
            baudrate=settings.sim800c_baudrate,
            exclude=exclude,
        )
    except PortDetectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not modem.status.connected:
        modem.port = candidate.device  # apply so the next Connect uses it

    return DetectPortOut(
        device=candidate.device,
        description=candidate.description,
        hwid=candidate.hwid,
        method=method,
        score=candidate.score,
        reason=candidate.reason,
    )


@router.post("/sms/send", response_model=MessageOut, dependencies=[Depends(verify_api_key)])
async def send_sms(payload: SendSmsRequest) -> MessageOut:
    modem = get_modem()
    record = await db.create_message(
        direction="outbound",
        phone=payload.phone,
        body=payload.message,
        status="sending",
    )
    try:
        await asyncio.to_thread(modem.send_sms, payload.phone, payload.message)
        updated = await db.update_message(record["id"], status="sent", error=None)
    except Sim800CError as exc:
        updated = await db.update_message(record["id"], status="failed", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    assert updated is not None
    return MessageOut(**updated)


@router.get("/sms", response_model=MessageListOut, dependencies=[Depends(verify_api_key)])
async def list_sms(
    direction: Optional[str] = Query(default=None, pattern="^(inbound|outbound)$"),
    phone: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> MessageListOut:
    items = await db.list_messages(
        direction=direction,
        phone=phone,
        status=status,
        limit=limit,
        offset=offset,
    )
    total = await db.count_messages(direction=direction, status=status)
    return MessageListOut(total=total, items=[MessageOut(**m) for m in items])


@router.get("/sms/{msg_id}", response_model=MessageOut, dependencies=[Depends(verify_api_key)])
async def get_sms(msg_id: int) -> MessageOut:
    msg = await db.get_message(msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    return MessageOut(**msg)


@router.delete("/sms/{msg_id}", response_model=OkOut, dependencies=[Depends(verify_api_key)])
async def delete_sms(msg_id: int) -> OkOut:
    msg = await db.get_message(msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    modem = get_modem()
    if msg.get("modem_index") is not None and modem.status.connected:
        try:
            await asyncio.to_thread(modem.delete_sms, int(msg["modem_index"]))
        except Sim800CError as exc:
            logger.warning("Modem delete failed for index %s: %s", msg["modem_index"], exc)

    await db.delete_message(msg_id)
    return OkOut(ok=True, detail="Deleted")


@router.post("/sms/sync", response_model=SyncResultOut, dependencies=[Depends(verify_api_key)])
async def sync_inbox() -> SyncResultOut:
    """Pull messages from the modem SIM storage into the local database."""
    try:
        imported, total = await import_modem_inbox()
    except Sim800CError as exc:
        status = 503 if "not connected" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return SyncResultOut(imported=imported, total_on_modem=total)


@router.post("/sms/inject", response_model=MessageOut, dependencies=[Depends(verify_api_key)])
async def inject_mock_sms(payload: InjectSmsRequest) -> MessageOut:
    """Simulate an inbound SMS (mock mode only). Useful for UI testing."""
    modem = get_modem()
    try:
        raw = modem.inject_mock_sms(payload.phone, payload.message)
    except Sim800CError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = await db.create_message(
        direction="inbound",
        phone=raw.sender,
        body=raw.body,
        status="unread",
        modem_index=raw.index,
    )
    return MessageOut(**record)


@router.get("/stats", dependencies=[Depends(verify_api_key)])
async def stats() -> dict:
    return {
        "total": await db.count_messages(),
        "inbound": await db.count_messages(direction="inbound"),
        "outbound": await db.count_messages(direction="outbound"),
        "failed": await db.count_messages(status="failed"),
        "unread": await db.count_messages(status="unread"),
    }
