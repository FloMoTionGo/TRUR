"""Free-play limits and rankings (TS §3 step 6; S6)."""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import FakeClock

GAMES = "/api/games"
CURRENT = "/api/games/current?mode=free"
PER_DAY = 20  # FREE_GAMES_PER_DAY in the test settings (docs/architecture.md §5)

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
