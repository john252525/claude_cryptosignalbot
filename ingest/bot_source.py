from __future__ import annotations

import asyncio
from datetime import timezone

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from ingest.pipeline import IngestPipeline

log = structlog.get_logger(__name__)


class BotSource:
    """Telegram bot that accepts forwarded messages from whitelisted users.

    Workflow:
      1. User adds the bot via @BotFather, puts the token in env, lists their
         user id in BOT_ALLOWED_USERS.
      2. User forwards messages from a signal channel into the bot's DM.
      3. The bot uses message.forward_origin / forward_from_chat to attribute
         the signal to the original channel.

    Non-forwarded messages from whitelisted users are also processed (treated
    as the user pasting a signal manually) — channel attributed to the user.
    """

    def __init__(self, pipeline: IngestPipeline) -> None:
        if not settings.bot_token:
            raise RuntimeError("BOT_TOKEN not set")
        self._pipeline = pipeline
        self._bot = Bot(settings.bot_token)
        self._dp = Dispatcher()
        self._allowed = settings.bot_allowed_user_ids
        self._task: asyncio.Task | None = None
        self._register_handlers()

    def _register_handlers(self) -> None:
        dp = self._dp

        @dp.message(Command("start"))
        async def _start(msg: Message) -> None:
            uid = msg.from_user.id if msg.from_user else 0
            allowed = (not self._allowed) or uid in self._allowed
            if allowed:
                await msg.answer(
                    "Forward signals from your channels here.\n"
                    "I will parse them, track TP/SL on Binance, and surface "
                    "channel-level stats on the web dashboard."
                )
            else:
                await msg.answer(f"Not authorized. Your user id is {uid}.")

        @dp.message(F.text | F.caption)
        async def _on_message(msg: Message) -> None:
            uid = msg.from_user.id if msg.from_user else 0
            if self._allowed and uid not in self._allowed:
                return
            text = msg.text or msg.caption or ""
            if not text:
                return

            # Identify origin channel from forward metadata.
            ext_id, title = _attribution(msg, uid)
            posted_at = msg.forward_date or msg.date
            if posted_at and posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)

            try:
                await self._pipeline.ingest(
                    source="bot",
                    channel_external_id=ext_id,
                    channel_title=title,
                    tg_message_id=msg.forward_from_message_id,
                    text=text,
                    posted_at=posted_at,
                )
                await msg.answer("✓ received")
            except Exception as e:
                log.warning("bot.ingest_error", error=str(e))
                await msg.answer(f"error: {e}")

    async def start(self) -> None:
        log.info("bot.started", allowed_users=sorted(self._allowed) or "(any)")
        self._task = asyncio.create_task(
            self._dp.start_polling(self._bot, handle_signals=False),
            name="bot-polling",
        )

    async def stop(self) -> None:
        try:
            await self._dp.stop_polling()
        except Exception:
            pass
        try:
            await self._bot.session.close()
        except Exception:
            pass
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _attribution(msg: Message, uid: int) -> tuple[str, str]:
    """Return (external_id, title) for the channel of origin."""
    origin = getattr(msg, "forward_origin", None)
    if origin is not None:
        # MessageOriginChannel / MessageOriginChat / MessageOriginUser
        chat = getattr(origin, "chat", None)
        if chat is not None:
            ext = getattr(chat, "username", None) or str(getattr(chat, "id", "unknown"))
            return ext, getattr(chat, "title", "") or ext
        sender = getattr(origin, "sender_user", None)
        if sender is not None:
            ext = getattr(sender, "username", None) or str(sender.id)
            return ext, sender.full_name or ext
        name = getattr(origin, "sender_user_name", None)
        if name:
            return f"hidden:{name}", name
    # Legacy forward_from_chat (older clients).
    chat = getattr(msg, "forward_from_chat", None)
    if chat is not None:
        ext = getattr(chat, "username", None) or str(chat.id)
        return ext, getattr(chat, "title", "") or ext
    # No forward — attribute to the user.
    return f"manual:{uid}", f"manual ({uid})"
