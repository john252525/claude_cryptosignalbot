from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telethon
    telethon_enabled: bool = False
    telethon_api_id: int | None = None
    telethon_api_hash: str | None = None
    telethon_phone: str | None = None
    telethon_session_name: str = "signals"
    telethon_channels: str = ""

    # Bot
    bot_enabled: bool = False
    bot_token: str | None = None
    bot_allowed_users: str = ""

    # Parser
    anthropic_api_key: str | None = None
    parser_model: str = "claude-haiku-4-5-20251001"

    # Exchange
    exchange: Literal["binance"] = "binance"
    exchange_market: Literal["futures", "spot"] = "futures"

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = 8000

    # Storage
    database_url: str = "sqlite+aiosqlite:///./data/signals.db"

    # Behaviour
    entry_tolerance_pct: float = 0.2
    signal_ttl_hours: int = 48
    log_level: str = "INFO"

    @field_validator("telethon_channels", "bot_allowed_users", mode="before")
    @classmethod
    def _str(cls, v: object) -> str:
        return str(v or "")

    @property
    def telethon_channel_list(self) -> list[str]:
        return [c.strip().lstrip("@") for c in self.telethon_channels.split(",") if c.strip()]

    @property
    def bot_allowed_user_ids(self) -> set[int]:
        out: set[int] = set()
        for x in self.bot_allowed_users.split(","):
            x = x.strip()
            if x.isdigit():
                out.add(int(x))
        return out


settings = Settings()

# Ensure data directory exists for sqlite default.
if settings.database_url.startswith("sqlite"):
    Path("data").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)
