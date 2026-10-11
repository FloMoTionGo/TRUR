"""Game flow: creating, resuming, submitting and ending (TS §3 step 3; S5)."""

import random
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FakeClock, db_snapshot, make_client, make_settings
from trur import games
from trur.app import create_app
from trur.config import Settings
from trur.solver import Lexicon

GAMES = "/api/games"
CURRENT = "/api/games/current?mode=free"
GARDEN_WORDS = ("AMAM", "DEARER", "DEER", "GEAR", "GENRES", "NEAR", "NEARER", "RERUNS", "RUNE")
STATE_KEYS = {
    "game_id", "mode", "daily_date", "word1", "word2", "tries_left", "total_words",
    "found", "found_count", "score", "won", "game_over", "missed_words",
}

Player = Callable[[str], TestClient]


def rows(db_path: Path, sql: str, *args: object) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


@pytest.fixture
def alice(player: Player) -> TestClient:
    return player("Alice")


def start(c: TestClient) -> dict:
    r = c.post(GAMES, json={"mode": "free"})
    assert r.status_code == 201, r.text
    return r.json()


# --- creating a game ---

def test_create_free_game(alice: TestClient) -> None:
    r = alice.post(GAMES, json={"mode": "free"})
    assert r.status_code == 201
    game = r.json()
    assert set(game) == STATE_KEYS
    assert uuid.UUID(game["game_id"]).version == 4
    assert game == {
        "game_id": game["game_id"], "mode": "free", "daily_date": None, "word1": "GARDEN", "word2": "SUMMER",
        "tries_left": 3, "total_words": 9, "found": [], "found_count": 0, "score": 0, "won": False,
        "game_over": False, "missed_words": None,
    }
    assert not any(word in r.text for word in GARDEN_WORDS)  # no answers in the body (S12)


def test_create_stores_the_game(alice: TestClient, db_path: Path) -> None:
    game = start(alice)
    assert rows(db_path, "SELECT mode, daily_date, word1, word2, total_words, started_at, ended_at, wrong_count, score FROM games WHERE id = ?", game["game_id"]) == [
        ("free", None, "GARDEN", "SUMMER", 9, "2026-10-11T12:00:00.000000Z", None, 0, 0)
    ]


def test_create_needs_a_player(client: TestClient, db_path: Path) -> None:
    r = client.post(GAMES, json={"mode": "free"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "no_player"
    assert db_snapshot(db_path)[1]["games"] == 0


@pytest.mark.parametrize(
    "body",
    [{}, {"mode": "x"}, {"mode": "daily"}, {"mode": 1}, {"mode": "free", "score": 50}, {"mode": "free", "won": True},
     {"mode": "free", "date": "2026-10-10"}, {"mode": "free", "user_id": "x"}],
)
def test_create_refuses_bad_bodies(alice: TestClient, db_path: Path, body: dict) -> None:
    r = alice.post(GAMES, json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert db_snapshot(db_path)[1]["games"] == 0


def test_daily_is_not_built_yet(lex: Lexicon, settings: Settings) -> None:
    with pytest.raises(NotImplementedError):
        games.create_game(None, lex, settings, random.Random(0), "u", "daily", None)  # type: ignore[arg-type]


def test_no_pair_available_is_503(db_path: Path, clock: FakeClock, garden_seed: int) -> None:
    app = create_app(make_settings(db_path.parent, min_free_words=10), clock, random.Random(garden_seed))
    with make_client(app) as c:
        assert c.post("/api/users", json={"nickname": "Alice"}).status_code == 201
        r = c.post(GAMES, json={"mode": "free"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_pair_available"
    assert db_snapshot(db_path)[1]["games"] == 0


def test_free_games_respect_min_free_words(db_path: Path, clock: FakeClock, lex: Lexicon) -> None:
    app = create_app(make_settings(db_path.parent, min_free_words=9), clock, random.Random(1))
    with make_client(app) as c:
        c.post("/api/users", json={"nickname": "Alice"})
        for _ in range(5):
            game = c.post(GAMES, json={"mode": "free"}).json()
            assert game["total_words"] == len(lex.solve(game["word1"], game["word2"])) >= 9


# --- resuming ---

def test_current_resumes_the_open_game(alice: TestClient) -> None:
    game = start(alice)
    r = alice.get(CURRENT)
    assert r.status_code == 200 and r.json() == {"game": game}


def test_current_without_an_open_game_is_null(alice: TestClient) -> None:
    assert alice.get(CURRENT).json() == {"game": None}


def test_current_is_per_player(alice: TestClient, player: Player) -> None:
    start(alice)
    assert player("Bob").get(CURRENT).json() == {"game": None}


def test_current_needs_a_player(client: TestClient) -> None:
    assert client.get(CURRENT).status_code == 401


@pytest.mark.parametrize("query", ["", "?mode=x", "?mode=daily", "?mode=' OR 1=1--"])
def test_current_needs_a_valid_mode(alice: TestClient, query: str) -> None:
    r = alice.get("/api/games/current" + query)
    assert r.status_code == 422 and r.json()["error"] == {
        "code": "invalid_request", "message": "The request is not valid.", "fields": ["mode"]
    }


def test_total_words_comes_from_the_games_row(alice: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    start(alice)
    monkeypatch.setattr(app.state.lexicon, "solve", lambda w1, w2: ())
    assert alice.get(CURRENT).json()["game"]["total_words"] == 9


# --- one open free game ---

def test_starting_b_ends_a_as_replaced(alice: TestClient, db_path: Path, clock: FakeClock) -> None:
    a = start(alice)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE games SET score = 4 WHERE id = ?", (a["game_id"],))
    conn.commit()
    conn.close()
    clock.advance(60)
    b = start(alice)
    assert b["game_id"] != a["game_id"]
    assert rows(db_path, "SELECT ended_at, score FROM games WHERE id = ?", a["game_id"]) == [("2026-10-11T12:01:00.000000Z", 4)]
    assert rows(db_path, "SELECT COUNT(*) FROM games WHERE ended_at IS NULL") == [(1,)]
    assert alice.get(CURRENT).json()["game"]["game_id"] == b["game_id"]
