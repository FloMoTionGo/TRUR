"""Database layer: migrations, authorizer, read-only connection, bookkeeping (T9; TS §4 S1, S5)."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import db_snapshot, fixture_words_sha256
from trur import db
from trur.solver import Lexicon

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(db_path: Path):
    c = db.connect(db_path)
    yield c
    c.close()


def _add_user(conn: sqlite3.Connection, uid: str = "u1") -> None:
    conn.execute(
        "INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, "hash-" + uid, "Nick" + uid, "nick" + uid, db.iso(NOW)),
    )


def _add_game(conn: sqlite3.Connection, gid: str = "g1", uid: str = "u1") -> None:
    conn.execute(
        "INSERT INTO games (id, user_id, mode, word1, word2, total_words, started_at) "
        "VALUES (?, ?, 'free', 'GARDEN', 'SUMMER', 9, ?)",
        (gid, uid, db.iso(NOW)),
    )


def _add_submission(conn: sqlite3.Connection, word: str, valid: int) -> None:
    conn.execute(
        "INSERT INTO submissions (user_id, game_id, game_mode, word, is_valid, points, submitted_at, month) "
        "VALUES ('u1', 'g1', 'free', ?, ?, 4, ?, '2026-10')",
        (word, valid, db.iso(NOW)),
    )


# --- migrations ---

def test_migrate_sets_version_wal_and_strict_tables(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    db.migrate(path)
    db.migrate(path)  # a second run applies nothing
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tables == {"users", "daily_challenges", "games", "submissions", "rankings", "events", "words", "meta"}
        assert all("STRICT" in r[0] for r in raw.execute("SELECT sql FROM sqlite_master WHERE type = 'table'"))
    finally:
        raw.close()


# --- the authorizer (S1) ---

@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE submissions",
        "DROP INDEX submissions_valid_once",
        "ALTER TABLE users ADD COLUMN x TEXT",
        "CREATE TABLE evil (x TEXT)",
        "CREATE TEMP TABLE evil (x TEXT)",
        "CREATE INDEX evil ON users(nickname)",
        "CREATE VIEW evil AS SELECT 1",
        "CREATE TRIGGER evil AFTER INSERT ON users BEGIN DELETE FROM users; END",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA user_version = 1",
        "PRAGMA foreign_keys = OFF",
        "PRAGMA journal_mode = DELETE",
        "SELECT load_extension('evil')",
    ],
)
def test_authorizer_refuses_schema_and_pragma_writes(conn: sqlite3.Connection, db_path: Path, sql: str) -> None:
    before = db_snapshot(db_path)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute(sql)
    assert db_snapshot(db_path) == before


def test_detach_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute("DETACH DATABASE main")


def test_a_games_statements_still_work(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)  # PRAGMA reads are fine
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    with db.tx(conn):
        _add_user(conn)
        _add_game(conn)
        _add_submission(conn, "GEAR", 1)
        _add_submission(conn, "GSAS", 0)
        conn.execute("UPDATE games SET score = score + 4, wrong_count = wrong_count + 1 WHERE id = 'g1'")
    game = conn.execute("SELECT score, wrong_count FROM games WHERE id = ?", ("g1",)).fetchone()
    assert (game["score"], game["wrong_count"]) == (4, 1)
    conn.execute("DELETE FROM users WHERE id = 'u1'")  # cascades through the foreign keys
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0


def test_tx_rolls_back_on_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError):
        with db.tx(conn):
            _add_user(conn)
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert not conn.in_transaction


def test_valid_word_is_unique_per_game(conn: sqlite3.Connection) -> None:
    """S5: the unique index stops double scoring even if the code had a bug."""
    _add_user(conn)
    _add_game(conn)
    _add_submission(conn, "GEAR", 1)
    _add_submission(conn, "GSAS", 0)
    _add_submission(conn, "GSAS", 0)  # rejected words may repeat
    with pytest.raises(sqlite3.IntegrityError):
        _add_submission(conn, "GEAR", 1)


def test_one_open_free_game_per_player(conn: sqlite3.Connection) -> None:
    _add_user(conn)
    _add_game(conn, "g1")
    with pytest.raises(sqlite3.IntegrityError):
        _add_game(conn, "g2")


# --- read-only connection ---

def test_read_only_connection_refuses_insert(db_path: Path) -> None:
    ro = db.connect_ro(db_path)
    try:
        assert ro.execute("SELECT COUNT(*) FROM words").fetchone()[0] > 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro.execute("INSERT INTO meta (key, value) VALUES ('x', 'y')")
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            ro.execute("DROP TABLE meta")
    finally:
        ro.close()


# --- bookkeeping ---

def test_iso_is_fixed_width_utc() -> None:
    assert db.iso(NOW) == "2026-10-11T12:00:00.000000Z"
    kiritimati = timezone(timedelta(hours=14))
    assert db.iso(datetime(2026, 10, 12, 2, 0, 0, 5, tzinfo=kiritimati)) == "2026-10-11T12:00:00.000005Z"


def test_sync_words_fills_the_table(conn: sqlite3.Connection, lex: Lexicon) -> None:
    assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == len(lex.words)
    rows = conn.execute("SELECT word, length, freq_rank FROM words WHERE is_source = 1 ORDER BY freq_rank").fetchall()
    assert [(r["word"], r["length"], r["freq_rank"]) for r in rows] == [
        (w, len(w), i + 1) for i, w in enumerate(lex.source)
    ]
    assert conn.execute("SELECT COUNT(*) FROM words WHERE is_source = 0 AND freq_rank IS NOT NULL").fetchone()[0] == 0


def test_sync_words_is_a_noop_when_the_hash_is_unchanged(conn: sqlite3.Connection, lex: Lexicon) -> None:
    conn.execute("DELETE FROM words WHERE word = 'GEAR'")
    db.sync_words(conn, lex, fixture_words_sha256())
    assert conn.execute("SELECT COUNT(*) FROM words WHERE word = 'GEAR'").fetchone()[0] == 0
    db.sync_words(conn, lex, "a-different-hash")
    assert conn.execute("SELECT COUNT(*) FROM words WHERE word = 'GEAR'").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM meta WHERE key = 'words_sha256'").fetchone()[0] == "a-different-hash"
    assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == len(lex.words)


def test_purge_events_retention_boundary(conn: sqlite3.Connection) -> None:
    cutoff = NOW - timedelta(days=90)
    for created in (cutoff - timedelta(days=1), cutoff, cutoff + timedelta(seconds=1), NOW):
        conn.execute(
            "INSERT INTO events (type, created_at) VALUES (?, ?)", ("tile_clicked", db.iso(created))
        )
    assert db.purge_events(conn, cutoff) == 2
    left = [r[0] for r in conn.execute("SELECT created_at FROM events ORDER BY created_at")]
    assert left == [db.iso(cutoff + timedelta(seconds=1)), db.iso(NOW)]
