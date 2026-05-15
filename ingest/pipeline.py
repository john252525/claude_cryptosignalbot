from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from db import get_session
from db.models import Channel, Signal, SignalEvent, SignalSide, SignalStatus
from exchanges.base import PriceFeed
from parser import LLMSignalParser, ParsedSignal
from tracker import ExecutionTracker

log = structlog.get_logger(__name__)


class IngestPipeline:
    """Common processing path: raw TG message -> parse -> persist -> register with tracker.

    Both Telethon and bot ingesters call `ingest()` so they share dedup,
    parsing, validation and persistence logic.
    """

    def __init__(self, parser: LLMSignalParser, tracker: ExecutionTracker, feed: PriceFeed) -> None:
        self._parser = parser
        self._tracker = tracker
        self._feed = feed

    async def ingest(
        self,
        *,
        source: str,                # "telethon" | "bot"
        channel_external_id: str,   # @username or numeric id
        channel_title: str,
        tg_message_id: int | None,
        text: str,
        posted_at: datetime | None = None,
    ) -> Signal | None:
        text = (text or "").strip()
        if not text:
            return None
        posted_at = posted_at or datetime.now(timezone.utc)

        # 1. Resolve or create channel record.
        async with get_session() as s:
            res = await s.execute(
                select(Channel).where(
                    Channel.source == source,
                    Channel.external_id == channel_external_id,
                )
            )
            channel = res.scalar_one_or_none()
            if channel is None:
                channel = Channel(
                    source=source,
                    external_id=channel_external_id,
                    title=channel_title or channel_external_id,
                )
                s.add(channel)
                await s.flush()
            elif channel_title and channel.title != channel_title:
                channel.title = channel_title

            if not channel.enabled:
                return None

            # 2. Dedup by (channel_id, tg_message_id).
            if tg_message_id is not None:
                res = await s.execute(
                    select(Signal).where(
                        Signal.channel_id == channel.id,
                        Signal.tg_message_id == tg_message_id,
                    )
                )
                if res.scalar_one_or_none():
                    return None
            channel_id = channel.id

        # 3. Parse via LLM (outside DB transaction).
        try:
            parsed = await self._parser.parse(text)
        except Exception as e:
            log.warning("ingest.parse_error", error=str(e), text=text[:200])
            return None

        if not parsed.is_signal:
            log.debug("ingest.not_a_signal", reason=parsed.reason, text=text[:120])
            return None

        # 4. Validate / normalize symbol against exchange.
        if not parsed.symbol:
            return None
        norm = await self._feed.normalize_symbol(parsed.symbol)
        if not norm:
            log.info("ingest.unknown_symbol", symbol=parsed.symbol)
            return await self._save_invalid(
                channel_id, tg_message_id, text, parsed, reason="unknown_symbol"
            )

        if not parsed.is_complete():
            return await self._save_invalid(
                channel_id, tg_message_id, text, parsed, reason="incomplete"
            )

        # 5. Persist as PENDING.
        signal_id = await self._persist(
            channel_id=channel_id,
            tg_message_id=tg_message_id,
            text=text,
            parsed=parsed,
            symbol=norm,
            posted_at=posted_at,
        )

        # 6. Tell tracker to start watching.
        await self._tracker.register_signal(signal_id, norm)
        return None  # caller doesn't need the ORM object detached

    async def _persist(
        self,
        *,
        channel_id: int,
        tg_message_id: int | None,
        text: str,
        parsed: ParsedSignal,
        symbol: str,
        posted_at: datetime,
    ) -> int:
        side = SignalSide.LONG if parsed.side == "LONG" else SignalSide.SHORT
        async with get_session() as s:
            sig = Signal(
                channel_id=channel_id,
                tg_message_id=tg_message_id,
                raw_text=text,
                symbol=symbol,
                side=side,
                market=parsed.market,
                leverage=parsed.leverage,
                entry_low=float(parsed.entry_low),  # type: ignore[arg-type]
                entry_high=float(parsed.entry_high),  # type: ignore[arg-type]
                stop_loss=float(parsed.stop_loss),  # type: ignore[arg-type]
                take_profits=list(parsed.take_profits),
                status=SignalStatus.PENDING,
                posted_at=posted_at,
                parse_meta={"reason": parsed.reason},
            )
            s.add(sig)
            await s.flush()
            sid = sig.id
        log.info(
            "ingest.persisted",
            signal_id=sid,
            channel_id=channel_id,
            symbol=symbol,
            side=parsed.side,
            entry=(parsed.entry_low, parsed.entry_high),
            sl=parsed.stop_loss,
            tps=parsed.take_profits,
        )
        return sid

    async def _save_invalid(
        self,
        channel_id: int,
        tg_message_id: int | None,
        text: str,
        parsed: ParsedSignal,
        *,
        reason: str,
    ) -> None:
        # Record INVALID with whatever we have, so dashboards can show parser misses.
        async with get_session() as s:
            sig = Signal(
                channel_id=channel_id,
                tg_message_id=tg_message_id,
                raw_text=text,
                symbol=(parsed.symbol or "UNKNOWN")[:32],
                side=SignalSide.LONG if parsed.side != "SHORT" else SignalSide.SHORT,
                market=parsed.market,
                leverage=parsed.leverage,
                entry_low=parsed.entry_low or 0.0,
                entry_high=parsed.entry_high or 0.0,
                stop_loss=parsed.stop_loss or 0.0,
                take_profits=list(parsed.take_profits),
                status=SignalStatus.INVALID,
                parse_meta={"reason": reason, "parser_note": parsed.reason},
            )
            s.add(sig)
            await s.flush()
            s.add(SignalEvent(signal_id=sig.id, kind="INVALID", detail=reason))
        return None
