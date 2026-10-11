import random
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trur import db
from trur.app import create_app
from trur.config import Settings
from trur.solver import Lexicon, load_lexicon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = FIXTURES.parent.parent
ORIGIN = "https://testserver"
DEFAULT_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


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
        submit_min_gap_s=0.0,
        rl_new_players_per_ip_hour=0,
        rl_games_per_player_hour=0,
        rl_events_per_player_s=0,
        rl_requests_per_ip_min=0,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return make_settings(db_path.parent)


@pytest.fixture
def app(settings: Settings, clock: FakeClock) -> FastAPI:
    return create_app(settings, clock, random.Random(0))


def make_client(app: FastAPI) -> TestClient:
    """HTTPS, or the Secure cookie never comes back; an allowed Origin on every request."""
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with make_client(app) as c:  # the context manager runs the lifespan
        yield c
