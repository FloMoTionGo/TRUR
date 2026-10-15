import random
import shutil
import socket
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trur import db
from trur.app import create_app
from trur.config import Settings
from trur.solver import Lexicon, load_lexicon, pick_pair

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = FIXTURES.parent.parent
ORIGIN = "https://testserver"
DEFAULT_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("trur", "TRUR desktop baseline (docs/test-strategy.md §7)")
    group.addoption("--update-baselines", action="store_true",
                    help="rewrite tests/e2e/baselines/: only in P1 or for a desktop change the owner approved")
    group.addoption("--all-shapes", action="store_true", help="check D3 over all 72 grid shapes, not the per-push 8")
    group.addoption("--devices", default="core",
                    help="phone devices for the mobile tests: core (default), full, or ids like s360,se-bars")


class FakeClock:
    """Test clock (docs/test-strategy.md §2). Starts at 2026-10-11T12:00:00Z."""

    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        self.set(now)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("FakeClock needs a UTC-aware datetime")
        self._now = now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(scope="session")
def lex() -> Lexicon:
    """The fixture dictionary (docs/test-strategy.md §2)."""
    return load_lexicon(FIXTURES)


def fixture_words_sha256() -> str:
    return db.files_sha256(FIXTURES / "words.txt", FIXTURES / "source_words.txt")


@pytest.fixture(scope="session")
def template_db(tmp_path_factory: pytest.TempPathFactory, lex: Lexicon) -> Path:
    """Schema plus fixture words, built once per session (docs/test-strategy.md §2)."""
    path = tmp_path_factory.mktemp("template") / "trur.db"
    db.migrate(path)
    conn = db.connect(path)
    try:
        db.sync_words(conn, lex, fixture_words_sha256())
    finally:
        conn.close()
    return path


@pytest.fixture
def db_path(template_db: Path, tmp_path: Path) -> Path:
    """A fresh copy of the template database as tmp_path/trur.db, the default DATA_DIR layout."""
    path = tmp_path / "trur.db"
    shutil.copyfile(template_db, path)
    return path


def db_snapshot(path: Path) -> tuple[list[str], dict[str, int]]:
    """Schema SQL from sqlite_master plus the row count of every table (S1 checks)."""
    conn = sqlite3.connect(path)
    try:
        schema = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()
    return schema, counts


# --- app and clients (docs/test-strategy.md §2, docs/architecture.md §5 "Test" column) ---

def make_settings(data_dir: Path, **overrides: object) -> Settings:
    values: dict[str, object] = dict(
        data_dir=data_dir,
        lexicon_dir=FIXTURES,
        web_dir=ROOT / "web",
        daily_json_source=str(FIXTURES / "daily" / "valid.json"),
        allowed_origins=(ORIGIN,),
        min_free_words=1,
        max_free_words=0,
        min_match_words=1,
        submit_min_gap_s=0.0,
        match_submit_min_gap_s=0.0,
        match_tick_ms=0,  # no ticker: tests drive tick() themselves (docs/test-strategy.md §6)
        sse_keepalive_s=0.2,
        rl_new_players_per_ip_hour=0,
        rl_games_per_player_hour=0,
        rl_events_per_player_s=0,
        rl_requests_per_ip_min=0,
        rl_queue_joins_per_player_hour=0,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return make_settings(db_path.parent)


def seed_for(lex: Lexicon, word1: str, word2: str, min_words: int = 1) -> int:
    """A seed whose first free-play pick is word1 × word2."""
    accept = lambda w1, w2, answers: len(answers) >= min_words  # noqa: E731
    return next(s for s in range(10_000) if pick_pair(lex, random.Random(s), accept)[:2] == (word1, word2))


@pytest.fixture(scope="session")
def garden_seed(lex: Lexicon) -> int:
    return seed_for(lex, "GARDEN", "SUMMER")


@pytest.fixture
def app(settings: Settings, clock: FakeClock, garden_seed: int) -> FastAPI:
    """The first free game this app picks is GARDEN × SUMMER (docs/test-strategy.md §5 setup)."""
    return create_app(settings, clock, random.Random(garden_seed))


def make_client(app: FastAPI) -> TestClient:
    """HTTPS, or the Secure cookie never comes back; an allowed Origin on every request."""
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with make_client(app) as c:  # the context manager runs the lifespan
        yield c


@pytest.fixture
def player(app: FastAPI, client: TestClient) -> Iterator[Callable[[str], TestClient]]:
    """player("Alice") registers a player with their own cookie jar (the app's lifespan already runs)."""
    made: list[TestClient] = []

    def make(nickname: str) -> TestClient:
        c = make_client(app)
        made.append(c)
        r = c.post("/api/users", json={"nickname": nickname})
        assert r.status_code == 201, r.text
        return c

    yield make
    for c in made:
        c.close()


# --- a real server, for requests that truly overlap (docs/test-strategy.md §1) ---

class LiveServer(NamedTuple):
    base_url: str
    app: FastAPI
    clock: FakeClock
    stop: Callable[[], None]  # shuts the server down as a deploy would; the fixture calls it too


@pytest.fixture
def live_overrides() -> dict[str, object]:
    """Extra settings for live_server; a test changes them with @pytest.mark.parametrize("live_overrides", [...])."""
    return {}


def serve(
    db_path: Path, clock: FakeClock, seed: int, overrides: dict[str, object], port: int = 0, **config: object,
) -> Iterator[LiveServer]:
    """port: 0 picks a free one; a test restarts a stopped server on its old port, as a deploy would."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if port and sys.platform != "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # the old server's connections may be in TIME_WAIT
    sock.bind(("127.0.0.1", port))
    base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings = make_settings(db_path.parent, allowed_origins=(base_url,), **overrides)
    app = create_app(settings, clock, random.Random(seed))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on", **config))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.01)

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10)

    try:
        yield LiveServer(base_url, app, clock, stop)
    finally:
        stop()
        sock.close()


@pytest.fixture
def live_server(
    db_path: Path, clock: FakeClock, garden_seed: int, live_overrides: dict[str, object],
) -> Iterator[LiveServer]:
    """uvicorn in a thread of the test process on a random port, sharing the test's FakeClock.

    It serves plain http, so the Secure cookie isn't sent back by httpx's jar: tests pass the
    token in a Cookie header themselves (see live_player in test_games.py).
    """
    yield from serve(db_path, clock, garden_seed, live_overrides)


@pytest.fixture
def live_match_server(
    db_path: Path, clock: FakeClock, garden_seed: int, live_overrides: dict[str, object],
) -> Iterator[LiveServer]:
    """live_server with the real lobby ticker every 10 ms, and streams cut 2 s into a shutdown, as
    in the Dockerfile (docs/test-strategy.md §6). Its first match board is GARDEN × SUMMER: the
    pick draws from the app's rng, like free play."""
    yield from serve(db_path, clock, garden_seed, {"match_tick_ms": 10, **live_overrides}, timeout_graceful_shutdown=2)
