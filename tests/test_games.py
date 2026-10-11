"""Game flow: creating, resuming, submitting and ending (TS §3 step 3; S5)."""

import asyncio
import random
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FakeClock, LiveServer, db_snapshot, make_client, make_settings
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
    [{}, {"mode": "x"}, {"mode": "Daily"}, {"mode": 1}, {"mode": "free", "score": 50}, {"mode": "free", "won": True},
     {"mode": "free", "date": "2026-10-10"}, {"mode": "free", "user_id": "x"}],
)
def test_create_refuses_bad_bodies(alice: TestClient, db_path: Path, body: dict) -> None:
    r = alice.post(GAMES, json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert db_snapshot(db_path)[1]["games"] == 0


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


@pytest.mark.parametrize("query", ["", "?mode=x", "?mode=Daily", "?mode=' OR 1=1--"])
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


# --- submitting (GARDEN × SUMMER; tile [r,c] = GARDEN[c] + SUMMER[r]) ---

TILES = {
    "GEAR": [[4, 0], [5, 1]],
    "DEER": [[4, 3], [5, 4]],
    "NEAR": [[4, 5], [5, 1]],
    "RUNE": [[1, 2], [4, 5]],
    "AMAM": [[2, 1], [3, 1]],
    "NEARER": [[4, 5], [5, 1], [5, 4]],
    "DEARER": [[4, 3], [5, 1], [5, 4]],
    "GENRES": [[4, 0], [5, 5], [0, 4]],
    "RERUNS": [[4, 2], [1, 2], [0, 5]],
}
WRONG = {"GSAS": [[0, 0], [0, 1]], "GSGU": [[0, 0], [1, 0]], "RSDS": [[0, 2], [0, 3]]}
RESULT_KEYS = {
    "status", "word", "points", "score", "tries_left", "found_count", "total_words", "won", "game_over", "missed_words",
}


def submit_path(game: dict) -> str:
    return f"{GAMES}/{game['game_id']}/submit"


def send(c: TestClient, game: dict, tiles: list) -> dict:
    r = c.post(submit_path(game), json={"tiles": tiles})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def game(alice: TestClient) -> dict:
    return start(alice)


def test_accepted_word(alice: TestClient, game: dict, db_path: Path) -> None:
    result = send(alice, game, TILES["GEAR"])
    assert set(result) == RESULT_KEYS
    assert result == {
        "status": "accepted", "word": "GEAR", "points": 4, "score": 4, "tries_left": 3, "found_count": 1,
        "total_words": 9, "won": False, "game_over": False, "missed_words": None,
    }
    assert rows(db_path, "SELECT game_mode, word, is_valid, points, submitted_at, month FROM submissions") == [
        ("free", "GEAR", 1, 4, "2026-10-11T12:00:00.000000Z", "2026-10")
    ]


def test_six_letter_word(alice: TestClient, game: dict) -> None:
    send(alice, game, TILES["GEAR"])
    result = send(alice, game, TILES["NEARER"])
    assert (result["status"], result["word"], result["points"], result["score"], result["found_count"]) == (
        "accepted", "NEARER", 9, 13, 2
    )


def test_rejected_word_costs_a_try(alice: TestClient, game: dict, db_path: Path) -> None:
    result = send(alice, game, WRONG["GSAS"])
    assert (result["status"], result["word"], result["points"], result["tries_left"], result["found_count"]) == (
        "rejected", "GSAS", 0, 2, 0
    )
    assert rows(db_path, "SELECT word, is_valid, points FROM submissions") == [("GSAS", 0, 0)]


def test_duplicate_costs_nothing_and_stores_nothing(alice: TestClient, game: dict, db_path: Path) -> None:
    send(alice, game, TILES["GEAR"])
    result = send(alice, game, TILES["GEAR"])
    assert (result["status"], result["points"], result["score"], result["tries_left"], result["found_count"]) == (
        "duplicate", 0, 4, 3, 1
    )
    assert rows(db_path, "SELECT COUNT(*) FROM submissions") == [(1,)]


def test_duplicate_is_per_word_not_per_tile_set(alice: TestClient, game: dict) -> None:
    assert send(alice, game, [[2, 1], [3, 1]])["status"] == "accepted"
    assert send(alice, game, [[3, 1], [2, 1]])["status"] == "duplicate"


def test_a_rejected_word_costs_a_try_again(alice: TestClient, game: dict, db_path: Path) -> None:
    assert send(alice, game, WRONG["GSAS"])["tries_left"] == 2
    assert send(alice, game, WRONG["GSAS"])["tries_left"] == 1
    assert rows(db_path, "SELECT COUNT(*) FROM submissions WHERE is_valid = 0") == [(2,)]


def test_found_count_rises_only_on_accepted(alice: TestClient, game: dict) -> None:
    sequence = (TILES["GEAR"], WRONG["GSAS"], TILES["GEAR"], TILES["DEER"])
    assert [send(alice, game, t)["found_count"] for t in sequence] == [1, 1, 1, 2]


def test_reload_resumes_found_words_score_and_tries(alice: TestClient, game: dict) -> None:
    for tiles in (TILES["NEARER"], WRONG["GSAS"], TILES["GEAR"]):
        send(alice, game, tiles)
    state = alice.get(CURRENT).json()["game"]
    assert state["found"] == [{"word": "NEARER", "points": 9}, {"word": "GEAR", "points": 4}]
    assert (state["score"], state["tries_left"], state["found_count"], state["won"]) == (13, 2, 2, False)


def test_submit_needs_a_player(client: TestClient, game: dict) -> None:
    assert client.post(submit_path(game), json={"tiles": TILES["GEAR"]}).status_code == 401


@pytest.mark.parametrize("target", ["alices", "random-uuid4", "not-a-uuid", "' OR 1=1--"])
def test_someone_elses_or_unknown_game_is_404(
    alice: TestClient, player: Player, game: dict, db_path: Path, target: str
) -> None:
    game_id = {"alices": game["game_id"], "random-uuid4": str(uuid.uuid4())}.get(target, target)
    bob = player("Bob")
    before = db_snapshot(db_path)
    r = bob.post(f"{GAMES}/{game_id}/submit", json={"tiles": WRONG["GSAS"]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert db_snapshot(db_path) == before
    assert alice.get(CURRENT).json()["game"]["tries_left"] == 3


@pytest.mark.parametrize("extra", [{"score": 56}, {"points": 25}, {"won": True}, {"date": "2026-10-10"}, {"word": "GEAR"}])
def test_client_scores_are_refused(alice: TestClient, game: dict, db_path: Path, extra: dict) -> None:
    before = db_snapshot(db_path)
    r = alice.post(submit_path(game), json={"tiles": TILES["GEAR"], **extra})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert db_snapshot(db_path) == before


# --- ending (POST /end comes with T14) ---

def test_third_rejected_word_ends_the_game(alice: TestClient, game: dict, db_path: Path) -> None:
    send(alice, game, TILES["GEAR"])
    send(alice, game, WRONG["GSAS"])
    send(alice, game, WRONG["GSGU"])
    result = send(alice, game, WRONG["RSDS"])
    assert (result["status"], result["tries_left"], result["game_over"], result["score"]) == ("rejected", 0, True, 4)
    assert result["missed_words"] == ["DEARER", "GENRES", "NEARER", "RERUNS", "AMAM", "DEER", "NEAR", "RUNE"]
    assert rows(db_path, "SELECT ended_at, wrong_count, score FROM games") == [("2026-10-11T12:00:00.000000Z", 3, 4)]
    assert alice.get(CURRENT).json() == {"game": None}


def test_finding_every_word_ends_the_game(alice: TestClient, game: dict, db_path: Path) -> None:
    results = [send(alice, game, tiles) for tiles in TILES.values()]
    assert [r["game_over"] for r in results] == [False] * 8 + [True]
    last = results[-1]
    assert (last["status"], last["score"], last["found_count"], last["missed_words"]) == ("accepted", 56, 9, [])
    assert rows(db_path, "SELECT ended_at IS NOT NULL, score FROM games") == [(1, 56)]


@pytest.mark.parametrize("ending", ["tries", "all_found", "replaced"])
def test_submit_after_the_end_is_409(alice: TestClient, game: dict, db_path: Path, ending: str) -> None:
    if ending == "tries":
        for tiles in WRONG.values():
            send(alice, game, tiles)
    elif ending == "all_found":
        for tiles in TILES.values():
            send(alice, game, tiles)
    else:
        send(alice, game, TILES["GEAR"])
        start(alice)
    before = db_snapshot(db_path)
    for tiles in (TILES["DEER"], WRONG["GSAS"]):
        r = alice.post(submit_path(game), json={"tiles": tiles})
        assert r.status_code == 409 and r.json()["error"]["code"] == "game_over"
    assert db_snapshot(db_path) == before  # no row stored, tries and score unchanged


def test_submit_after_midnight_closes_the_game(alice: TestClient, clock: FakeClock, db_path: Path) -> None:
    clock.set(clock.now().replace(hour=23, minute=59, second=59))
    game = start(alice)
    clock.advance(0.999999)  # 23:59:59.999999
    assert send(alice, game, TILES["GEAR"])["status"] == "accepted"
    clock.advance(0.000001)  # 00:00:00
    for _ in range(2):
        r = alice.post(submit_path(game), json={"tiles": TILES["DEER"]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "day_closed"
    assert rows(db_path, "SELECT ended_at, score, wrong_count FROM games") == [("2026-10-12T00:00:00.000000Z", 4, 0)]
    assert rows(db_path, "SELECT word FROM submissions") == [("GEAR",)]


# --- POST /end (T14) ---

def end_path(game: dict) -> str:
    return f"{GAMES}/{game['game_id']}/end"


def test_end_returns_the_final_state_with_missed_words(alice: TestClient, game: dict, db_path: Path) -> None:
    send(alice, game, TILES["GEAR"])
    r = alice.post(end_path(game), json={})
    assert r.status_code == 200
    state = r.json()
    assert set(state) == STATE_KEYS
    assert (state["game_over"], state["score"], state["found_count"], state["tries_left"]) == (True, 4, 1, 3)
    assert state["missed_words"] == ["DEARER", "GENRES", "NEARER", "RERUNS", "AMAM", "DEER", "NEAR", "RUNE"]
    assert rows(db_path, "SELECT ended_at, score FROM games") == [("2026-10-11T12:00:00.000000Z", 4)]
    assert alice.get(CURRENT).json() == {"game": None}


def test_second_end_is_409_and_changes_nothing(alice: TestClient, game: dict, db_path: Path) -> None:
    send(alice, game, TILES["GEAR"])
    alice.post(end_path(game), json={})
    before = db_snapshot(db_path)
    r = alice.post(end_path(game), json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "game_over"
    assert db_snapshot(db_path) == before


def test_submit_after_end_is_409(alice: TestClient, game: dict) -> None:
    alice.post(end_path(game), json={})
    r = alice.post(submit_path(game), json={"tiles": TILES["GEAR"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "game_over"


def test_end_someone_elses_game_is_404(player: Player, game: dict, db_path: Path) -> None:
    bob = player("Bob")
    before = db_snapshot(db_path)
    for game_id in (game["game_id"], str(uuid.uuid4()), "not-a-uuid"):  # Alice's game looks like a missing one
        r = bob.post(f"{GAMES}/{game_id}/end", json={})
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert db_snapshot(db_path) == before


@pytest.mark.parametrize("body", [{"score": 99}, {"won": True}, {"reason": "player"}])
def test_end_takes_no_input(alice: TestClient, game: dict, db_path: Path, body: dict) -> None:
    before = db_snapshot(db_path)
    r = alice.post(end_path(game), json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert db_snapshot(db_path) == before


def test_end_after_midnight_is_day_closed(alice: TestClient, clock: FakeClock, db_path: Path) -> None:
    clock.set(clock.now().replace(hour=23, minute=59, second=0))
    game = start(alice)
    send(alice, game, TILES["GEAR"])
    clock.advance(60)
    r = alice.post(end_path(game), json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "day_closed"
    assert rows(db_path, "SELECT ended_at, score FROM games") == [("2026-10-12T00:00:00.000000Z", 4)]


# --- closing abandoned games (T14) ---

def test_current_closes_yesterdays_open_game(alice: TestClient, clock: FakeClock, db_path: Path) -> None:
    clock.set(clock.now().replace(hour=23, minute=0))
    game = start(alice)
    send(alice, game, TILES["GEAR"])
    clock.advance(3600)  # 2026-10-12T00:00
    assert alice.get(CURRENT).json() == {"game": None}
    assert rows(db_path, "SELECT ended_at, score FROM games") == [("2026-10-12T00:00:00.000000Z", 4)]


def test_new_game_closes_yesterdays_game_as_day_closed_not_replaced(alice: TestClient, clock: FakeClock, db_path: Path) -> None:
    clock.set(clock.now().replace(hour=23, minute=0))
    old = start(alice)
    clock.advance(7200)
    new = start(alice)
    assert new["game_id"] != old["game_id"]
    # ended_at is when the sweep closed it; the game still counts for the day it started
    assert rows(db_path, "SELECT ended_at FROM games WHERE id = ?", old["game_id"]) == [("2026-10-12T01:00:00.000000Z",)]


def test_sweep_only_closes_past_days(alice: TestClient, player: Player, clock: FakeClock, db_path: Path, settings: Settings, lex: Lexicon) -> None:
    clock.set(clock.now().replace(hour=23, minute=0))
    start(alice)  # yesterday's game, once the clock moves on
    clock.advance(7200)
    start(player("Bob"))  # today's game
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        assert games.sweep_abandoned(conn, lex, settings, clock.now()) == 1
        assert games.sweep_abandoned(conn, lex, settings, clock.now()) == 0
    finally:
        conn.close()
    assert rows(db_path, "SELECT COUNT(*) FROM games WHERE ended_at IS NULL") == [(1,)]


def test_startup_closes_abandoned_games(alice: TestClient, clock: FakeClock, db_path: Path, settings: Settings) -> None:
    game = start(alice)
    send(alice, game, TILES["GEAR"])
    clock.advance(86400)
    with make_client(create_app(settings, clock, random.Random(0))):
        pass  # the lifespan runs the sweep
    assert rows(db_path, "SELECT ended_at, score FROM games") == [("2026-10-12T12:00:00.000000Z", 4)]


# --- invalid tiles: 422, no try used, nothing stored ---

@pytest.mark.parametrize(
    "tiles, code",
    [
        ([], "invalid_request"),
        ([[4, 0]], "invalid_request"),
        ([[0, 0], [0, 6]], "invalid_tiles"),  # off this 6×6 grid
        ([[0, 0], [6, 0]], "invalid_tiles"),
        ([[-1, 0], [0, 0]], "invalid_request"),
        ([[0, 0], [10, 0]], "invalid_request"),
        ([[0, 0], [0, 0]], "invalid_request"),  # the same tile twice
        ([["1", "0"], [0, 0]], "invalid_request"),
        ([[True, 0], [0, 0]], "invalid_request"),
        ([[1.0, 0], [0, 0]], "invalid_request"),
        ([[0, 0, 0], [0, 1]], "invalid_request"),
        ([[0], [0, 1]], "invalid_request"),
        ([[r, c] for r in range(6) for c in range(2)][:7], "invalid_tiles"),  # 7 tiles > max_tiles (6)
        ([[r, c] for r in range(10) for c in range(10)][:51], "invalid_request"),
        ("GEAR", "invalid_request"),
    ],
)
def test_invalid_tiles(alice: TestClient, game: dict, db_path: Path, tiles: object, code: str) -> None:
    before = db_snapshot(db_path)
    r = alice.post(submit_path(game), json={"tiles": tiles})
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    assert db_snapshot(db_path) == before
    assert alice.get(CURRENT).json()["game"]["tries_left"] == 3


def test_max_tiles_comes_from_the_lexicon(lex: Lexicon) -> None:
    assert lex.max_tiles == 6  # DEMOCRATICLO, the longest fixture word


# --- S5 races on a live server (SUBMIT_MIN_GAP_S = 0) ---

def live_player(base_url: str, nickname: str) -> dict[str, str]:
    """Headers for a new player. The live server is plain http, so the Secure cookie is passed by hand."""
    r = httpx.post(f"{base_url}/api/users", json={"nickname": nickname}, headers={"Origin": base_url})
    assert r.status_code == 201
    token = r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    return {"Origin": base_url, "Cookie": f"trur_token={token}"}


def live_game(base_url: str, headers: dict[str, str]) -> dict:
    game = httpx.post(f"{base_url}{GAMES}", json={"mode": "free"}, headers=headers).json()
    assert (game["word1"], game["word2"]) == ("GARDEN", "SUMMER")
    return game


def burst(base_url: str, headers: dict[str, str], path: str, body: dict, n: int) -> list[httpx.Response]:
    async def run() -> list[httpx.Response]:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as c:
            return await asyncio.gather(*(c.post(path, json=body) for _ in range(n)))

    return asyncio.run(run())


def test_parallel_identical_submits_score_once(live_server: LiveServer, db_path: Path) -> None:
    headers = live_player(live_server.base_url, "Alice")
    game = live_game(live_server.base_url, headers)
    responses = burst(live_server.base_url, headers, submit_path(game), {"tiles": TILES["GEAR"]}, 20)
    assert [r.status_code for r in responses] == [200] * 20
    assert sorted(r.json()["status"] for r in responses) == ["accepted"] + ["duplicate"] * 19
    assert rows(db_path, "SELECT score FROM games") == [(4,)]
    assert rows(db_path, "SELECT word FROM submissions WHERE is_valid = 1") == [("GEAR",)]


def test_parallel_wrong_submits_stop_at_three(live_server: LiveServer, db_path: Path) -> None:
    headers = live_player(live_server.base_url, "Alice")
    game = live_game(live_server.base_url, headers)
    responses = burst(live_server.base_url, headers, submit_path(game), {"tiles": WRONG["GSAS"]}, 10)
    assert sorted(r.status_code for r in responses) == [200] * 3 + [409] * 7
    assert all(r.json()["error"]["code"] == "game_over" for r in responses if r.status_code == 409)
    assert rows(db_path, "SELECT wrong_count, ended_at IS NOT NULL FROM games") == [(3, 1)]
    assert rows(db_path, "SELECT COUNT(*) FROM submissions WHERE is_valid = 0") == [(3,)]
    assert rows(db_path, "SELECT COUNT(*) FROM events WHERE type = 'game_ended'") == [(1,)]


# --- the gameplay event log (T18; TS step 4 "Logging") ---

EVENTS = "/api/events"


def events(db_path: Path) -> list[tuple]:
    return rows(db_path, "SELECT type, game_id, payload FROM events ORDER BY id")


def tile_event(game_id: str, **overrides: object) -> dict:
    return {"type": "tile_clicked", "game_id": game_id, "row": 4, "col": 0, "selected": True, "client_ms": 1200, **overrides}


def test_the_server_logs_each_game_step(alice: TestClient, game: dict, db_path: Path) -> None:
    send(alice, game, TILES["GEAR"])
    send(alice, game, TILES["GEAR"])
    send(alice, game, WRONG["GSAS"])
    assert alice.post(f"{GAMES}/{game['game_id']}/end", json={}).status_code == 200
    gid = game["game_id"]
    assert events(db_path) == [
        ("game_started", gid, '{"mode":"free"}'),
        ("word_accepted", gid, '{"word":"GEAR","points":4}'),
        ("word_duplicate", gid, '{"word":"GEAR","points":0}'),
        ("word_rejected", gid, '{"word":"GSAS","points":0}'),
        ("game_ended", gid, '{"reason":"player","score":4,"flagged":false}'),
    ]
    user_ids = rows(db_path, "SELECT DISTINCT e.user_id = u.id FROM events e, users u")
    assert user_ids == [(1,)] and rows(db_path, "SELECT created_at FROM events LIMIT 1") == [("2026-10-11T12:00:00.000000Z",)]


@pytest.mark.parametrize(
    "finish, reason",
    [
        (lambda c, g: [send(c, g, WRONG[w]) for w in WRONG], "tries"),
        (lambda c, g: [send(c, g, TILES[w]) for w in TILES], "all_found"),
        (lambda c, g: start(c), "replaced"),
    ],
)
def test_game_ended_is_logged_once_with_its_reason(alice: TestClient, game: dict, db_path: Path, finish, reason: str) -> None:
    finish(alice, game)
    ended = rows(db_path, "SELECT game_id, json_extract(payload, '$.reason') FROM events WHERE type = 'game_ended'")
    assert ended == [(game["game_id"], reason)]


def test_client_events_are_stored_with_player_and_game(alice: TestClient, game: dict, db_path: Path) -> None:
    batch = [tile_event(game["game_id"], client_ms=i) for i in range(49)]
    batch.append({"type": "selection_cleared", "game_id": game["game_id"], "client_ms": 49})
    r = alice.post(EVENTS, json={"events": batch})
    assert r.status_code == 204 and r.content == b""
    stored = rows(db_path, "SELECT type, game_id, payload FROM events WHERE type IN ('tile_clicked', 'selection_cleared')")
    assert len(stored) == 50
    assert stored[0] == ("tile_clicked", game["game_id"], '{"row":4,"col":0,"selected":true,"client_ms":0}')
    assert stored[-1] == ("selection_cleared", game["game_id"], '{"client_ms":49}')
    assert rows(db_path, "SELECT COUNT(*) FROM events WHERE user_id IS NULL") == [(0,)]


@pytest.mark.parametrize(
    "make_batch",
    [
        lambda g: [tile_event(g)] * 51,
        lambda g: [],
        lambda g: [tile_event(g, type="word_accepted")],  # server-only
        lambda g: [tile_event(g, type="game_ended")],
        lambda g: [tile_event(g, type="nope")],
        lambda g: [tile_event(g, extra=1)],
        lambda g: [tile_event(g, row=10)],
        lambda g: [tile_event(g, client_ms=-1)],
        lambda g: [tile_event(g, selected="yes")],
        lambda g: [{"type": "selection_cleared", "game_id": g, "client_ms": 1, "row": 0}],
    ],
)
def test_bad_event_batches_are_422(alice: TestClient, game: dict, db_path: Path, make_batch) -> None:
    before = db_snapshot(db_path)
    r = alice.post(EVENTS, json={"events": make_batch(game["game_id"])})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert db_snapshot(db_path) == before


def test_a_batch_with_someone_elses_game_is_dropped(alice: TestClient, game: dict, player: Player, db_path: Path) -> None:
    bob_game = start(player("Bob"))
    before = db_snapshot(db_path)
    for other in (bob_game["game_id"], str(uuid.uuid4())):
        r = alice.post(EVENTS, json={"events": [tile_event(game["game_id"]), tile_event(other)]})
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert db_snapshot(db_path) == before


def test_events_need_a_player(client: TestClient, game: dict, db_path: Path) -> None:
    before = db_snapshot(db_path)
    assert client.post(EVENTS, json={"events": [tile_event(game["game_id"])]}).status_code == 401
    assert db_snapshot(db_path) == before
