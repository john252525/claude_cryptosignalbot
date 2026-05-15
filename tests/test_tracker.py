"""End-to-end tracker test with an in-memory DB and a fake price feed.

Drives a LONG signal from PENDING -> ACTIVE -> CLOSED_TP, and a SHORT signal
from PENDING -> CLOSED_SL, verifying status transitions, events, and PnL.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from db import engine, get_session, init_db
from db.models import Base, Channel, Signal, SignalEvent, SignalSide, SignalStatus
from exchanges.base import PriceFeed, Tick
from tracker.execution_tracker import ExecutionTracker


async def _fresh_db() -> None:
    """Drop & recreate all tables — call at the start of each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()


class FakeFeed(PriceFeed):
    def __init__(self) -> None:
        self.subs: list[str] = []
        self.unsubs: list[str] = []
        self._cb = None

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def subscribe(self, symbol: str) -> None:
        self.subs.append(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self.unsubs.append(symbol)

    def on_tick(self, callback) -> None:
        self._cb = callback

    async def ticks(self):
        if False:
            yield  # pragma: no cover

    async def normalize_symbol(self, raw: str):
        return raw.upper()

    async def get_price(self, symbol: str):
        return None

    async def emit(self, tick: Tick) -> None:
        await self._cb(tick)


async def _make_signal(**kw) -> int:
    async with get_session() as s:
        ch = Channel(source="test", external_id=kw.pop("ext", "t"), title="t")
        s.add(ch)
        await s.flush()
        sig = Signal(
            channel_id=ch.id,
            tg_message_id=None,
            raw_text="…",
            status=SignalStatus.PENDING,
            posted_at=datetime.now(timezone.utc),
            **kw,
        )
        s.add(sig)
        await s.flush()
        return sig.id


async def _load(sid: int) -> Signal:
    async with get_session() as s:
        return (await s.execute(select(Signal).where(Signal.id == sid))).scalar_one()


@pytest.mark.asyncio
async def test_long_signal_closes_on_all_tps():
    await _fresh_db()
    feed = FakeFeed()
    tracker = ExecutionTracker(feed)

    sid = await _make_signal(
        symbol="BTCUSDT",
        side=SignalSide.LONG,
        entry_low=100.0, entry_high=101.0,
        stop_loss=95.0,
        take_profits=[105.0, 110.0],
    )
    await tracker.register_signal(sid, "BTCUSDT")
    assert "BTCUSDT" in feed.subs

    # Tick inside entry zone -> ACTIVE.
    await feed.emit(Tick(symbol="BTCUSDT", price=100.5, high=100.7, low=100.3, ts_ms=1))
    assert (await _load(sid)).status == SignalStatus.ACTIVE

    # First TP hit but not the last.
    await feed.emit(Tick(symbol="BTCUSDT", price=105.5, high=105.5, low=104.0, ts_ms=2))
    sig = await _load(sid)
    assert sig.status == SignalStatus.ACTIVE
    assert sig.last_tp_hit_index == 0

    # Final TP hit -> CLOSED_TP.
    await feed.emit(Tick(symbol="BTCUSDT", price=110.2, high=110.2, low=109.0, ts_ms=3))
    sig = await _load(sid)
    assert sig.status == SignalStatus.CLOSED_TP
    assert sig.last_tp_hit_index == 1
    # entry_avg=100.5, mean of (105-100.5)/100.5 and (110-100.5)/100.5 -> ~6.97%
    assert sig.pnl_pct is not None and 6.0 < sig.pnl_pct < 8.0
    assert "BTCUSDT" in feed.unsubs


@pytest.mark.asyncio
async def test_short_signal_stops_out():
    await _fresh_db()
    feed = FakeFeed()
    tracker = ExecutionTracker(feed)

    sid = await _make_signal(
        ext="t2",
        symbol="ETHUSDT",
        side=SignalSide.SHORT,
        entry_low=2000.0, entry_high=2010.0,
        stop_loss=2050.0,
        take_profits=[1950.0, 1900.0],
    )
    await tracker.register_signal(sid, "ETHUSDT")

    await feed.emit(Tick(symbol="ETHUSDT", price=2005.0, high=2010.0, low=2000.0, ts_ms=1))
    # Wick up through SL.
    await feed.emit(Tick(symbol="ETHUSDT", price=2040.0, high=2055.0, low=2020.0, ts_ms=2))

    sig = await _load(sid)
    assert sig.status == SignalStatus.CLOSED_SL
    # entry_avg=2005, sl=2050 -> short pnl=(2005-2050)/2005 = ~-2.24%
    assert sig.pnl_pct is not None and -3.0 < sig.pnl_pct < -2.0

    async with get_session() as s:
        evs = list((await s.execute(select(SignalEvent).where(SignalEvent.signal_id == sid))).scalars().all())
    kinds = [e.kind for e in evs]
    assert "ENTRY" in kinds and "SL" in kinds
