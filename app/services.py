"""Shared SMS operations used by the API and background poller."""

from __future__ import annotations

import asyncio
import logging

from app import database as db
from app.sim800c import Sim800CError, get_modem

logger = logging.getLogger(__name__)


async def import_modem_inbox() -> tuple[int, int]:
    """Copy new SIM messages into the local database.

    Returns (imported_count, total_on_modem).
    """
    modem = get_modem()
    if not modem.status.connected:
        raise Sim800CError("Modem is not connected")

    raw_messages = await asyncio.to_thread(modem.list_sms, "ALL")
    imported = 0
    for raw in raw_messages:
        existing = await db.find_by_modem_index(raw.index)
        if existing:
            continue
        status = "unread" if "UNREAD" in raw.status.upper() else "read"
        await db.create_message(
            direction="inbound",
            phone=raw.sender,
            body=raw.body,
            status=status,
            modem_index=raw.index,
        )
        imported += 1
        logger.info("Imported inbound SMS from %s (index %s)", raw.sender, raw.index)
    return imported, len(raw_messages)
