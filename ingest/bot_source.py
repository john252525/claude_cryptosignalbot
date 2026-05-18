from __future__ import annotations

import asyncio
import json
from datetime import timezone

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dataclasses import dataclass

from config import settings
from ingest.pipeline import IngestPipeline

log = structlog.get_logger(__name__)


@dataclass
class _Origin:
    external_id: str
    title: str
    tg_message_id: int | None


class BotSource:
    """Telegram bot that accepts forwarded messages from whitelisted users.

    Workflow:
      1. User adds the bot via @BotFather, puts the token in env, lists their
         user id in BOT_ALLOWED_USERS.
      2. User forwards messages from a signal channel into the bot's DM.
      3. The bot attributes the signal to the original channel using
         message.forward_origin (aiogram 3.x), falling back to legacy fields.

    Non-forwarded messages from whitelisted users are also processed (treated
    as the user pasting a signal manually) — channel attributed to the user.

    Commands:
      /start  — usage info
      /test   — reply to a forwarded signal with raw parser output (debug)
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
            if self._allowed and uid not in self._allowed:
                await msg.answer(f"Not authorized. Your user id is {uid}.")
                return
            await msg.answer(
                "Forward signals from your channels here.\n"
                "I parse them, track TP/SL on Binance, and surface channel-level "
                "stats on the web dashboard.\n\n"
                "Commands:\n"
                "/test  — show raw parser output for a forwarded message"
            )

        @dp.message(Command("test"))
        async def _test(msg: Message) -> None:
            uid = msg.from_user.id if msg.from_user else 0
            if self._allowed and uid not in self._allowed:
                return
            text = (msg.text or msg.caption or "").removeprefix("/test").strip()
            if not text:
                await msg.answer(
                    "Send `/test <text of signal>` to see raw parser JSON.\n"
                    "Example:\n"
                    "/test BTCUSDT LONG entry 60000 tp 61000 62000 sl 59000"
                )
                return
            try:
                parsed = await self._pipeline._parser.parse(text)
                payload = {
                    "is_signal": parsed.is_signal,
                    "symbol": parsed.symbol,
                    "side": parsed.side,
                    "market": parsed.market,
                    "leverage": parsed.leverage,
                    "entry_low": parsed.entry_low,
                    "entry_high": parsed.entry_high,
                    "take_profits": parsed.take_profits,
                    "stop_loss": parsed.stop_loss,
                    "reason": parsed.reason,
                }
                await msg.answer(
                    "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await msg.answer(f"parser error: {e}")

        @dp.message(F.text | F.caption)
        async def _on_message(msg: Message) -> None:
            uid = msg.from_user.id if msg.from_user else 0
            if self._allowed and uid not in self._allowed:
                return
            text = msg.text or msg.caption or ""
            if not text:
                return

            origin = _attribution(msg, uid)
            posted_at = msg.forward_date or msg.date
            if posted_at and posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)

            try:
                result = await self._pipeline.ingest(
                    source="bot",
                    channel_external_id=origin.external_id,
                    channel_title=origin.title,
                    tg_message_id=origin.tg_message_id,
                    text=text,
                    posted_at=posted_at,
                )
                await msg.answer(
                    f"{result.human()}\nchannel: {origin.title}",
                )
            except Exception as e:
                log.exception("bot.ingest_error", error=str(e))
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


def _attribution(msg: Message, uid: int) -> _Origin:
    """Return origin (channel, title, original tg_message_id) for the message."""
    origin = getattr(msg, "forward_origin", None)
    if origin is not None:
        # MessageOriginChannel — has chat + message_id
        chat = getattr(origin, "chat", None)
        if chat is not None:
            ext = getattr(chat, "username", None) or str(getattr(chat, "id", "unknown"))
            title = getattr(chat, "title", "") or ext
            return _Origin(ext, title, getattr(origin, "message_id", None))
        # MessageOriginUser — sender_user
        sender = getattr(origin, "sender_user", None)
        if sender is not None:
            ext = getattr(sender, "username", None) or str(sender.id)
            title = sender.full_name or ext
            return _Origin(ext, title, None)
        # MessageOriginHiddenUser
        name = getattr(origin, "sender_user_name", None)
        if name:
            return _Origin(f"hidden:{name}", name, None)

    # Legacy forward_from_chat (older clients).
    chat = getattr(msg, "forward_from_chat", None)
    if chat is not None:
        ext = getattr(chat, "username", None) or str(chat.id)
        title = getattr(chat, "title", "") or ext
        return _Origin(ext, title, getattr(msg, "forward_from_message_id", None))

    # No forward — attribute to the user. Use msg.message_id as dedup key.
    return _Origin(f"manual:{uid}", f"manual ({uid})", msg.message_id)
