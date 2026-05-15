from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SignalSide(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, enum.Enum):
    PENDING = "PENDING"          # Entry not yet hit.
    ACTIVE = "ACTIVE"            # Entry hit, position open.
    CLOSED_TP = "CLOSED_TP"      # All TPs hit (or last one).
    CLOSED_SL = "CLOSED_SL"      # Stop-loss hit.
    EXPIRED = "EXPIRED"          # TTL elapsed without entry.
    INVALID = "INVALID"          # Failed to parse / missing fields.


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "telethon" or "bot". For bot source we record the forward origin chat.
    source: Mapped[str] = mapped_column(String(32))
    # Stable external identifier (tg channel id or @username).
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    signals: Mapped[list["Signal"]] = relationship(back_populates="channel")

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_channel_source_extid"),)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    channel: Mapped[Channel] = relationship(back_populates="signals")

    # Original Telegram message id, for dedup.
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text)

    # Parsed fields.
    symbol: Mapped[str] = mapped_column(String(32), index=True)  # e.g. BTCUSDT
    side: Mapped[SignalSide] = mapped_column(Enum(SignalSide))
    market: Mapped[str] = mapped_column(String(16), default="futures")
    leverage: Mapped[float | None] = mapped_column(Float, nullable=True)

    entry_low: Mapped[float] = mapped_column(Float)
    entry_high: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    # JSON list[float], ordered.
    take_profits: Mapped[list[float]] = mapped_column(JSON, default=list)

    status: Mapped[SignalStatus] = mapped_column(
        Enum(SignalStatus), default=SignalStatus.PENDING, index=True
    )
    # Index of the highest TP that has been hit so far (-1 = none).
    last_tp_hit_index: Mapped[int] = mapped_column(Integer, default=-1)

    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # P&L is computed from entry_avg and exit prices, expressed as percent of entry (no leverage).
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    parse_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    events: Mapped[list["SignalEvent"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan", order_by="SignalEvent.created_at"
    )

    @property
    def entry_avg(self) -> float:
        return (self.entry_low + self.entry_high) / 2.0


class SignalEvent(Base):
    """Audit trail: entry hit, each TP hit, SL hit, expiration."""

    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    signal: Mapped[Signal] = relationship(back_populates="events")

    kind: Mapped[str] = mapped_column(String(32))  # ENTRY, TP, SL, EXPIRED
    detail: Mapped[str] = mapped_column(String(128), default="")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
