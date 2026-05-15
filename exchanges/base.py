from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Tick:
    symbol: str   # exchange-normalised (e.g. BTCUSDT)
    price: float
    high: float   # high of the current bar window (>= price)
    low: float    # low of the current bar window (<= price)
    ts_ms: int


class PriceFeed(abc.ABC):
    """Streams live tick/bar data and exposes per-symbol subscription management.

    Implementations must let the tracker subscribe to a dynamic set of symbols
    and receive a callback (or async iterator) of Ticks.
    """

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @abc.abstractmethod
    async def subscribe(self, symbol: str) -> None: ...

    @abc.abstractmethod
    async def unsubscribe(self, symbol: str) -> None: ...

    @abc.abstractmethod
    def on_tick(self, callback: Callable[[Tick], Awaitable[None]]) -> None:
        """Register an async callback for incoming ticks."""

    @abc.abstractmethod
    async def ticks(self) -> AsyncIterator[Tick]:
        """Iterate ticks (alternative to on_tick)."""

    @abc.abstractmethod
    async def normalize_symbol(self, raw: str) -> str | None:
        """Return the exchange-canonical symbol or None if not listed."""

    @abc.abstractmethod
    async def get_price(self, symbol: str) -> float | None:
        """Best-effort current price (REST)."""


class OrderExecutor(abc.ABC):
    """Place/manage real orders. Not implemented in the MVP — reserved for the
    'forward best signals to a real account' phase. Concrete impls will live
    alongside PriceFeed (e.g. BinanceOrderExecutor)."""

    @abc.abstractmethod
    async def place(self, *, symbol: str, side: str, qty: float, **kw: object) -> str: ...

    @abc.abstractmethod
    async def cancel(self, order_id: str) -> None: ...
