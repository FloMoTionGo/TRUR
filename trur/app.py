"""The app factory: lifespan, middleware, routes and the static mount (docs/architecture.md §2).

Run with `uvicorn trur.app:create_app --factory --workers 1`. One worker, because the rate
limiter and the daily lock live in memory.
"""

import logging
import random
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from trur import auth, db, games
from trur.api import install_error_handlers, router
from trur.clock import Clock, SystemClock
from trur.config import Settings, load_settings
from trur.security import BodySizeLimit, InternalErrors, SecurityHeaders
from trur.solver import load_lexicon

log = logging.getLogger("trur")

MIN_SQLITE = (3, 37, 0)  # STRICT tables


def _startup(app: FastAPI) -> None:
    s: Settings = app.state.settings
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old; STRICT tables need 3.37+")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    lex = load_lexicon(s.lexicon_dir)
    blocklist = auth.load_blocklist(s.lexicon_dir)
    db.migrate(s.db_path)
    conn = db.connect(s.db_path)
    try:
        db.sync_words(conn, lex, db.files_sha256(s.lexicon_dir / "words.txt", s.lexicon_dir / "source_words.txt"))
        purged = db.purge_events(conn, app.state.clock.now() - timedelta(days=s.event_retention_days))
        if purged:
            log.info("purged %d events older than %d days", purged, s.event_retention_days)
        # Lifespan step 6 (import the daily JSON) comes with T20.
        closed = games.sweep_abandoned(conn, lex, s, app.state.clock.now())
        if closed:
            log.info("closed %d abandoned games from past days", closed)
    finally:
        conn.close()
    app.state.lexicon = lex
    app.state.blocklist = blocklist


def create_app(settings: Settings | None = None, clock: Clock | None = None, rng: random.Random | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _startup(app)
        yield

    docs = settings.debug
    app = FastAPI(
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    app.state.rng = rng or random.Random()
    install_error_handlers(app)
    app.include_router(router)
    # Starlette runs the last-added middleware outermost: headers wrap everything, including the 413 and 500.
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes)
    app.add_middleware(InternalErrors)
    app.add_middleware(SecurityHeaders, hsts=settings.hsts)
    if settings.web_dir.is_dir():  # web/ is the only static root (S9); mounted last so the API wins
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app
