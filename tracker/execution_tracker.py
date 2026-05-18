from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from config import settings
from db import get_session
from db.models import Signal, SignalEvent, SignalSide, SignalStatus
from exchanges.base import PriceFeed, Tick

log = structlog.get_logger(__name__)


class ExecutionTracker:
    """Owns the lifecycle of each Signal from PENDING -> ACTIVE -> CLOSED_*.

    For each non-terminal signal we ensure the price feed is subscribed to its
    symbol. On every Tick we evaluate:
      * PENDING:  did price touch the entry zone? -> ACTIVE
      * ACTIVE:   did a candle high/low cross any unhit TP? did it cross SL?

    Hit detection uses the candle high/low carried on the Tick — this handles
    fast wicks that aren't captured by the last aggTrade alone.
    """

    def __init__(self, feed: PriceFeed) -> None:
        self._feed = feed
        # symbol -> count of non-terminal signals; subscribe/unsubscribe on transitions to/from 0.
        self._refcount: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._expire_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        feed.on_tick(self._on_tick)

    async def start(self) -> None:
        # Resubscribe to all symbols of non-terminal signals.
        async with get_session() as s:
            res = await s.execute(
                select(Signal.symbol).where(
                    Signal.status.in_([SignalStatus.PENDING, SignalStatus.ACTIVE])
                )
            )
            symbols = {row[0] for row in res.all()}
        for sym in symbols:
            await self._add_ref(sym)
        self._expire_task = asyncio.create_task(self._expire_loop(), name="signal-expirer")
        log.info("tracker.started", symbols=sorted(symbols))

    async def stop(self) -> None:
        self._stop.set()
        if self._expire_task:
            self._expire_task.cancel()
            try:
                await self._expire_task
            except (asyncio.CancelledError, Exception):
                pass

    async def register_signal(self, signal_id: int, symbol: str) -> None:
        """Called when a new signal is persisted. Subscribes price feed if needed."""
        await self._add_ref(symbol)
        log.info("tracker.registered", signal_id=signal_id, symbol=symbol)

    async def _add_ref(self, symbol: str) -> None:
        async with self._lock:
            n = self._refcount.get(symbol, 0)
            self._refcount[symbol] = n + 1
            if n == 0:
                await self._feed.subscribe(symbol)

    async def _release_ref(self, symbol: str) -> None:
        async with self._lock:
            n = self._refcount.get(symbol, 0)
            if n <= 1:
                self._refcount.pop(symbol, None)
                await self._feed.unsubscribe(symbol)
            else:
                self._refcount[symbol] = n - 1

    # ---- tick handler ----

    async def _on_tick(self, tick: Tick) -> None:
        async with get_session() as s:
            res = await s.execute(
                select(Signal).where(
                    Signal.symbol == tick.symbol,
                    Signal.status.in_([SignalStatus.PENDING, SignalStatus.ACTIVE]),
                )
            )
            signals = list(res.scalars().all())
            released: list[str] = []
            for sig in signals:
                changed_to_terminal = await self._evaluate(s, sig, tick)
                if changed_to_terminal:
                    released.append(sig.symbol)
            # commit happens via context manager
        for sym in released:
            await self._release_ref(sym)

    async def _evaluate(self, session, sig: Signal, tick: Tick) -> bool:
        """Update sig in-place based on tick. Returns True if reached terminal status."""
        # PENDING -> ACTIVE
        if sig.status == SignalStatus.PENDING:
            lo = sig.entry_low
            hi = sig.entry_high
            if lo == hi:
                # Single-price entry: use a small tolerance band.
                tol = lo * settings.entry_tolerance_pct / 100.0
                lo, hi = lo - tol, hi + tol
            if tick.low <= hi and tick.high >= lo:
                sig.status = SignalStatus.ACTIVE
                sig.entered_at = datetime.now(timezone.utc)
                session.add(
                    SignalEvent(
                        signal_id=sig.id,
                        kind="ENTRY",
                        detail=f"zone {sig.entry_low}-{sig.entry_high}",
                        price=tick.price,
                    )
                )

        # ACTIVE -> CLOSED_*
        if sig.status == SignalStatus.ACTIVE:
            tps = sig.take_profits or []
            if sig.side == SignalSide.LONG:
                # SL first: check if low broke below SL (only if SL is set).
                if sig.stop_loss is not None and tick.low <= sig.stop_loss:
                    await _close_sl(session, sig, tick)
                    return True
                # TPs in order.
                for idx in range(sig.last_tp_hit_index + 1, len(tps)):
                    if tick.high >= tps[idx]:
                        sig.last_tp_hit_index = idx
                        session.add(
                            SignalEvent(
                                signal_id=sig.id,
                                kind="TP",
                                detail=f"TP{idx + 1}",
                                price=tps[idx],
                            )
                        )
                        if idx == len(tps) - 1:
                            await _close_all_tp(session, sig, tick)
                            return True
                    else:
                        break
            else:  # SHORT
                if sig.stop_loss is not None and tick.high >= sig.stop_loss:
                    await _close_sl(session, sig, tick)
                    return True
                for idx in range(sig.last_tp_hit_index + 1, len(tps)):
                    if tick.low <= tps[idx]:
                        sig.last_tp_hit_index = idx
                        session.add(
                            SignalEvent(
                                signal_id=sig.id,
                                kind="TP",
                                detail=f"TP{idx + 1}",
                                price=tps[idx],
                            )
                        )
                        if idx == len(tps) - 1:
                            await _close_all_tp(session, sig, tick)
                            return True
                    else:
                        break
        return False

    # ---- TTL expiration ----

    async def _expire_loop(self) -> None:
        while not self._stop.is_set():
            try:
                released: list[str] = []
                cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.signal_ttl_hours)
                async with get_session() as s:
                    res = await s.execute(
                        select(Signal).where(
                            Signal.status == SignalStatus.PENDING,
                            Signal.posted_at < cutoff,
                        )
                    )
                    for sig in res.scalars().all():
                        sig.status = SignalStatus.EXPIRED
                        sig.closed_at = datetime.now(timezone.utc)
                        s.add(SignalEvent(signal_id=sig.id, kind="EXPIRED"))
                        released.append(sig.symbol)
                for sym in released:
                    await self._release_ref(sym)
            except Exception as e:
                log.warning("tracker.expire_error", error=str(e))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass


async def _close_sl(session, sig: Signal, tick: Tick) -> None:
    sig.status = SignalStatus.CLOSED_SL
    sig.closed_at = datetime.now(timezone.utc)
    sig.close_price = sig.stop_loss
    sig.pnl_pct = _pnl_at_exit(sig, exit_price=sig.stop_loss)
    session.add(SignalEvent(signal_id=sig.id, kind="SL", detail="stop loss hit", price=sig.stop_loss))


async def _close_all_tp(session, sig: Signal, tick: Tick) -> None:
    sig.status = SignalStatus.CLOSED_TP
    sig.closed_at = datetime.now(timezone.utc)
    last_tp = sig.take_profits[-1]
    sig.close_price = last_tp
    sig.pnl_pct = _pnl_avg_tps(sig)


def _pnl_at_exit(sig: Signal, *, exit_price: float) -> float:
    entry = sig.entry_avg
    if sig.side == SignalSide.LONG:
        return (exit_price - entry) / entry * 100.0
    return (entry - exit_price) / entry * 100.0


def _pnl_avg_tps(sig: Signal) -> float:
    """Average %-PnL across all TPs (equal weight). Approximation when channel
    doesn't specify per-TP sizing."""
    entry = sig.entry_avg
    tps = sig.take_profits or []
    if not tps:
        return 0.0
    pnls: list[float] = []
    for tp in tps:
        if sig.side == SignalSide.LONG:
            pnls.append((tp - entry) / entry * 100.0)
        else:
            pnls.append((entry - tp) / entry * 100.0)
    return sum(pnls) / len(pnls)
