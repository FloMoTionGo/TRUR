import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trur import db
from trur.solver import Lexicon, load_lexicon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
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
