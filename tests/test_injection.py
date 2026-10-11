"""S1: SQL injection through every input that exists so far (docs/test-strategy.md §4, S1).

Each payload goes into each input. Nothing may return a 500 or mention SQL, and the schema,
the row counts and PRAGMA integrity_check must be unchanged afterwards.

Inputs added by later tasks join this matrix there: event type and payload (T18),
the answers date (T22), and the board month and date (T26).
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from tests.conftest import db_snapshot, make_client

PAYLOADS = [
    "' OR 1=1--",
    "x'); DROP TABLE submissions;--",
    "1; DROP TABLE users",
    "' UNION SELECT token_hash FROM users--",
    "%27",
    "a\x00b",
    "A" * 10_000,
]

Player = Callable[[str], TestClient]


def integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def assert_harmless(response, db_path: Path, before: tuple) -> None:
    assert response.status_code < 500, response.text
    text = response.text.lower()
    assert "sqlite" not in text and "syntax" not in text
    assert db_snapshot(db_path) == before
    assert integrity_ok(db_path)


@pytest.fixture
def alice(player: Player) -> TestClient:
    return player("Alice")


@pytest.fixture
def game_id(alice: TestClient) -> str:
    return alice.post("/api/games", json={"mode": "free"}).json()["game_id"]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_nickname(client: TestClient, db_path: Path, payload: str) -> None:
    before = db_snapshot(db_path)
    assert_harmless(client.post("/api/users", json={"nickname": payload}), db_path, before)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_mode_in_body_and_query(alice: TestClient, db_path: Path, payload: str) -> None:
    before = db_snapshot(db_path)
    assert_harmless(alice.post("/api/games", json={"mode": payload}), db_path, before)
    assert_harmless(alice.get("/api/games/current", params={"mode": payload}), db_path, before)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_game_id_in_the_path(alice: TestClient, game_id: str, db_path: Path, payload: str) -> None:
    before = db_snapshot(db_path)
    for action, body in (("submit", {"tiles": [[4, 0], [5, 1]]}), ("end", {})):
        r = alice.post(f"/api/games/{quote(payload, safe='')}/{action}", json=body)  # as sent on the wire
        assert r.status_code in (404, 405), r.text
        assert_harmless(r, db_path, before)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_tiles(alice: TestClient, game_id: str, db_path: Path, payload: str) -> None:
    before = db_snapshot(db_path)
    for tiles in ([[payload, 0], [5, 1]], [payload, [5, 1]], payload):
        assert_harmless(alice.post(f"/api/games/{game_id}/submit", json={"tiles": tiles}), db_path, before)


@pytest.mark.parametrize("payload", [p for p in PAYLOADS if "\x00" not in p])  # a NUL can't go in a header
def test_cookie(app, db_path: Path, payload: str) -> None:
    before = db_snapshot(db_path)
    with make_client(app) as c:
        c.cookies.set("trur_token", payload)
        for r in (c.get("/api/users/me"), c.post("/api/games", json={"mode": "free"})):
            assert r.status_code == 401, r.text
            assert_harmless(r, db_path, before)


def test_sql_lookalike_nickname_is_stored_verbatim(client: TestClient, db_path: Path) -> None:
    """Only allowed characters, so it passes validation: this proves the placeholders, not the regex."""
    r = client.post("/api/users", json={"nickname": "a-- DROP users"})
    assert r.status_code == 201 and r.json() == {"nickname": "a-- DROP users"}
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT nickname FROM users").fetchall() == [("a-- DROP users",)]
    finally:
        conn.close()
    assert integrity_ok(db_path)
