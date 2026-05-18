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
    telethon_session_string: str | None = None  # StringSession instead of file
    telethon_channels: str = ""

    # Bot
    bot_enabled: bool = False
    bot_token: str | None = None
    bot_allowed_users: str = ""

    # LLM parser
    # Provider: "anthropic" | "deepseek" | "auto" (picks whichever key is set).
    llm_provider: Literal["anthropic", "deepseek", "auto"] = "auto"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-4-5-20251001"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Exchange
    exchange: Literal["binance"] = "binance"
    exchange_market: Literal["futures", "spot"] = "futures"

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    # Railway/Heroku convention - overrides web_port if set
    port: int | None = None

    # Storage. Auto-normalized for asyncpg if pointing at Postgres.
    database_url: str = "sqlite+aiosqlite:///./data/signals.db"

    # Behaviour
    entry_tolerance_pct: float = 0.2
    signal_ttl_hours: int = 48
    log_level: str = "INFO"

    @field_validator("telethon_channels", "bot_allowed_users", mode="before")
    @classmethod
    def _str(cls, v: object) -> str:
        return str(v or "")

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Railway / Heroku give "postgres://..." or "postgresql://...".
        # SQLAlchemy 2.x needs explicit driver: "postgresql+asyncpg://...".
        if v.startswith("postgres://"):
            v = "postgresql+asyncpg://" + v[len("postgres://"):]
        elif v.startswith("postgresql://") and "+" not in v.split("://", 1)[0]:
            v = "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    @property
    def effective_port(self) -> int:
        """Railway sets PORT; respect it if present, else fall back to WEB_PORT."""
        return self.port or self.web_port

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
