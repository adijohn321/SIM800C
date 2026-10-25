"""Application configuration loaded from environment / .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Serial port of the SIM800C. "auto" detects the port the modem is plugged
    # into (USB signature, then AT probe) on every connect; set e.g. "COM3" or
    # "/dev/ttyUSB0" to force a specific port.
    sim800c_port: str = "auto"
    # Reconnect automatically from the inbox poller when the modem drops.
    sim800c_auto_reconnect: bool = True
    sim800c_baudrate: int = 115200
    sim800c_timeout: float = 5.0
    sim800c_mock: bool = True

    host: str = "0.0.0.0"
    port: int = 8000

    api_key: str = ""
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'messages.db'}"
    sim800c_poll_seconds: float = 15.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
