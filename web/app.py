from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from analytics import channel_stats, overall_stats
from db import get_session
from db.models import Channel, Signal, SignalStatus

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="Signal Aggregator")
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        async with get_session() as s:
            overall = await overall_stats(s)
            channels = await channel_stats(s)
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {"overall": overall, "channels": channels},
        )

    @app.get("/channels", response_class=HTMLResponse)
    async def channels_page(request: Request) -> HTMLResponse:
        async with get_session() as s:
            channels = await channel_stats(s)
        return TEMPLATES.TemplateResponse(
            request, "channels.html", {"channels": channels}
        )

    @app.get("/signals", response_class=HTMLResponse)
    async def signals_page(
        request: Request,
        channel: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> HTMLResponse:
        limit = max(1, min(500, limit))
        async with get_session() as s:
            q = select(Signal).order_by(Signal.posted_at.desc()).limit(limit)
            if channel is not None:
                q = q.where(Signal.channel_id == channel)
            if status:
                try:
                    q = q.where(Signal.status == SignalStatus(status))
                except ValueError:
                    pass
            signals = list((await s.execute(q)).scalars().all())
            chans = {c.id: c for c in (await s.execute(select(Channel))).scalars().all()}
        return TEMPLATES.TemplateResponse(
            request,
            "signals.html",
            {
                "signals": signals,
                "channels": chans,
                "filter_channel": channel,
                "filter_status": status,
                "statuses": [s.value for s in SignalStatus],
            },
        )

    @app.get("/signals/{sid}", response_class=HTMLResponse)
    async def signal_detail(request: Request, sid: int) -> HTMLResponse:
        async with get_session() as s:
            sig = (await s.execute(select(Signal).where(Signal.id == sid))).scalar_one_or_none()
            if not sig:
                raise HTTPException(404)
            ch = (await s.execute(select(Channel).where(Channel.id == sig.channel_id))).scalar_one()
            events = list(sig.events)
        return TEMPLATES.TemplateResponse(
            request,
            "signal_detail.html",
            {"sig": sig, "channel": ch, "events": events},
        )

    @app.post("/channels/{cid}/toggle")
    async def toggle_channel(cid: int) -> JSONResponse:
        async with get_session() as s:
            ch = (await s.execute(select(Channel).where(Channel.id == cid))).scalar_one_or_none()
            if not ch:
                raise HTTPException(404)
            ch.enabled = not ch.enabled
            return JSONResponse({"id": cid, "enabled": ch.enabled})

    @app.get("/api/stats")
    async def api_stats() -> JSONResponse:
        async with get_session() as s:
            return JSONResponse(
                {
                    "overall": await overall_stats(s),
                    "channels": [c.__dict__ for c in await channel_stats(s)],
                }
            )

    return app
