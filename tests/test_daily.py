"""Daily challenge: import and fallback (TS §3 step 5; S10)."""

import io
import json
import logging
import random
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, FakeClock, make_client, make_settings
from trur import daily, db
from trur.app import create_app
from trur.config import Settings
from trur.errors import ApiError
from trur.solver import DailyEntry, Lexicon, pair_key

DAILY = FIXTURES / "daily"
OCT10, OCT11, OCT12 = date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)
MEDICAL_CORRECT = pair_key("MEDICAL", "CORRECT")
STRONG_MEANING = pair_key("STRONG", "MEANING")


def write_json(path: Path, entries: object) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A tmp copy of valid.json: 10-10 = STRONG × MEANING, 10-11 = MEDICAL × CORRECT."""
    return Path(shutil.copyfile(DAILY / "valid.json", tmp_path / "daily.json"))


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(db_path)
    yield c
    c.close()


def settings_for(db_path: Path, source: Path | str, **overrides: object) -> Settings:
    return make_settings(db_path.parent, daily_json_source=str(source), **overrides)


def stored(conn: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT date, word1, word2, picked_by FROM daily_challenges ORDER BY date")]


# --- import ---

def test_valid_entries_are_stored_as_workflow(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    entries = daily.import_daily(conn, lex, settings_for(db_path, source))
    assert entries == [DailyEntry(OCT10, "STRONG", "MEANING"), DailyEntry(OCT11, "MEDICAL", "CORRECT")]
    assert stored(conn) == [
        ("2026-10-10", "STRONG", "MEANING", "workflow"),
        ("2026-10-11", "MEDICAL", "CORRECT", "workflow"),
    ]


def test_a_stored_date_is_never_overwritten(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-11', 'STRONG', 'MEANING', 'fallback')")
    daily.import_daily(conn, lex, settings_for(db_path, source))
    assert ("2026-10-11", "STRONG", "MEANING", "fallback") in stored(conn)


def test_bad_entries_are_skipped_and_logged(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="trur"):
        entries = daily.import_daily(conn, lex, settings_for(db_path, DAILY / "mixed.json"))
    assert entries == [DailyEntry(OCT11, "MEDICAL", "CORRECT")]
    skipped = [r.getMessage() for r in caplog.records if "skipped" in r.getMessage()]
    assert skipped == [f"daily JSON entry {i} skipped: not a valid pair" for i in range(1, 7)]
    assert stored(conn) == [("2026-10-11", "MEDICAL", "CORRECT", "workflow")]


def failing_sources(tmp_path: Path) -> dict[str, tuple[str, dict]]:
    big = write_json(tmp_path / "big.json", [{"date": "2026-10-11", "word1": "MEDICAL", "word2": "CORRECT"}] * 50)
    return {
        "broken": (str(DAILY / "broken.json"), {}),
        "missing": (str(tmp_path / "nope.json"), {}),
        "over the cap": (str(big), {"daily_fetch_max_bytes": 1000}),
        "not an array": (str(write_json(tmp_path / "obj.json", {"date": "2026-10-11"})), {}),
    }


@pytest.mark.parametrize("case", ["broken", "missing", "over the cap", "not an array"])
def test_a_failing_source_is_logged_and_the_fallback_is_used(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    path, overrides = failing_sources(tmp_path)[case]
    s = settings_for(db_path, path, **overrides)
    with caplog.at_level(logging.WARNING, logger="trur"):
        assert daily.import_daily(conn, lex, s) == []
        entry = daily.ensure_daily_pair(conn, lex, s, random.Random(0), OCT11)
    assert any(r.levelno == logging.ERROR and "could not be read" in r.getMessage() for r in caplog.records)
    assert stored(conn) == [("2026-10-11", entry.word1, entry.word2, "fallback")]


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


HTTPS = "https://raw.githubusercontent.com/FloMoTionGo/TRUR/main/data/daily_challenges.json"


def test_https_source_is_fetched_with_a_timeout(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_urlopen(url: str, timeout: float) -> FakeResponse:
        calls.append((url, timeout))
        return FakeResponse((DAILY / "valid.json").read_bytes())

    monkeypatch.setattr(daily.urllib.request, "urlopen", fake_urlopen)
    assert len(daily.import_daily(conn, lex, settings_for(db_path, HTTPS, daily_fetch_timeout_s=2.5))) == 2
    assert calls == [(HTTPS, 2.5)]


def test_a_fetch_timeout_is_logged_and_the_fallback_is_used(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def timeout(url: str, timeout: float) -> FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(daily.urllib.request, "urlopen", timeout)
    with caplog.at_level(logging.ERROR, logger="trur"):
        entry = daily.ensure_daily_pair(conn, lex, settings_for(db_path, HTTPS), random.Random(0), OCT11)
    assert "daily JSON could not be read: TimeoutError" in caplog.text
    assert stored(conn)[0][3] == "fallback" and entry.date == OCT11


# --- ensure_daily_pair ---

def test_ensure_imports_a_missing_date(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    s = settings_for(db_path, source)
    assert daily.ensure_daily_pair(conn, lex, s, random.Random(0), OCT11) == DailyEntry(OCT11, "MEDICAL", "CORRECT")
    entries = [{"date": "2026-10-12", "word1": "STRONG", "word2": "MEANING"}]
    write_json(source, entries)  # the JSON gains a date after the first import
    assert daily.ensure_daily_pair(conn, lex, s, random.Random(0), OCT12) == DailyEntry(OCT12, "STRONG", "MEANING")
    assert stored(conn)[-1] == ("2026-10-12", "STRONG", "MEANING", "workflow")


def test_fallback_avoids_pairs_in_the_db_and_the_json(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    write_json(source, [{"date": "2026-10-10", "word1": "MEDICAL", "word2": "CORRECT"}])
    s = settings_for(db_path, source)
    for seed in range(5):
        conn.execute("DELETE FROM daily_challenges")
        with caplog.at_level(logging.WARNING, logger="trur"):
            entry = daily.ensure_daily_pair(conn, lex, s, random.Random(seed), OCT11)
        assert (entry.word1, entry.word2) == ("STRONG", "MEANING")
        assert stored(conn)[-1] == ("2026-10-11", "STRONG", "MEANING", "fallback")
    assert "no daily pair for 2026-10-11; picked a fallback pair" in caplog.text


def test_planned_json_pairs_are_avoided_too(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    """Only the JSON knows MEDICAL × CORRECT (planned for a later date): the fallback must not take it."""
    write_json(source, [{"date": "2026-10-20", "word1": "MEDICAL", "word2": "CORRECT"}])
    for seed in range(5):
        conn.execute("DELETE FROM daily_challenges WHERE picked_by = 'fallback'")
        entry = daily.ensure_daily_pair(conn, lex, settings_for(db_path, source), random.Random(seed), OCT11)
        assert pair_key(entry.word1, entry.word2) == STRONG_MEANING


def test_no_unused_pair_is_503(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-09', 'STRONG', 'MEANING', 'workflow')")
    write_json(source, [{"date": "2026-10-10", "word1": "MEDICAL", "word2": "CORRECT"}])
    with pytest.raises(ApiError) as exc:
        daily.ensure_daily_pair(conn, lex, settings_for(db_path, source), random.Random(0), OCT11)
    assert exc.value.status == 503 and exc.value.code == "no_pair_available"
    assert daily.get_daily_pair(conn, OCT11) is None


def picks_alone(lex: Lexicon, db_path: Path, s: Settings, seed: int) -> frozenset[str]:
    conn = db.connect(db_path)
    try:
        entry = daily.ensure_daily_pair(conn, lex, s, random.Random(seed), OCT11)
        conn.execute("DELETE FROM daily_challenges")
        return pair_key(entry.word1, entry.word2)
    finally:
        conn.close()


def test_fallback_race_stores_one_pair(lex: Lexicon, db_path: Path, source: Path) -> None:
    s = settings_for(db_path, write_json(source, []))
    picks = {seed: picks_alone(lex, db_path, s, seed) for seed in range(20)}
    seed_a = next(k for k, v in picks.items() if v == MEDICAL_CORRECT)
    seed_b = next(k for k, v in picks.items() if v == STRONG_MEANING)  # alone, the two seeds pick different pairs

    barrier = threading.Barrier(2)
    results: dict[int, DailyEntry] = {}

    def run(seed: int) -> None:
        conn = db.connect(db_path)
        try:
            barrier.wait()
            results[seed] = daily.ensure_daily_pair(conn, lex, s, random.Random(seed), OCT11)
        finally:
            conn.close()

    threads = [threading.Thread(target=run, args=(seed,)) for seed in (seed_a, seed_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    conn = db.connect(db_path)
    try:
        assert len(stored(conn)) == 1
    finally:
        conn.close()
    assert results[seed_a] == results[seed_b]


# --- startup ---

def startup_rows(db_path: Path, clock: FakeClock, source: Path) -> list[tuple]:
    with make_client(create_app(settings_for(db_path, source), clock)):
        pass
    conn = db.connect(db_path)
    try:
        return stored(conn)
    finally:
        conn.close()


def test_startup_imports_when_today_or_tomorrow_is_missing(db_path: Path, clock: FakeClock, source: Path) -> None:
    assert [r[0] for r in startup_rows(db_path, clock, source)] == ["2026-10-10", "2026-10-11"]


def test_startup_skips_the_import_when_both_days_are_stored(
    db_path: Path, clock: FakeClock, source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(db_path)
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-11', 'MEDICAL', 'CORRECT', 'workflow')")
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-12', 'STRONG', 'MEANING', 'workflow')")
    conn.close()
    calls: list[object] = []
    monkeypatch.setattr(daily, "import_daily", lambda *a: calls.append(a) or [])
    startup_rows(db_path, clock, source)
    assert calls == []


def test_a_broken_source_doesnt_stop_startup(db_path: Path, clock: FakeClock) -> None:
    assert startup_rows(db_path, clock, DAILY / "broken.json") == []
