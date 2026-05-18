# Signal Aggregator (MVP)

Ingests crypto trading signals from Telegram channels, parses them with Claude,
tracks each one against live Binance prices to determine whether it closed on
take-profit or stop-loss, and surfaces per-channel performance on a web
dashboard so you can decide which sources to forward to a real exchange.

The exchange layer is abstracted (`exchanges/base.py`) so adding Bybit / OKX /
etc. is a matter of writing one more `PriceFeed` and registering it.

## Deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new)

→ **[Step-by-step Railway deployment guide](./DEPLOY.md)** (~5 min, ~$15/mo)

For local development, see "Quick start" below.

## Pipeline

```
   Telegram (Telethon user account)              Telegram (bot, forwarded msgs)
                  │                                            │
                  └────────────────────┬───────────────────────┘
                                       ▼
                        IngestPipeline  ── LLM parser (Claude Haiku | DeepSeek)
                                       │            │
                                       │            ▼
                                       │    {symbol, side, entry, TP[], SL}
                                       ▼
                                  SQLite (Signal: PENDING)
                                       │
                                       ▼
                          ExecutionTracker  ◀── Binance WebSocket (aggTrade + kline_1m)
                                       │
                          status: PENDING → ACTIVE → CLOSED_TP / CLOSED_SL / EXPIRED
                                       │
                                       ▼
                                FastAPI dashboard
                                (winrate, P&L, leaderboard)
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill ANTHROPIC_API_KEY *or* DEEPSEEK_API_KEY, plus at least one ingestion source
python -m main
# open http://localhost:8000
```

### LLM provider

Set `LLM_PROVIDER=anthropic|deepseek|auto`. With `auto` (default), whichever
key is present is used; if both are set, Anthropic wins. To force DeepSeek
even when Anthropic key is also set, use `LLM_PROVIDER=deepseek`.

| Provider  | Model              | ~cost/signal | Note                       |
|-----------|--------------------|--------------|----------------------------|
| anthropic | claude-haiku-4-5   | ~$0.001      | Default                    |
| deepseek  | deepseek-chat      | ~$0.0003     | OpenAI-compatible API      |

### Ingestion options

You can run either or both at the same time.

**Option A — Telethon (recommended for public channels).**
A user account joins / subscribes to the channels and reads new posts in real time.

1. Get `api_id` and `api_hash` at https://my.telegram.org → API development tools.
2. **Get session string** (one-time step):
   - **On Railway** (web shell):
     ```bash
     railway run python scripts/get_telethon_session.py
     # Enter credentials + SMS code → copy SESSION_STRING
     ```
   - **On local VPS**:
     ```bash
     python scripts/get_telethon_session.py
     # Enter credentials + SMS code → copy SESSION_STRING
     ```
3. `.env`:
   ```
   TELETHON_ENABLED=true
   TELETHON_API_ID=...
   TELETHON_API_HASH=...
   TELETHON_PHONE=+1234567890
   TELETHON_SESSION_STRING=<paste-from-step-2>
   TELETHON_CHANNELS=daytrader_signals,another_channel
   ```
4. Done. No interactive login on restart needed. Session lives in memory.

**Legacy: file-based session** (if TELETHON_SESSION_STRING not set):
   First run will prompt for SMS code, session cached at `data/signals.session`.
   Useful for development but requires file persistence on Railway (volume or keep
   session file in git after first auth).

**Option B — Bot (works for any channel, even private, via manual forwarding).**

1. Create a bot via @BotFather, copy the token.
2. Get your numeric Telegram id (e.g. via @userinfobot).
3. `.env`:
   ```
   BOT_ENABLED=true
   BOT_TOKEN=...
   BOT_ALLOWED_USERS=123456789
   ```
4. DM the bot once, then forward signals to it. Origin channel is detected
   from the forward metadata, so per-channel stats still work.

### Without Telegram (manual demo)

```bash
echo "BTCUSDT LONG entry 60000-60500, tp 61000 62000 63500, sl 59000" \
  | python scripts/ingest_text.py --channel demo
python -m main  # then visit /signals
```

## What the tracker does

For every non-terminal signal it subscribes to two Binance streams for the
symbol: `@aggTrade` (live last price) and `@kline_1m` (high/low of the
current candle). Each tick carries the running candle high/low — this catches
wicks that would be missed if we only looked at the last trade price.

Transitions:

| From    | To         | Trigger                                                        |
|---------|------------|----------------------------------------------------------------|
| PENDING | ACTIVE     | candle range overlaps the entry zone (single price uses ±0.2%) |
| PENDING | EXPIRED    | no entry hit within `SIGNAL_TTL_HOURS`                         |
| ACTIVE  | CLOSED_SL  | LONG: low ≤ SL; SHORT: high ≥ SL                               |
| ACTIVE  | (TP_n hit) | LONG: high ≥ TP_n; SHORT: low ≤ TP_n                           |
| ACTIVE  | CLOSED_TP  | all TPs hit                                                    |

P&L is recorded in percent of entry (no leverage). For multi-TP closes it's
the equal-weight mean across all TPs — channels rarely specify per-TP sizing,
so this is a deliberately simple approximation.

## Adding another exchange

1. Implement `exchanges/<name>.py` subclassing `PriceFeed`.
2. Add a branch to `exchanges/registry.get_price_feed()`.
3. Set `EXCHANGE=<name>` in `.env`.

The tracker, ingest pipeline, and analytics are exchange-agnostic.

The `OrderExecutor` abstract class in `exchanges/base.py` is the seam where
"forward the best signals to a real account" plugs in — that's a follow-up
phase, not part of the MVP.

## Layout

```
config.py                pydantic-settings, reads .env
main.py                  orchestrator (boots feed, tracker, sources, web)
db/models.py             Channel, Signal, SignalEvent
parser/base.py           Shared prompt, JSON extraction, ParsedSignal
parser/anthropic_parser.py
parser/deepseek_parser.py
parser/registry.py       Picks provider from LLM_PROVIDER or auto-detect
exchanges/base.py        PriceFeed / OrderExecutor abstractions
exchanges/binance.py     WS + REST impl
exchanges/registry.py    factory
ingest/pipeline.py       dedup, parse, persist, register-with-tracker
ingest/telethon_source.py
ingest/bot_source.py
tracker/execution_tracker.py
analytics/stats.py
web/app.py               FastAPI; /, /channels, /signals
tests/                   tracker + parser unit tests
scripts/ingest_text.py   manual ingest for debugging
```

## Tests

```bash
pip install pytest pytest-asyncio
pytest -q
```

## Notes / limitations

- One worker process. Tick handler is per-signal-row, so very high signal volume
  on the same symbol will scale linearly with DB writes — fine for MVP load.
- Entry detection accepts any candle range overlap with the entry zone. For
  single-price entries the band is `±ENTRY_TOLERANCE_PCT` (default 0.2%).
- LLM call cost is ~$0.001 per parsed message (Claude Haiku) or ~$0.0003 (DeepSeek).
- The bot source only processes messages from `BOT_ALLOWED_USERS` (or any user
  if that list is empty — useful for solo deployments).
