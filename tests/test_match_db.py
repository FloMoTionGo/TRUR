"""Step M3: migration 2, save_match and match_results (docs/test-strategy.md §6, docs/multiplayer.md §6.6)."""

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import admin
from tests.conftest import db_snapshot
from tests.test_match import tiles
from trur import db, match

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
NICKS = {"uA": "Alice", "uB": "Bob", "uC": "Cleo"}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def conn(db_path: Path):
    c = db.connect(db_path)
    for uid, nick in NICKS.items():
        c.execute(
            "INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, "hash-" + uid, nick, nick.lower(), db.iso(T0)),
        )
    yield c
    c.close()


def played() -> match.MatchRecord:
    """The worked results example plus a duplicate, a rejected word and a second display word for A."""
    m = match.new_match(
        "m1", "GARDEN", "SUMMER", ("AMAM", "DEARER", "DEER", "GEAR", "GENRES", "NEAR", "NEARER", "RERUNS", "RUNE"),
        [(uid, nick, nick.lower()) for uid, nick in NICKS.items()], T0,
    )
    match.submit(m, "uA", tiles(m, "GEAR"), at(1))  # accepted, 4
    match.submit(m, "uA", tiles(m, "GEAR"), at(2))  # duplicate
    match.submit(m, "uC", tiles(m, "RUAR"), at(3))  # rejected
    match.submit(m, "uB", tiles(m, "NEARER"), at(4))  # accepted, 8
    match.submit(m, "uC", tiles(m, "DEER"), at(5))  # accepted, 3
    match.display(m, "uA", tiles(m, "DEER"), at(6))
    match.display(m, "uA", tiles(m, "RUNS"), at(7))  # the last one counts
    match.display(m, "uB", [(0, 0), (1, 3)], at(8))  # GSDU
    assert match.close_play(m, at(60), random.Random(0))
    cid = {uid: c for c, uid in m.candidates.items()}
    match.vote(m, "uB", [cid["uA"]], at(61))
    match.vote(m, "uC", [cid["uA"], cid["uB"]], at(62))
    match.vote(m, "uA", [cid["uB"]], at(63))
    return match.finish(m, at(63))


def rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(sql)]


# --- migration 2 ---

def test_migration_2_applies_on_a_version_1_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixture DB built at user_version 1, never a copy of production (S13)."""
    path = tmp_path / "v1.db"
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:1])
    db.migrate(path)
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES ('u1','h','N','n','t')")
    raw.execute("INSERT INTO events (user_id, type, created_at) VALUES ('u1', 'game_started', 't')")
    raw.commit()
    raw.close()
    monkeypatch.undo()

    db.migrate(path)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        assert raw.execute("SELECT user_id, type, match_id FROM events").fetchall() == [("u1", "game_started", None)]
        new = ("matches", "match_players", "match_votes", "match_submissions")
        sql = dict(raw.execute(f"SELECT name, sql FROM sqlite_master WHERE name IN {new}"))
        assert set(sql) == set(new) and all("STRICT" in s for s in sql.values())
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        raw.close()


# --- saving ---

def test_snapshot_equals_the_in_memory_results(conn: sqlite3.Connection) -> None:
    rec = played()
    db.save_match(conn, rec)
    assert db.match_results(conn, "m1", "uA") == match.results(rec)
    stored = conn.execute("SELECT results FROM matches").fetchone()[0]
    assert not any(nick in stored for nick in NICKS.values())  # user_ids only (S13)


def test_match_row(conn: sqlite3.Connection) -> None:
    db.save_match(conn, played())
    assert rows(conn, "SELECT id, word1, word2, total_words, started_at, ended_at, month, player_count FROM matches") == [
        ("m1", "GARDEN", "SUMMER", 9, db.iso(T0), db.iso(at(63)), "2026-10", 3),
    ]


def test_submission_rows_skip_duplicates_and_keep_every_display_word(conn: sqlite3.Connection) -> None:
    db.save_match(conn, played())
    got = rows(conn, "SELECT user_id, word, tiles, kind, is_valid, points, submitted_at, month "
                     "FROM match_submissions ORDER BY id")
    assert [g[:2] + g[3:6] for g in got] == [
        ("uA", "GEAR", "word", 1, 4),
        ("uC", "RUAR", "word", 0, 0),
        ("uB", "NEARER", "word", 1, 8),
        ("uC", "DEER", "word", 1, 3),
        ("uA", "DEER", "display", 0, 0),
        ("uA", "RUNS", "display", 0, 0),
        ("uB", "GSDU", "display", 0, 0),
    ]
    assert got[-1][2] == "[[0,0],[1,3]]"
    assert [g[6] for g in got] == [db.iso(at(s)) for s in (1, 3, 4, 5, 6, 7, 8)]
    assert {g[7] for g in got} == {"2026-10"}


def test_player_and_vote_rows(conn: sqlite3.Connection) -> None:
    db.save_match(conn, played())
    players = rows(conn, "SELECT user_id, score, word_score, vote_bonus, display_word, place, words_found "
                         "FROM match_players ORDER BY place")
    assert players == [
        ("uB", 16, 8, 8, "GSDU", 1, 1),  # received 3 + 5 from two ballots
        ("uA", 14, 4, 10, "RUNS", 2, 1),  # received 5 + 5; the last display word
        ("uC", 3, 3, 0, None, 3, 1),
    ]
    assert all(score == word + bonus for _, score, word, bonus, *_ in players)
    assert sorted(rows(conn, "SELECT voter_id, target_id, points FROM match_votes")) == [
        ("uA", "uB", 5), ("uB", "uA", 5), ("uC", "uA", 5), ("uC", "uB", 3),
    ]


@pytest.mark.parametrize("voter, target", [("uA", "uA"), ("uC", "uA")], ids=["self-vote", "second row"])
def test_vote_constraints(conn: sqlite3.Connection, voter: str, target: str) -> None:
    db.save_match(conn, played())
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO match_votes VALUES ('m1', ?, ?, 1)", (voter, target))


def test_a_valid_word_is_stored_once_per_player(conn: sqlite3.Connection) -> None:
    db.save_match(conn, played())
    conn.execute("INSERT INTO match_submissions (match_id, user_id, word, tiles, is_valid, submitted_at, month) "
                 "VALUES ('m1', 'uB', 'GEAR', '[]', 1, 't', '2026-10')")  # another player may find it
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO match_submissions (match_id, user_id, word, tiles, is_valid, submitted_at, month) "
                     "VALUES ('m1', 'uA', 'GEAR', '[]', 1, 't', '2026-10')")


def test_events_with_their_real_timestamps(conn: sqlite3.Connection) -> None:
    db.save_match(conn, played())
    got = rows(conn, "SELECT user_id, type, payload, created_at FROM events WHERE match_id = 'm1' ORDER BY id")
    start = [(u, "match_started", '{"player_count":3}', db.iso(T0)) for u in ("uA", "uB", "uC")]
    assert got[:3] == start
    assert got[3:-3] == [
        ("uA", "match_word_accepted", '{"word":"GEAR","points":4}', db.iso(at(1))),
        ("uA", "match_word_duplicate", '{"word":"GEAR","points":0}', db.iso(at(2))),
        ("uC", "match_word_rejected", '{"word":"RUAR","points":0}', db.iso(at(3))),
        ("uB", "match_word_accepted", '{"word":"NEARER","points":8}', db.iso(at(4))),
        ("uC", "match_word_accepted", '{"word":"DEER","points":3}', db.iso(at(5))),
        ("uA", "match_display_word", '{"word":"DEER"}', db.iso(at(6))),
        ("uA", "match_display_word", '{"word":"RUNS"}', db.iso(at(7))),
        ("uB", "match_display_word", '{"word":"GSDU"}', db.iso(at(8))),
        ("uB", "match_vote_cast", '{"ranked":1}', db.iso(at(61))),
        ("uC", "match_vote_cast", '{"ranked":2}', db.iso(at(62))),
        ("uA", "match_vote_cast", '{"ranked":1}', db.iso(at(63))),
    ]
    assert [(u, t, json.loads(p), c) for u, t, p, c in got[-3:]] == [
        ("uB", "match_ended", {"place": 1, "score": 16}, db.iso(at(63))),
        ("uA", "match_ended", {"place": 2, "score": 14}, db.iso(at(63))),
        ("uC", "match_ended", {"place": 3, "score": 3}, db.iso(at(63))),
    ]


def test_a_failed_save_writes_nothing(conn: sqlite3.Connection, db_path: Path) -> None:
    rec = played()
    db.save_match(conn, rec)
    before = db_snapshot(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.save_match(conn, rec)  # same match id
    assert db_snapshot(db_path) == before
    assert not conn.in_transaction


# --- deleted players ---

def test_a_player_deleted_during_the_match(conn: sqlite3.Connection) -> None:
    rec = played()
    conn.execute("DELETE FROM users WHERE id = 'uB'")
    db.save_match(conn, rec)
    for table, col in (("match_players", "user_id"), ("match_submissions", "user_id"), ("events", "user_id")):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = 'uB'").fetchone()[0] == 0
    assert rows(conn, "SELECT voter_id, target_id, points FROM match_votes") == [("uC", "uA", 5)]
    res = db.match_results(conn, "m1", "uA")
    assert res is not None
    expected = match.results(rec)
    for p in expected["players"]:
        if p["user_id"] == "uB":
            p["nickname"] = db.DELETED_PLAYER
    assert res == expected  # bonuses as computed, B shown as "deleted player"
    assert db.match_results(conn, "m1", "uB") is None


def test_delete_player_removes_their_match_rows(conn: sqlite3.Connection, db_path: Path) -> None:
    db.save_match(conn, played())
    admin.delete_player(conn, "Bob")
    for table, cols in (
        ("match_players", ("user_id",)),
        ("match_submissions", ("user_id",)),
        ("match_votes", ("voter_id", "target_id")),
        ("events", ("user_id",)),
    ):
        for col in cols:
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = 'uB'").fetchone()[0] == 0
    raw = sqlite3.connect(db_path)  # iterdump needs PRAGMAs the request authorizer refuses
    dump = "\n".join(raw.iterdump())
    raw.close()
    assert "Bob" not in dump
    res = db.match_results(conn, "m1", "uA")
    assert res is not None
    assert [p["nickname"] for p in res["players"]] == ["deleted player", "Alice", "Cleo"]


# --- reading ---

@pytest.mark.parametrize(
    "match_id, user_id",
    [
        ("m1", "uZ"),  # not a player
        ("m2", "uA"),  # no such match
        ("m1' OR '1'='1", "uA"),
        ("m1", "uZ' OR '1'='1"),
        ("m1; DROP TABLE matches; --", "uA"),
    ],
)
def test_match_results_only_for_players(conn: sqlite3.Connection, db_path: Path, match_id: str, user_id: str) -> None:
    db.save_match(conn, played())
    before = db_snapshot(db_path)
    assert db.match_results(conn, match_id, user_id) is None
    assert db_snapshot(db_path) == before
