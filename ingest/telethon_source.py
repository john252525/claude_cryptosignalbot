from __future__ import annotations

import asyncio
from datetime import timezone

import structlog
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import settings
from ingest.pipeline import IngestPipeline

log = structlog.get_logger(__name__)


class TelethonSource:
    """Reads new messages from configured public/joined channels via a user account.

    Session can be provided in two ways:
      1. TELETHON_SESSION_STRING env var (StringSession) — preferred, no file needed
      2. File-based session at ./data/<session_name>.session — fallback if env var not set

    On first run (if using file-based), Telethon will interactively ask for the login code;
    subsequent runs reuse the session. To get SESSION_STRING: run scripts/get_telethon_session.py
    """

    def __init__(self, pipeline: IngestPipeline) -> None:
        if not settings.telethon_api_id or not settings.telethon_api_hash:
            raise RuntimeError("TELETHON_API_ID / TELETHON_API_HASH not set")
        self._pipeline = pipeline

        # Prefer StringSession from env, fall back to file-based
        session = None
        if settings.telethon_session_string:
            session = StringSession(settings.telethon_session_string)
            log.info("telethon.session_source", source="StringSession (env)")
        else:
            session = f"data/{settings.telethon_session_name}"
            log.info("telethon.session_source", source=f"file ({session})")

        self._client = TelegramClient(
            session,
            int(settings.telethon_api_id),
            settings.telethon_api_hash,
        )
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # For StringSession, don't pass phone (session is already authed)
        # For file-based, pass phone for interactive login
        if settings.telethon_session_string:
            await self._client.start()  # Already authenticated
        else:
            await self._client.start(phone=settings.telethon_phone)  # type: ignore[arg-type]
        channels = settings.telethon_channel_list
        if not channels:
            log.warning("telethon.no_channels_configured")
        # Resolve entities so the username strings work in the handler filter.
        entities = []
        for c in channels:
            try:
                e = await self._client.get_entity(c)
                entities.append(e)
                log.info("telethon.joined", channel=c)
            except Exception as e:
                log.warning("telethon.entity_failed", channel=c, error=str(e))

        @self._client.on(events.NewMessage(chats=entities) if entities else events.NewMessage())
        async def _handler(event):  # noqa: ANN001
            text = event.message.message or ""
            chat = await event.get_chat()
            username = getattr(chat, "username", None) or str(chat.id)
            title = getattr(chat, "title", "") or username
            posted_at = event.message.date
            if posted_at and posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            try:
                result = await self._pipeline.ingest(
                    source="telethon",
                    channel_external_id=str(username),
                    channel_title=title,
                    tg_message_id=event.message.id,
                    text=text,
                    posted_at=posted_at,
                )
                log.info(
                    "telethon.ingested",
                    channel=username,
                    status=result.status.value,
                    signal_id=result.signal_id,
                    detail=result.detail,
                )
            except Exception as e:
                log.warning("telethon.ingest_error", error=str(e))

        log.info("telethon.started", channels=channels)
        self._task = asyncio.create_task(self._client.run_until_disconnected(), name="telethon")

    async def stop(self) -> None:
        try:
            await self._client.disconnect()
        except Exception:
            pass
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
