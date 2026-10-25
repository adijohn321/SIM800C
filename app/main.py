"""SIM800C SMS Manager — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import database as db
from app import sim800c
from app.api import router as api_router
from app.config import get_settings
from app.port_detect import is_auto
from app.services import import_modem_inbox
from app.sim800c import Sim800CError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    await db.init_db()

    sim800c.modem = sim800c.Sim800CModem(
        port=settings.sim800c_port,
        baudrate=settings.sim800c_baudrate,
        timeout=settings.sim800c_timeout,
        mock=settings.sim800c_mock,
    )
    try:
        await asyncio.to_thread(sim800c.modem.connect)
        logger.info(
            "Modem ready (mock=%s, port=%s)",
            settings.sim800c_mock,
            sim800c.modem.port,
        )
    except sim800c.Sim800CError as exc:
        logger.warning("Modem not connected at startup: %s", exc)

    poll_task: asyncio.Task | None = None
    if settings.sim800c_poll_seconds > 0:
        poll_task = asyncio.create_task(_poll_inbox(settings.sim800c_poll_seconds))

    yield

    if poll_task:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
    if sim800c.modem:
        sim800c.modem.disconnect()


async def _poll_inbox(interval: float) -> None:
    await asyncio.sleep(1.5)
    while True:
        try:
            modem = sim800c.modem
            if (
                modem is not None
                and not modem.status.connected
                and get_settings().sim800c_auto_reconnect
            ):
                try:
                    await asyncio.to_thread(modem.connect)
                    logger.info("Modem reconnected (port=%s)", modem.port)
                except Sim800CError as exc:
                    logger.debug("Reconnect attempt failed: %s", exc)
            imported, total = await import_modem_inbox()
            if imported:
                logger.info("Auto-sync imported %s of %s messages", imported, total)
        except Sim800CError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Inbox auto-sync failed")
        await asyncio.sleep(interval)


app = FastAPI(
    title="SIM800C SMS Manager",
    description="Web UI and REST API for sending/receiving SMS via a SIM800C GSM module.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/documentation", response_class=HTMLResponse, include_in_schema=False)
async def documentation(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        "documentation.html",
        {
            "request": request,
            "mock_mode": settings.sim800c_mock,
            "api_key_required": bool(settings.api_key),
            "active_page": "documentation",
            "configured_port": settings.sim800c_port,
            "auto_port": is_auto(settings.sim800c_port),
            "poll_seconds": settings.sim800c_poll_seconds,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mock_mode": settings.sim800c_mock,
            "api_key_required": bool(settings.api_key),
            "active_page": "messages",
        },
    )


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
