from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Channel, Signal, SignalStatus


@dataclass
class ChannelStats:
    channel_id: int
    title: str
    source: str
    enabled: bool
    total: int
    pending: int
    active: int
    closed_tp: int
    closed_sl: int
    expired: int
    invalid: int
    winrate: float | None        # closed_tp / (closed_tp + closed_sl), null if no closed.
    avg_pnl_pct: float | None    # mean pnl_pct over closed signals.
    total_pnl_pct: float | None  # sum pnl_pct over closed signals.


async def channel_stats(session: AsyncSession) -> list[ChannelStats]:
    res = await session.execute(select(Channel))
    out: list[ChannelStats] = []
    for ch in res.scalars().all():
        out.append(await _stats_for_channel(session, ch))
    out.sort(key=lambda x: (x.total_pnl_pct is None, -(x.total_pnl_pct or 0)))
    return out


async def _stats_for_channel(session: AsyncSession, ch: Channel) -> ChannelStats:
    counts_q = select(Signal.status, func.count()).where(
        Signal.channel_id == ch.id
    ).group_by(Signal.status)
    counts_res = await session.execute(counts_q)
    counts = {s: c for s, c in counts_res.all()}

    def n(st: SignalStatus) -> int:
        return int(counts.get(st, 0))

    closed_tp = n(SignalStatus.CLOSED_TP)
    closed_sl = n(SignalStatus.CLOSED_SL)
    decided = closed_tp + closed_sl
    winrate = (closed_tp / decided) if decided else None

    pnl_q = select(func.avg(Signal.pnl_pct), func.sum(Signal.pnl_pct)).where(
        Signal.channel_id == ch.id,
        Signal.status.in_([SignalStatus.CLOSED_TP, SignalStatus.CLOSED_SL]),
    )
    avg_pnl, total_pnl = (await session.execute(pnl_q)).one()
    total = sum(counts.values())

    return ChannelStats(
        channel_id=ch.id,
        title=ch.title or ch.external_id,
        source=ch.source,
        enabled=ch.enabled,
        total=total,
        pending=n(SignalStatus.PENDING),
        active=n(SignalStatus.ACTIVE),
        closed_tp=closed_tp,
        closed_sl=closed_sl,
        expired=n(SignalStatus.EXPIRED),
        invalid=n(SignalStatus.INVALID),
        winrate=winrate,
        avg_pnl_pct=float(avg_pnl) if avg_pnl is not None else None,
        total_pnl_pct=float(total_pnl) if total_pnl is not None else None,
    )


async def overall_stats(session: AsyncSession) -> dict[str, float | int | None]:
    counts_q = select(Signal.status, func.count()).group_by(Signal.status)
    counts = {s: c for s, c in (await session.execute(counts_q)).all()}

    def n(st: SignalStatus) -> int:
        return int(counts.get(st, 0))

    closed_tp = n(SignalStatus.CLOSED_TP)
    closed_sl = n(SignalStatus.CLOSED_SL)
    decided = closed_tp + closed_sl
    pnl_q = select(func.avg(Signal.pnl_pct)).where(
        Signal.status.in_([SignalStatus.CLOSED_TP, SignalStatus.CLOSED_SL])
    )
    avg_pnl = (await session.execute(pnl_q)).scalar()

    return {
        "total": sum(counts.values()),
        "pending": n(SignalStatus.PENDING),
        "active": n(SignalStatus.ACTIVE),
        "closed_tp": closed_tp,
        "closed_sl": closed_sl,
        "expired": n(SignalStatus.EXPIRED),
        "invalid": n(SignalStatus.INVALID),
        "winrate": (closed_tp / decided) if decided else None,
        "avg_pnl_pct": float(avg_pnl) if avg_pnl is not None else None,
    }
