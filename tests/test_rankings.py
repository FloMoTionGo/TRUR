"""Free-play limits and rankings (TS §3 step 6; S6)."""

import json
import random
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeClock, make_client, make_settings
from trur import db, rankings
from trur.app import create_app
from trur.config import Settings
from trur.games import is_implausible

GAMES = "/api/games"
CURRENT = "/api/games/current?mode=free"
PER_DAY = 20  # FREE_GAMES_PER_DAY in the test settings (docs/architecture.md §5)
MONTHLY = "/api/rankings"
DAILY = "/api/rankings/daily"

Player = Callable[[str], TestClient]


def start(c: TestClient, mode: str = "free") -> dict:
    r = c.post(GAMES, json={"mode": mode})
    assert r.status_code == 201, r.text
    return r.json()


def assert_limited(c: TestClient) -> None:
    r = c.post(GAMES, json={"mode": "free"})
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "limit_reached"


# --- free-play limits ---

def test_twenty_free_starts_succeed_and_the_21st_leaves_the_open_game_alone(player: Player, db_path: Path) -> None:
    alice = player("Alice")
    last = [start(alice) for _ in range(PER_DAY)][-1]
    assert_limited(alice)
    assert alice.get(CURRENT).json()["game"]["game_id"] == last["game_id"]
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone() == (PER_DAY,)
        assert conn.execute("SELECT ended_at FROM games WHERE id = ?", (last["game_id"],)).fetchone() == (None,)
    finally:
        conn.close()


def test_the_limit_resets_at_midnight_utc(player: Player, clock: FakeClock) -> None:
    alice = player("Alice")
    for _ in range(PER_DAY):
        start(alice)
    clock.set(datetime(2026, 10, 11, 23, 59, 59, 999999, tzinfo=timezone.utc))
    assert_limited(alice)
    clock.set(datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc))
    start(alice)


def test_games_ended_by_the_player_still_count(player: Player) -> None:
    alice = player("Alice")
    for _ in range(PER_DAY):
        game = start(alice)
        assert alice.post(f"{GAMES}/{game['game_id']}/end", json={}).status_code == 200
    assert_limited(alice)


def test_daily_games_do_not_count(player: Player) -> None:
    alice = player("Alice")
    start(alice, "daily")
    for _ in range(PER_DAY):
        start(alice)
    assert_limited(alice)
    assert alice.post(GAMES, json={"mode": "daily"}).status_code == 200  # the limit doesn't block the daily game


def test_the_limit_is_per_player(player: Player) -> None:
    alice, bob = player("Alice"), player("Bob")
    for _ in range(PER_DAY):
        start(alice)
    assert_limited(alice)
    start(bob)


def test_flagged_games_still_count_toward_the_limit(player: Player, db_path: Path) -> None:
    alice = player("Alice")
    for _ in range(PER_DAY):
        start(alice)
    execute(db_path, "UPDATE games SET flagged = 1")
    assert_limited(alice)


# --- helpers for the boards ---

def execute(db_path: Path, sql: str, *args: object) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def rows(db_path: Path, sql: str, *args: object) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def finish(c: TestClient, db_path: Path, score: int, mode: str = "free") -> str:
    """Start a game, give it `score` points directly in the database, then end it through the API."""
    r = c.post(GAMES, json={"mode": mode})
    assert r.status_code in (200, 201), r.text
    game_id = r.json()["game_id"]
    execute(db_path, "UPDATE games SET score = ? WHERE id = ?", score, game_id)
    assert c.post(f"{GAMES}/{game_id}/end", json={}).status_code == 200
    return game_id


def board(c: TestClient, path: str = MONTHLY, **params: str) -> list[tuple]:
    r = c.get(path, params={"mode": "free", **params} if path == MONTHLY else params)
    assert r.status_code == 200, r.text
    return [(e["rank"], e["nickname"], e["score"]) for e in r.json()["entries"]]


def recompute_all(db_path: Path) -> None:
    """What `scripts.admin flag|unflag` does after changing a flag (T27): rebuild the affected rankings."""
    conn = db.connect(db_path)
    try:
        with db.tx(conn):
            for game in conn.execute("SELECT * FROM games WHERE ended_at IS NOT NULL").fetchall():
                for period in rankings.periods(game):
                    rankings.recompute_ranking(conn, game["user_id"], game["mode"], period)
    finally:
        conn.close()


# --- boards ---

def test_monthly_board_keeps_each_players_best_game(
    player: Player, client: TestClient, db_path: Path, clock: FakeClock,
) -> None:
    alice = player("Alice")
    for score in (10, 30, 20):
        finish(alice, db_path, score)
    assert board(client) == [(1, "Alice", 30)]
    clock.set(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc))
    finish(alice, db_path, 50)
    assert board(client, month="2026-10") == [(1, "Alice", 30)]
    assert board(client) == [(1, "Alice", 50)]  # month defaults to the current UTC month


def test_board_response_shape(player: Player, client: TestClient, db_path: Path) -> None:
    finish(player("Alice"), db_path, 12)
    monthly = client.get(MONTHLY, params={"mode": "free"}).json()
    assert monthly == {"mode": "free", "month": "2026-10", "entries": [{"rank": 1, "nickname": "Alice", "score": 12}]}
    assert client.get(DAILY).json() == {"date": "2026-10-11", "entries": []}  # date defaults to today (UTC)


def test_a_game_from_the_last_seconds_of_a_month_lands_on_that_month(
    player: Player, client: TestClient, db_path: Path, clock: FakeClock,
) -> None:
    alice = player("Alice")
    clock.set(datetime(2026, 10, 31, 23, 59, 50, tzinfo=timezone.utc))
    game_id = start(alice)["game_id"]
    execute(db_path, "UPDATE games SET score = 7 WHERE id = ?", game_id)
    clock.set(datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc))
    assert board(client) == []  # the sweep before the board closes it, on October's board
    assert board(client, month="2026-10") == [(1, "Alice", 7)]


def test_daily_board_has_one_row_per_player_ordered_by_points(
    player: Player, client: TestClient, db_path: Path,
) -> None:
    for name, score in (("Alice", 8), ("Bob", 21), ("Carol", 13)):
        finish(player(name), db_path, score, mode="daily")
    expected = [(1, "Bob", 21), (2, "Carol", 13), (3, "Alice", 8)]
    assert board(client, DAILY) == expected
    assert board(client, DAILY, date="2026-10-11") == expected
    assert board(client, mode="daily") == expected  # the monthly daily board
    assert board(client) == []  # and nothing on free play's


def test_ties_share_a_rank_and_are_ordered_by_nickname(player: Player, client: TestClient, db_path: Path) -> None:
    for name, score in (("bob", 30), ("Carol", 10), ("Alice", 30)):
        finish(player(name), db_path, score)
    assert board(client) == [(1, "Alice", 30), (1, "bob", 30), (3, "Carol", 10)]


def test_board_shows_the_top_board_limit(db_path: Path, clock: FakeClock, garden_seed: int) -> None:
    app = create_app(make_settings(db_path.parent, board_limit=2), clock, random.Random(garden_seed))
    with make_client(app) as anon:
        for name, score in (("Alice", 5), ("Bob", 9), ("Carol", 7)):
            with make_client(app) as c:
                assert c.post("/api/users", json={"nickname": name}).status_code == 201
                finish(c, db_path, score)
        assert board(anon) == [(1, "Bob", 9), (2, "Carol", 7)]


def test_flagged_games_stay_off_the_boards_until_unflagged(player: Player, client: TestClient, db_path: Path) -> None:
    alice = player("Alice")
    flagged = finish(alice, db_path, 50)
    finish(alice, db_path, 30)
    execute(db_path, "UPDATE games SET flagged = 1 WHERE id = ?", flagged)
    recompute_all(db_path)
    assert board(client) == [(1, "Alice", 30)]
    execute(db_path, "UPDATE games SET flagged = 0 WHERE id = ?", flagged)
    recompute_all(db_path)
    assert board(client) == [(1, "Alice", 50)]


def test_a_player_whose_only_game_is_flagged_has_no_row(player: Player, client: TestClient, db_path: Path) -> None:
    game_id = finish(player("Alice"), db_path, 40)
    execute(db_path, "UPDATE games SET flagged = 1 WHERE id = ?", game_id)
    recompute_all(db_path)
    assert board(client) == []
    assert rows(db_path, "SELECT COUNT(*) FROM rankings") == [(0,)]


def test_hidden_players_are_absent_from_all_three_boards(player: Player, client: TestClient, db_path: Path) -> None:
    alice, bob = player("Alice"), player("Bob")
    for c, score in ((alice, 20), (bob, 10)):
        finish(c, db_path, score)
        finish(c, db_path, score, mode="daily")
    execute(db_path, "UPDATE users SET hidden = 1 WHERE nickname = 'Alice'")
    only_bob = [(1, "Bob", 10)]  # Bob moves up to first place
    assert board(client) == board(client, mode="daily") == board(client, DAILY) == only_bob
    execute(db_path, "UPDATE users SET hidden = 0 WHERE nickname = 'Alice'")
    both = [(1, "Alice", 20), (2, "Bob", 10)]
    assert board(client) == board(client, mode="daily") == board(client, DAILY) == both


def test_a_replaced_game_is_ranked(player: Player, client: TestClient, db_path: Path) -> None:
    alice = player("Alice")
    a = start(alice)["game_id"]
    execute(db_path, "UPDATE games SET score = 4 WHERE id = ?", a)
    start(alice)
    assert board(client) == [(1, "Alice", 4)]


def test_the_board_sweeps_yesterdays_open_games_first(
    player: Player, client: TestClient, db_path: Path, clock: FakeClock,
) -> None:
    alice = player("Alice")
    for mode in ("free", "daily"):
        game_id = start(alice, mode)["game_id"]
        execute(db_path, "UPDATE games SET score = 6 WHERE id = ?", game_id)
    clock.set(datetime(2026, 10, 12, 0, 0, 1, tzinfo=timezone.utc))
    assert board(client, DAILY, date="2026-10-11") == [(1, "Alice", 6)]
    assert rows(db_path, "SELECT mode, ended_at FROM games ORDER BY mode") == [
        ("daily", "2026-10-12T00:00:01.000000Z"), ("free", "2026-10-12T00:00:01.000000Z"),
    ]
    assert board(client, month="2026-10") == [(1, "Alice", 6)]


@pytest.mark.parametrize(("path", "params"), [
    (MONTHLY, {"mode": "x"}),
    (MONTHLY, {}),
    (MONTHLY, {"mode": "free", "month": "2026-13"}),
    (MONTHLY, {"mode": "free", "month": "2026-1"}),
    (MONTHLY, {"mode": "free", "month": "2026-10-01"}),
    (DAILY, {"date": "yesterday"}),
    (DAILY, {"date": "2026-02-30"}),
])
def test_bad_board_input_is_422(client: TestClient, path: str, params: dict) -> None:
    r = client.get(path, params=params)
    assert r.status_code == 422, r.text


def test_boards_need_no_cookie_and_set_none(client: TestClient) -> None:
    r = client.get(MONTHLY, params={"mode": "free"})
    assert r.status_code == 200 and "set-cookie" not in r.headers


def test_board_queries_run_on_a_read_only_connection(player: Player, db_path: Path) -> None:
    finish(player("Alice"), db_path, 3)
    conn = db.connect_ro(db_path)
    try:
        assert rankings.monthly_board(conn, "free", "2026-10", 100) == [{"rank": 1, "nickname": "Alice", "score": 3}]
        assert rankings.daily_board(conn, date(2026, 10, 11), 100) == []
    finally:
        conn.close()


def test_periods() -> None:
    free = {"mode": "free", "daily_date": None, "started_at": "2026-10-31T23:59:50.000000Z"}
    daily = {"mode": "daily", "daily_date": "2026-11-01", "started_at": "2026-11-01T08:00:00.000000Z"}
    assert rankings.periods(free) == ["2026-10"]
    assert rankings.periods(daily) == ["2026-11", "2026-11-01"]


# --- plausibility check (docs/architecture.md §8 item 9) ---

S = Settings()  # PLAUS_MAX_WPM 20, PLAUS_SOFT_WPM 8, PLAUS_MIN_CELLS 49, PLAUS_MIN_WORDS 5, PLAUS_FOUND_RATIO 0.9


@pytest.mark.parametrize(("found", "total", "cells", "minutes", "flagged"), [
    (9, 10, 49, 1.0, True),      # fast (9 a minute), 90 % found, on a grid at the minimum size
    (9, 10, 36, 1.0, False),     # the same speed on a small grid
    (10, 10, 100, 10.0, False),  # every word found, but slowly
    (8, 10, 49, 1.0, False),     # fast, but only 80 % found
    (21, 200, 100, 1.0, True),   # over PLAUS_MAX_WPM with enough words, whatever the grid
    (4, 4, 16, 0.1, False),      # too few words for the speed rule
    (5, 100, 16, 0.2, False),    # 5 words in 12 s: 10 a minute after the 0.5-minute floor
    (11, 100, 16, 0.5, True),    # 22 a minute
    (0, 30, 49, 0.0, False),     # nothing found
])
def test_is_implausible(found: int, total: int, cells: int, minutes: float, flagged: bool) -> None:
    assert is_implausible(found, total, cells, minutes, S) is flagged


GEAR, DEER = [[4, 0], [5, 1]], [[4, 3], [5, 4]]  # GARDEN × SUMMER


@pytest.mark.parametrize("plaus_max_wpm", [1.0, 20.0])
def test_a_game_is_flagged_when_it_ends(db_path: Path, clock: FakeClock, garden_seed: int, plaus_max_wpm: float) -> None:
    """Two words in 30 s is 4 a minute: over a limit of 1, under the default 20."""
    s = make_settings(db_path.parent, plaus_max_wpm=plaus_max_wpm, plaus_min_words=2)
    app = create_app(s, clock, random.Random(garden_seed))
    with make_client(app) as c:
        assert c.post("/api/users", json={"nickname": "Alice"}).status_code == 201
        game_id = start(c)["game_id"]
        for tiles in (GEAR, DEER):
            clock.advance(15)
            assert c.post(f"{GAMES}/{game_id}/submit", json={"tiles": tiles}).json()["status"] == "accepted"
        clock.advance(600)  # time after the last valid word doesn't count
        assert c.post(f"{GAMES}/{game_id}/end", json={}).status_code == 200
        flagged = plaus_max_wpm == 1.0
        assert rows(db_path, "SELECT flagged FROM games") == [(int(flagged),)]
        ((payload,),) = rows(db_path, "SELECT payload FROM events WHERE type = 'game_ended'")
        assert json.loads(payload) == {"reason": "player", "score": 8, "flagged": flagged}
        assert board(c) == ([] if flagged else [(1, "Alice", 8)])
