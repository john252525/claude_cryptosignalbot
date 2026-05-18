from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import structlog
import websockets

from exchanges.base import PriceFeed, Tick

log = structlog.get_logger(__name__)

# Binance public market endpoints.
# Futures (USDT-M perpetuals) — most signal channels reference these.
FUTURES_WS = "wss://fstream.binance.com/ws"
FUTURES_REST = "https://fapi.binance.com"

# Spot.
SPOT_WS = "wss://stream.binance.com:9443/ws"
SPOT_REST = "https://api.binance.com"


class BinancePriceFeed(PriceFeed):
    """WebSocket-based price feed for Binance Futures or Spot.

    Strategy: each symbol subscribes to two streams — `@aggTrade` for tick price
    and `@kline_1m` for high/low of the live 1m candle. The tracker uses the
    candle extremes for hit detection (so it doesn't miss a wick between
    successive trade events).
    """

    def __init__(self, market: str = "futures") -> None:
        self._market = market
        if market == "futures":
            self._ws_url = FUTURES_WS
            self._rest_url = FUTURES_REST
        else:
            self._ws_url = SPOT_WS
            self._rest_url = SPOT_REST

        self._subs: set[str] = set()
        self._sub_lock = asyncio.Lock()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        self._callbacks: list[Callable[[Tick], Awaitable[None]]] = []
        self._queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=10_000)
        self._stop = asyncio.Event()
        self._req_id = 0
        # Last known kline high/low per symbol so each tick carries the candle's extremes.
        self._kline_state: dict[str, tuple[float, float]] = {}
        # Cache of valid symbols from exchangeInfo.
        self._valid_symbols: set[str] | None = None
        # Connection-ready event so subscriptions can wait for WS to open.
        self._ws_ready = asyncio.Event()

    # ---- lifecycle ----

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="binance-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---- subscriptions ----

    async def subscribe(self, symbol: str) -> None:
        sym = symbol.lower()
        async with self._sub_lock:
            if sym in self._subs:
                log.info("binance.already_subscribed", symbol=sym)
                return
            self._subs.add(sym)
        log.info("binance.subscribe_requested", symbol=sym)
        await self._send_subscribe([sym], subscribe=True)

    async def unsubscribe(self, symbol: str) -> None:
        sym = symbol.lower()
        async with self._sub_lock:
            if sym not in self._subs:
                return
            self._subs.discard(sym)
        await self._send_subscribe([sym], subscribe=False)

    async def _send_subscribe(self, syms: list[str], *, subscribe: bool) -> None:
        if not syms:
            return
        await self._ws_ready.wait()
        if self._ws is None:
            return
        streams = []
        for s in syms:
            streams.append(f"{s}@aggTrade")
            streams.append(f"{s}@kline_1m")
        self._req_id += 1
        msg = {
            "method": "SUBSCRIBE" if subscribe else "UNSUBSCRIBE",
            "params": streams,
            "id": self._req_id,
        }
        log.info("binance.send_subscription", method=msg["method"], streams=streams, id=self._req_id)
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            log.warning("binance.sub.send_failed", error=str(e))

    def on_tick(self, callback: Callable[[Tick], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    async def ticks(self) -> AsyncIterator[Tick]:
        while not self._stop.is_set():
            t = await self._queue.get()
            yield t

    # ---- main loop ----

    async def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self._ws_url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self._ws_ready.set()
                    backoff = 1
                    log.info("binance.ws.connected", market=self._market)
                    # Re-subscribe to current set on (re)connect.
                    if self._subs:
                        await self._send_subscribe(list(self._subs), subscribe=True)
                    async for raw in ws:
                        try:
                            await self._handle_message(raw)
                        except Exception as e:
                            log.warning("binance.handle_error", error=str(e))
            except Exception as e:
                self._ws = None
                self._ws_ready.clear()
                if self._stop.is_set():
                    break
                log.warning("binance.ws.reconnect", error=str(e), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _handle_message(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except Exception as e:
            log.warning("binance.parse_error", error=str(e))
            return

        # SUBSCRIBE/UNSUBSCRIBE replies have only {"result": null, "id": N}.
        if "e" not in data:
            if "id" in data:
                log.info("binance.subscription_ack", id=data.get("id"), result=data.get("result"))
            else:
                log.debug("binance.msg_no_event", data_keys=list(data.keys()))
            return

        event = data["e"]
        symbol = data.get("s", "unknown")
        if event == "kline":
            k = data["k"]
            high = float(k["h"])
            low = float(k["l"])
            close = float(k["c"])
            self._kline_state[symbol] = (high, low)
            tick = Tick(symbol=symbol, price=close, high=high, low=low, ts_ms=int(k["T"]))
            await self._emit(tick)
        elif event == "aggTrade":
            price = float(data["p"])
            high, low = self._kline_state.get(symbol, (price, price))
            high = max(high, price)
            low = min(low, price)
            self._kline_state[symbol] = (high, low)
            tick = Tick(symbol=symbol, price=price, high=high, low=low, ts_ms=int(data["T"]))
            await self._emit(tick)

    async def _emit(self, tick: Tick) -> None:
        log.info("binance.tick", symbol=tick.symbol, price=tick.price, high=tick.high, low=tick.low)
        for cb in self._callbacks:
            try:
                await cb(tick)
            except Exception as e:
                log.warning("binance.cb_error", error=str(e))
        try:
            self._queue.put_nowait(tick)
        except asyncio.QueueFull:
            # Drop oldest under back-pressure.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(tick)
            except Exception:
                pass

    # ---- REST helpers ----

    async def _load_symbols(self) -> set[str]:
        if self._valid_symbols is not None:
            return self._valid_symbols
        path = "/fapi/v1/exchangeInfo" if self._market == "futures" else "/api/v3/exchangeInfo"
        async with httpx.AsyncClient(base_url=self._rest_url, timeout=10) as cli:
            r = await cli.get(path)
            r.raise_for_status()
            info = r.json()
        out: set[str] = set()
        for s in info.get("symbols", []):
            status_ok = s.get("status") in (None, "TRADING")
            ct = s.get("contractType")
            # On futures, restrict to PERPETUAL.
            if self._market == "futures" and ct and ct != "PERPETUAL":
                continue
            if status_ok and "symbol" in s:
                out.add(s["symbol"])
        self._valid_symbols = out
        return out

    async def normalize_symbol(self, raw: str) -> str | None:
        s = raw.upper().replace("/", "").replace(" ", "").lstrip("$#")
        try:
            symbols = await self._load_symbols()
        except Exception as e:
            log.warning("binance.exchangeInfo_failed", error=str(e))
            # Fall back to the raw string; tracker will still attempt to subscribe.
            return s
        if s in symbols:
            return s
        # Try common variants: append USDT, swap quote currency.
        candidates = [s + "USDT", s.replace("USD", "USDT"), s.replace("BUSD", "USDT")]
        for c in candidates:
            if c in symbols:
                return c
        return None

    async def get_price(self, symbol: str) -> float | None:
        path = "/fapi/v1/ticker/price" if self._market == "futures" else "/api/v3/ticker/price"
        try:
            async with httpx.AsyncClient(base_url=self._rest_url, timeout=10) as cli:
                r = await cli.get(path, params={"symbol": symbol})
                r.raise_for_status()
                return float(r.json()["price"])
        except Exception as e:
            log.warning("binance.price_failed", symbol=symbol, error=str(e))
            return None
