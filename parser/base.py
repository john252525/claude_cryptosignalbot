"""Shared LLM parser logic. Provider-specific code lives in sibling modules."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
You extract crypto trading signals from Telegram messages.

Return ONLY a single JSON object, no prose, no markdown fences. Schema:

{
  "is_signal": bool,            // false if the message is not an actionable trade signal
  "symbol": "BTCUSDT" | null,   // normalised: uppercase, no slash/spaces/$/#, append USDT if quote omitted but implied
  "side": "LONG" | "SHORT" | null,
  "market": "futures" | "spot", // default "futures" if leverage mentioned or unclear
  "leverage": number | null,    // e.g. 10 for "10x"
  "entry_low": number | null,   // lower bound of entry zone (or the single entry price)
  "entry_high": number | null,  // upper bound (equal to entry_low if a single price)
  "take_profits": [number, ...],// ordered from nearest to furthest from entry
  "stop_loss": number | null,
  "reason": string              // short explanation if is_signal=false; else ""
}

Rules:
- Numbers must be plain JSON numbers (no strings, no commas, no currency signs).
- Strip emojis, decorations, language. Works across English, Russian, etc.
- "Buy"/"Long"/"Лонг" => LONG. "Sell"/"Short"/"Шорт" => SHORT.
- "Market entry" or "now" with no number => use the same number for entry_low and entry_high equal to null and set is_signal=false ONLY if you truly can't recover a price; otherwise still try.
- If take-profit targets are written as TP1/TP2/Target 1/Цель 1, list them in order.
- If multiple symbols appear, pick the primary one being signalled.
- If the message is commentary, an update on an existing signal, news, or an ad — set is_signal=false.
"""

# Examples in user/assistant role-pair format. Anthropic uses these as `messages`;
# OpenAI-compatible providers (DeepSeek) interleave them the same way.
FEWSHOT = [
    {
        "role": "user",
        "content": (
            "#BTC/USDT LONG \U0001f680\n"
            "Entry: 50000 - 50500\n"
            "Leverage: 10x\n"
            "Targets: 51000, 52000, 53500\n"
            "Stop: 49000"
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "is_signal": True,
                "symbol": "BTCUSDT",
                "side": "LONG",
                "market": "futures",
                "leverage": 10,
                "entry_low": 50000,
                "entry_high": 50500,
                "take_profits": [51000, 52000, 53500],
                "stop_loss": 49000,
                "reason": "",
            }
        ),
    },
    {
        "role": "user",
        "content": "TP1 hit on ETH ✅ move SL to entry",
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "is_signal": False,
                "symbol": None,
                "side": None,
                "market": "futures",
                "leverage": None,
                "entry_low": None,
                "entry_high": None,
                "take_profits": [],
                "stop_loss": None,
                "reason": "Update on existing trade, not a new signal",
            }
        ),
    },
]


@dataclass
class ParsedSignal:
    is_signal: bool
    symbol: str | None
    side: Literal["LONG", "SHORT"] | None
    market: Literal["futures", "spot"]
    leverage: float | None
    entry_low: float | None
    entry_high: float | None
    take_profits: list[float]
    stop_loss: float | None
    reason: str = ""

    def is_complete(self) -> bool:
        return bool(
            self.is_signal
            and self.symbol
            and self.side
            and self.entry_low is not None
            and self.entry_high is not None
            and self.take_profits
            and self.stop_loss is not None
        )


def extract_json(text: str) -> dict:
    text = text.strip()
    # Strip optional ``` fences.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    # Grab the first balanced {...} block.
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in LLM response")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON in LLM response")


def to_parsed_signal(data: dict) -> ParsedSignal:
    """Normalise an LLM JSON response into a ParsedSignal."""
    sym = data.get("symbol")
    if isinstance(sym, str):
        sym = sym.upper().replace("/", "").replace(" ", "").lstrip("$#")

    tps_raw = data.get("take_profits") or []
    tps = [float(x) for x in tps_raw if isinstance(x, (int, float))]

    side_raw = data.get("side")
    side = side_raw.upper() if isinstance(side_raw, str) else None
    if side not in ("LONG", "SHORT"):
        side = None

    market_raw = data.get("market")
    market = market_raw.lower() if isinstance(market_raw, str) else "futures"
    if market not in ("futures", "spot"):
        market = "futures"

    return ParsedSignal(
        is_signal=bool(data.get("is_signal")),
        symbol=sym or None,
        side=side,
        market=market,
        leverage=_to_float(data.get("leverage")),
        entry_low=_to_float(data.get("entry_low")),
        entry_high=_to_float(data.get("entry_high")),
        take_profits=tps,
        stop_loss=_to_float(data.get("stop_loss")),
        reason=str(data.get("reason") or ""),
    )


def empty_signal(reason: str) -> ParsedSignal:
    return ParsedSignal(
        is_signal=False,
        symbol=None,
        side=None,
        market="futures",
        leverage=None,
        entry_low=None,
        entry_high=None,
        take_profits=[],
        stop_loss=None,
        reason=reason,
    )


def _to_float(v: object) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


class BaseLLMParser:
    """Shared parse() pipeline. Subclasses implement `_call_llm(text) -> str`."""

    provider_name: str = "base"

    async def parse(self, text: str) -> ParsedSignal:
        try:
            content = await self._call_llm(text)
        except Exception as e:
            log.warning("parser.api_error", provider=self.provider_name, error=str(e))
            return empty_signal(f"api error: {e}")

        try:
            data = extract_json(content)
        except Exception as e:
            log.warning(
                "parser.bad_json",
                provider=self.provider_name,
                error=str(e),
                content=content[:300],
            )
            return empty_signal(f"bad json: {e}")

        return to_parsed_signal(data)

    async def _call_llm(self, text: str) -> str:
        raise NotImplementedError
