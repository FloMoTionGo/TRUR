"""The admin script: hide, unhide, flag, unflag and delete-player (TS §3 steps 6–7; S4, S13)."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts import admin

GAMES = "/api/games"
MONTHLY = "/api/rankings"
TABLES = ("users", "games", "submissions", "events", "rankings")

Player = Callable[[str], TestClient]


def run(db_path: Path, *args: str) -> int:
    return admin.main(["--db", str(db_path), *args])


def rows(db_path: Path, sql: str, *args: object) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def finish(c: TestClient, db_path: Path, score: int, mode: str = "free") -> str:
    """Start a game, submit one word (valid or not, it leaves a row), set the score, end it."""
    game_id = c.post(GAMES, json={"mode": mode}).json()["game_id"]
    assert c.post(f"{GAMES}/{game_id}/submit", json={"tiles": [[0, 0], [0, 1]]}).status_code == 200
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE games SET score = ? WHERE id = ?", (score, game_id))
    conn.commit()
    conn.close()
    assert c.post(f"{GAMES}/{game_id}/end", json={}).status_code == 200
    return game_id


def board(c: TestClient, mode: str = "free") -> list[tuple]:
    r = c.get(MONTHLY, params={"mode": mode})
    assert r.status_code == 200, r.text
    return [(e["rank"], e["nickname"], e["score"]) for e in r.json()["entries"]]


def test_flag_and_unflag_take_effect_on_the_next_board_request(
    player: Player, client: TestClient, db_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    alice = player("Alice")
    best = finish(alice, db_path, 50)
    finish(alice, db_path, 30)
    assert run(db_path, "flag", best) == 0
    assert board(client) == [(1, "Alice", 30)]
    assert run(db_path, "unflag", best) == 0
    assert board(client) == [(1, "Alice", 50)]
    assert "unflagged" in capsys.readouterr().out


def test_flagging_a_daily_game_updates_both_of_its_boards(player: Player, client: TestClient, db_path: Path) -> None:
    game_id = finish(player("Alice"), db_path, 12, mode="daily")
    assert run(db_path, "flag", game_id) == 0
    assert board(client, "daily") == []
    assert client.get("/api/rankings/daily").json()["entries"] == []


def test_an_open_game_cant_be_flagged(player: Player, db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """_end sets the flag from the plausibility check, which would overwrite a manual one."""
    game_id = player("Alice").post(GAMES, json={"mode": "free"}).json()["game_id"]
    assert run(db_path, "flag", game_id) == 1
    assert "still open" in capsys.readouterr().err
    assert rows(db_path, "SELECT flagged FROM games") == [(0,)]


def test_hide_and_unhide(player: Player, client: TestClient, db_path: Path) -> None:
    for name, score in (("Alice", 20), ("Bob", 10)):
        finish(player(name), db_path, score)
    assert run(db_path, "hide", "ALICE") == 0  # found by the folded nickname, like registration
    assert board(client) == [(1, "Bob", 10)]
    assert run(db_path, "unhide", "Alice") == 0
    assert board(client) == [(1, "Alice", 20), (2, "Bob", 10)]


def test_delete_player_removes_only_that_players_rows(player: Player, client: TestClient, db_path: Path) -> None:
    alice, bob = player("Alice"), player("Bob")
    for c, score in ((alice, 20), (bob, 10)):
        game_id = finish(c, db_path, score)
        finish(c, db_path, score, mode="daily")
        event = {"type": "selection_cleared", "game_id": game_id, "client_ms": 0}
        assert c.post("/api/events", json={"events": [event]}).status_code == 204
    (bob_id,) = rows(db_path, "SELECT id FROM users WHERE nickname = 'Bob'")[0]
    (alice_id,) = rows(db_path, "SELECT id FROM users WHERE nickname = 'Alice'")[0]
    bobs = {t: rows(db_path, f'SELECT * FROM "{t}" WHERE {"id" if t == "users" else "user_id"} = ?', bob_id) for t in TABLES}
    assert all(bobs.values())

    assert run(db_path, "delete-player", "alice") == 0

    for t in TABLES:
        key = "id" if t == "users" else "user_id"
        assert rows(db_path, f'SELECT COUNT(*) FROM "{t}" WHERE {key} = ?', alice_id) == [(0,)], t
        assert rows(db_path, f'SELECT * FROM "{t}" WHERE {key} = ?', bob_id) == bobs[t], t
    assert alice.get("/api/users/me").status_code == 401
    assert board(client) == [(1, "Bob", 10)]
    assert rows(db_path, "PRAGMA foreign_key_check") == []


@pytest.mark.parametrize("args", [
    ("hide", "Nobody"),
    ("unhide", "Nobody"),
    ("delete-player", "Nobody"),
    ("flag", "00000000-0000-4000-8000-000000000000"),
    ("unflag", "' OR 1=1--"),
])
def test_unknown_targets_change_nothing(
    player: Player, db_path: Path, capsys: pytest.CaptureFixture[str], args: tuple[str, str],
) -> None:
    finish(player("Alice"), db_path, 5)
    before = {t: rows(db_path, f'SELECT * FROM "{t}"') for t in TABLES}
    assert run(db_path, *args) == 1
    assert "no such" in capsys.readouterr().err
    assert {t: rows(db_path, f'SELECT * FROM "{t}"') for t in TABLES} == before


def test_a_missing_database_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path / "nope.db", "hide", "Alice") == 1
    assert "no database" in capsys.readouterr().err
    assert not (tmp_path / "nope.db").exists()
