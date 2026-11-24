"""FastAPI application factory."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from . import models  # noqa: F401  (register tables)
from .api import router
from .config import Settings, load_settings
from .db import Base, make_engine, make_session_factory
from .models import Campaign, Search, utcnow

log = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def thread_spawn(fn: Callable, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def inline_spawn(fn: Callable, *args) -> None:
    fn(*args)


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    spawn: Callable | None = None,
    osm=None,
    fetcher=None,
    bolagsverket=None,
    sender=None,
    sleep=None,
) -> FastAPI:
    """Build the app. Everything external (providers, SMTP, threads) can be injected for tests."""
    import time

    settings = settings or load_settings()
    if session_factory is None:
        Path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
        if settings.database_url.startswith("sqlite:///") and ":memory:" not in settings.database_url:
            Path(settings.database_url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine(settings.database_url)
        Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)
    else:
        Base.metadata.create_all(session_factory.kw["bind"])

    with session_factory() as db:  # recover from a previous crash/restart
        db.execute(update(Campaign).where(Campaign.status == "sending").values(
            status="paused", note="Interrupted by a restart. Press Send to continue."))
        for s in db.scalars(select(Search).where(Search.status.in_(["pending", "running"]))):
            s.status, s.message, s.finished_at = "failed", "Interrupted by a restart.", utcnow()
        db.commit()

    app = FastAPI(title="Lead Finder", version="0.1.0")
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.spawn = spawn or thread_spawn
    app.state.osm = osm
    app.state.fetcher = fetcher
    app.state.bolagsverket = bolagsverket
    app.state.sender = sender
    app.state.sleep = sleep or time.sleep

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: `uvicorn app.main:app_factory --factory`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
