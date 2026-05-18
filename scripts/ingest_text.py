"""Manually ingest a signal from a text file or stdin.

Useful for debugging the parser/tracker without a live Telegram source.

Examples:
    echo "BTCUSDT LONG entry 50000 tps 51000 52000 sl 49000" | python scripts/ingest_text.py
    python scripts/ingest_text.py --file signal.txt --channel demo
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make the project root importable when run as `python scripts/ingest_text.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import init_db  # noqa: E402
from exchanges import get_price_feed  # noqa: E402
from ingest.pipeline import IngestPipeline  # noqa: E402
from parser import get_parser  # noqa: E402
from tracker import ExecutionTracker  # noqa: E402


async def main(text: str, channel: str) -> None:
    await init_db()
    feed = get_price_feed()
    parser = get_parser()
    tracker = ExecutionTracker(feed)
    pipeline = IngestPipeline(parser=parser, tracker=tracker, feed=feed)
    result = await pipeline.ingest(
        source="manual",
        channel_external_id=channel,
        channel_title=channel,
        tg_message_id=None,
        text=text,
    )
    print(f"\nresult: {result.status.value}")
    if result.signal_id:
        print(f"signal_id: {result.signal_id}")
    if result.symbol:
        print(f"symbol: {result.symbol} {result.side or ''}")
    if result.detail:
        print(f"detail: {result.detail}")
    print("\nRun `python -m main` to start tracker + web dashboard.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, help="read text from a file instead of stdin")
    ap.add_argument("--channel", default="manual", help="channel external_id to attribute to")
    args = ap.parse_args()
    text = args.file.read_text() if args.file else sys.stdin.read()
    asyncio.run(main(text, args.channel))
