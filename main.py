from __future__ import annotations

import asyncio
import logging
import signal as _signal
import sys

import structlog
import uvicorn

from config import settings
from db import init_db
from exchanges import get_price_feed
from ingest import IngestPipeline
from ingest.bot_source import BotSource
from ingest.telethon_source import TelethonSource
from parser import get_parser
from tracker import ExecutionTracker
from web import create_app


def _configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


async def _run_web(app) -> None:
    cfg = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.effective_port,
        log_level="warning",
    )
    server = uvicorn.Server(cfg)
    await server.serve()


async def main() -> None:
    _configure_logging()
    log = structlog.get_logger("main")
    await init_db()

    if not (settings.anthropic_api_key or settings.deepseek_api_key):
        log.error(
            "missing.llm_api_key",
            hint="set ANTHROPIC_API_KEY or DEEPSEEK_API_KEY in env",
        )
        sys.exit(2)
    if not (settings.telethon_enabled or settings.bot_enabled):
        log.error("no_source_enabled", hint="enable TELETHON_ENABLED or BOT_ENABLED in .env")
        sys.exit(2)

    try:
        parser = get_parser()
    except Exception as e:
        log.error("parser.init_failed", error=str(e))
        sys.exit(2)

    feed = get_price_feed()
    await feed.start()

    # Test REST API connectivity
    try:
        price = await feed.get_price("XRPUSDT")
        log.info("startup.rest_api_test", symbol="XRPUSDT", price=price)
    except Exception as e:
        log.warning("startup.rest_api_test_failed", error=str(e))

    tracker = ExecutionTracker(feed)
    await tracker.start()

    pipeline = IngestPipeline(parser=parser, tracker=tracker, feed=feed)

    sources = []
    if settings.telethon_enabled:
        try:
            tg = TelethonSource(pipeline)
            await tg.start()
            sources.append(tg)
        except Exception as e:
            log.error("telethon.start_failed", error=str(e))
    if settings.bot_enabled:
        try:
            bot = BotSource(pipeline)
            await bot.start()
            sources.append(bot)
        except Exception as e:
            log.error("bot.start_failed", error=str(e))

    app = create_app()
    web_task = asyncio.create_task(_run_web(app), name="web")

    stop_evt = asyncio.Event()
    loop = asyncio.get_event_loop()
    for s in (_signal.SIGINT, _signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop_evt.set)
        except NotImplementedError:
            pass

    log.info(
        "startup.complete",
        exchange=settings.exchange,
        market=settings.exchange_market,
        telethon=settings.telethon_enabled,
        bot=settings.bot_enabled,
        web=f"http://{settings.web_host}:{settings.effective_port}",
        db=settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url,
    )

    await stop_evt.wait()
    log.info("shutdown.starting")

    for src in sources:
        try:
            await src.stop()
        except Exception as e:
            log.warning("source.stop_error", error=str(e))
    await tracker.stop()
    await feed.stop()
    web_task.cancel()
    try:
        await web_task
    except (asyncio.CancelledError, Exception):
        pass
    log.info("shutdown.complete")


if __name__ == "__main__":
    asyncio.run(main())
