from __future__ import annotations

from config import settings
from exchanges.base import PriceFeed
from exchanges.binance import BinancePriceFeed


def get_price_feed() -> PriceFeed:
    """Factory keyed by settings.exchange. Add new venues here."""
    name = settings.exchange.lower()
    if name == "binance":
        return BinancePriceFeed(market=settings.exchange_market)
    raise ValueError(f"Unsupported exchange: {settings.exchange}")
