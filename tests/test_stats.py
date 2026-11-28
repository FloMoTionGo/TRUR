"""A player's daily record: streaks, played, won, best score, longest word (tasks/todo.md B2)."""

import sqlite3
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeClock
from trur import db, stats

TODAY = date(2026, 10, 11)
ALICE, BOB = "alice-id", "bob-id"


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(db_path)
    for user in (ALICE, BOB):
        c.execute("INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES (?, ?, ?, ?, ?)",
                  (user, f"hash-{user}", user, user, "2026-01-01T00:00:00Z"))
    yield c
    c.close()


def add_day(conn: sqlite3.Connection, user: str, day: date, words: tuple[str, ...] = ("GEAR",), *,
            score: int = 4, won: bool = False, flagged: bool = False) -> str:
    """One daily game on `day` with these accepted words, a rejected one, and an event (for the purge)."""
    iso_day = day.isoformat()
    at = f"{iso_day}T12:00:00Z"
    game_id = f"{user}-{iso_day}"
    conn.execute("INSERT OR IGNORE INTO daily_challenges VALUES (?, 'GARDEN', 'SUMMER', 'workflow')", (iso_day,))
    conn.execute(
        "INSERT INTO games (id, user_id, mode, daily_date, word1, word2, total_words, started_at, ended_at,"
        " score, won, flagged) VALUES (?, ?, 'daily', ?, 'GARDEN', 'SUMMER', 9, ?, ?, ?, ?, ?)",
        (game_id, user, iso_day, at, at, score, int(won), int(flagged)),
    )
    rows = [(word, 1) for word in words] + [("ZZZZZZZZZZZZ", 0)]
    for word, valid in rows:
        conn.execute(
            "INSERT INTO submissions (user_id, game_id, game_mode, word, is_valid, points, submitted_at, month)"
            " VALUES (?, ?, 'daily', ?, ?, 0, ?, ?)",
            (user, game_id, word, valid, at, iso_day[:7]),
        )
    conn.execute("INSERT INTO events (user_id, game_id, type, created_at) VALUES (?, ?, 'submit', ?)", (user, game_id, at))
    return game_id


def of(conn: sqlite3.Connection, user: str = ALICE, today: date = TODAY) -> stats.Stats:
    return stats.daily_stats(conn, user, today)


# --- the streak ---

def test_a_new_player_has_an_empty_record(conn: sqlite3.Connection) -> None:
    got = of(conn)
    assert (got["played"], got["won"], got["current_streak"], got["best_streak"]) == (0, 0, 0, 0)
    assert got["best_score"] == 0 and got["longest_word"] is None
    assert len(got["days"]) == stats.RECENT_DAYS and not any(d["played"] or d["won"] for d in got["days"])


def test_days_in_a_row_up_to_today(conn: sqlite3.Connection) -> None:
    for back in (0, 1, 2):
        add_day(conn, ALICE, ago(back))
    assert of(conn)["current_streak"] == 3


def test_today_not_played_yet_leaves_yesterdays_streak_standing(conn: sqlite3.Connection) -> None:
    for back in (1, 2):
        add_day(conn, ALICE, ago(back))
    assert of(conn)["current_streak"] == 2


def test_a_skipped_day_resets_the_current_streak_and_keeps_the_best(conn: sqlite3.Connection) -> None:
    for back in (0, 2, 3, 4, 6):  # a run of three, then gaps on both sides
        add_day(conn, ALICE, ago(back))
    got = of(conn)
    assert (got["current_streak"], got["best_streak"], got["played"]) == (1, 3, 5)


def test_two_days_without_play_end_the_streak(conn: sqlite3.Connection) -> None:
    for back in (2, 3):
        add_day(conn, ALICE, ago(back))
    got = of(conn)
    assert (got["current_streak"], got["best_streak"]) == (0, 2)


def test_a_streak_runs_across_a_month_and_a_year_end(conn: sqlite3.Connection) -> None:
    for day in (date(2026, 12, 30), date(2026, 12, 31), date(2027, 1, 1)):
        add_day(conn, ALICE, day)
    assert of(conn, today=date(2027, 1, 1))["current_streak"] == 3


def test_a_started_daily_without_a_word_does_not_count(conn: sqlite3.Connection) -> None:
    """The streak rule: at least one word found. Opening the daily, or only wrong tries, isn't play."""
    add_day(conn, ALICE, ago(1))
    add_day(conn, ALICE, ago(0), words=())
    got = of(conn)
    assert (got["played"], got["current_streak"]) == (1, 1)  # yesterday's streak still stands today
    assert got["days"][-1] == {"date": TODAY.isoformat(), "played": False, "won": False}
    assert not stats.counts_as_played(0) and stats.counts_as_played(1)


def test_another_players_games_are_not_counted(conn: sqlite3.Connection) -> None:
    add_day(conn, ALICE, ago(0))
    for back in (0, 1, 2):
        add_day(conn, BOB, ago(back), words=("DEMOCRATIC",), score=25, won=True)
    got = of(conn)
    assert (got["played"], got["won"], got["current_streak"], got["best_score"], got["longest_word"]) == (1, 0, 1, 4, "GEAR")


def test_free_games_are_not_counted(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO games (id, user_id, mode, word1, word2, total_words, started_at, score)"
                 " VALUES ('free-1', ?, 'free', 'GARDEN', 'SUMMER', 9, '2026-10-11T10:00:00Z', 56)", (ALICE,))
    conn.execute("INSERT INTO submissions (user_id, game_id, game_mode, word, is_valid, points, submitted_at, month)"
                 " VALUES (?, 'free-1', 'free', 'DEARER', 1, 9, '2026-10-11T10:00:00Z', '2026-10')", (ALICE,))
    got = of(conn)
    assert (got["played"], got["best_score"], got["longest_word"]) == (0, 0, None)


# --- wins, best score, longest word ---

def test_wins_are_counted_apart_from_the_streak(conn: sqlite3.Connection) -> None:
    add_day(conn, ALICE, ago(2), words=("DEMOCRATIC",), score=25, won=True)
    add_day(conn, ALICE, ago(1))
    add_day(conn, ALICE, ago(0), words=("GEAR", "DEARER"), score=13)
    got = of(conn)
    assert (got["played"], got["won"], got["current_streak"], got["best_score"]) == (3, 1, 3, 25)
    assert got["longest_word"] == "DEMOCRATIC"


def test_the_longest_word_is_stable_among_equals(conn: sqlite3.Connection) -> None:
    add_day(conn, ALICE, ago(1), words=("NEARER", "GEAR"))
    add_day(conn, ALICE, ago(0), words=("DEARER",))
    assert of(conn)["longest_word"] == "DEARER"  # six letters twice: the first in the alphabet


def test_a_flagged_game_keeps_the_streak_and_leaves_the_score_and_words_out(conn: sqlite3.Connection) -> None:
    add_day(conn, ALICE, ago(1))
    add_day(conn, ALICE, ago(0), words=("DEMOCRATIC",), score=25, won=True, flagged=True)
    got = of(conn)
    assert (got["played"], got["won"], got["current_streak"]) == (2, 1, 2)
    assert (got["best_score"], got["longest_word"]) == (4, "GEAR")


def test_a_hidden_player_still_sees_their_own_record(conn: sqlite3.Connection) -> None:
    add_day(conn, ALICE, ago(0))
    conn.execute("UPDATE users SET hidden = 1 WHERE id = ?", (ALICE,))
    assert of(conn)["current_streak"] == 1


# --- the recent days ---

def test_recent_days_run_oldest_first_and_end_today(conn: sqlite3.Connection) -> None:
    add_day(conn, ALICE, ago(0), won=True)
    add_day(conn, ALICE, ago(29))
    add_day(conn, ALICE, ago(30))  # one day too old for the strip, still counted as played
    got = of(conn)
    days = got["days"]
    assert [days[0]["date"], days[-1]["date"]] == [ago(29).isoformat(), TODAY.isoformat()]
    assert days[0] == {"date": ago(29).isoformat(), "played": True, "won": False}
    assert days[-1] == {"date": TODAY.isoformat(), "played": True, "won": True}
    assert sum(d["played"] for d in days) == 2 and got["played"] == 3


# --- what the record rests on ---

def test_the_record_survives_the_event_purge(conn: sqlite3.Connection) -> None:
    """Only `events` is purged after 90 days; games and submissions stay, so old days keep counting."""
    for back in (200, 201, 202, 0):
        add_day(conn, ALICE, ago(back))
    before = of(conn)
    purged = db.purge_events(conn, datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert purged == 4 and conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert of(conn) == before
    assert (before["played"], before["best_streak"], before["current_streak"]) == (4, 3, 1)


def test_the_query_uses_the_one_game_per_day_index(conn: sqlite3.Connection) -> None:
    """No new index is needed: UNIQUE (user_id, daily_date) already serves the lookup."""
    plan = " ".join(r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + stats._GAMES, (ALICE,)))
    assert "USING" in plan and "INDEX" in plan and "SCAN g" not in plan, plan


def test_it_reads_through_a_read_only_connection(conn: sqlite3.Connection, db_path: Path) -> None:
    add_day(conn, ALICE, ago(0))
    ro = db.connect_ro(db_path)
    try:
        assert stats.daily_stats(ro, ALICE, TODAY)["current_streak"] == 1
    finally:
        ro.close()


# --- midnight, through the real game start ---

def test_games_started_either_side_of_midnight_utc_are_two_days(
        player: Callable[[str], TestClient], clock: FakeClock, db_path: Path) -> None:
    alice = player("Alice")
    conn = db.connect(db_path)
    try:
        for moment in (datetime(2026, 10, 10, 23, 59, tzinfo=timezone.utc), datetime(2026, 10, 11, 0, 1, tzinfo=timezone.utc)):
            clock.set(moment)
            r = alice.post("/api/games", json={"mode": "daily"})
            assert r.status_code in (200, 201), r.text
            game = conn.execute("SELECT id, user_id FROM games WHERE id = ?", (r.json()["game_id"],)).fetchone()
            conn.execute(
                "INSERT INTO submissions (user_id, game_id, game_mode, word, is_valid, points, submitted_at, month)"
                " VALUES (?, ?, 'daily', 'WORD', 1, 4, ?, '2026-10')",
                (game["user_id"], game["id"], db.iso(moment)),
            )
        user_id = conn.execute("SELECT id FROM users").fetchone()["id"]
        got = stats.daily_stats(conn, user_id, date(2026, 10, 11))
    finally:
        conn.close()
    assert (got["played"], got["current_streak"], got["best_streak"]) == (2, 2, 2)
    assert [d["date"] for d in got["days"] if d["played"]] == ["2026-10-10", "2026-10-11"]
