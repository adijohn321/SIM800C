"""SQLite persistence for SMS messages and sync metadata."""

from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "messages.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
                phone TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                modem_index INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_created
            ON messages(created_at DESC)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_phone
            ON messages(phone)
            """
        )
        await db.commit()


async def create_message(
    *,
    direction: str,
    phone: str,
    body: str,
    status: str = "pending",
    modem_index: Optional[int] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    now = _utc_now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO messages
                (direction, phone, body, status, modem_index, created_at, updated_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (direction, phone, body, status, modem_index, now, now, error),
        )
        await db.commit()
        msg_id = cursor.lastrowid
        return await get_message(msg_id)  # type: ignore[arg-type]


async def update_message(msg_id: int, **fields: Any) -> Optional[dict[str, Any]]:
    if not fields:
        return await get_message(msg_id)

    fields["updated_at"] = _utc_now()
    columns = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [msg_id]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE messages SET {columns} WHERE id=?", values)
        await db.commit()
    return await get_message(msg_id)


async def get_message(msg_id: int) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM messages WHERE id=?", (msg_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_messages(
    *,
    direction: Optional[str] = None,
    phone: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if direction:
        clauses.append("direction=?")
        params.append(direction)
    if phone:
        clauses.append("phone LIKE ?")
        params.append(f"%{phone}%")
    if status:
        clauses.append("status=?")
        params.append(status)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT * FROM messages
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def count_messages(
    *,
    direction: Optional[str] = None,
    status: Optional[str] = None,
) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if direction:
        clauses.append("direction=?")
        params.append(direction)
    if status:
        clauses.append("status=?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(f"SELECT COUNT(*) FROM messages {where}", params)
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def delete_message(msg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
        await db.commit()
        return cursor.rowcount > 0


async def find_by_modem_index(modem_index: int) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM messages WHERE modem_index=? AND direction='inbound'",
            (modem_index,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
