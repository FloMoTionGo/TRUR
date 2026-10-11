"""The minimal player cookie: registration, /me, and the JSON/Origin check (T11; TS step 4 "Players", S2)."""

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ORIGIN, db_snapshot
from trur.auth import COOKIE, COOKIE_MAX_AGE

ME = "/api/users/me"
USERS = "/api/users"


def _users(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT nickname, nickname_key, token_hash FROM users").fetchall()
    finally:
        conn.close()


def test_registration_sets_a_hardened_cookie(client: TestClient, db_path: Path) -> None:
    r = client.post(USERS, json={"nickname": "Alice"})
    assert r.status_code == 201
    assert r.json() == {"nickname": "Alice"}
    cookie = r.headers["set-cookie"]
    attrs = {part.strip().split("=")[0].lower() for part in cookie.split(";")}
    assert {"httponly", "secure", "path", "max-age", "samesite"} <= attrs
    assert "samesite=lax" in cookie.lower() and f"max-age={COOKIE_MAX_AGE}" in cookie.lower()
    assert "path=/" in cookie.lower()
    token = client.cookies[COOKIE]
    assert len(token) == 43  # token_urlsafe(32): 256 random bits

    assert _users(db_path) == [("Alice", "Alice", hashlib.sha256(token.encode()).hexdigest())]
    raw = b"".join(p.read_bytes() for p in db_path.parent.glob("trur.db*"))
    assert token.encode() not in raw  # the database stores only the hash


def test_me_returns_the_nickname(player: Callable[[str], TestClient]) -> None:
    alice = player("Alice")
    r = alice.get(ME)
    assert r.status_code == 200 and r.json() == {"nickname": "Alice"}
    assert "user_id" not in r.text and "token" not in r.text


def test_me_without_a_cookie_is_401(client: TestClient) -> None:
    r = client.get(ME)
    assert r.status_code == 401 and r.json()["error"]["code"] == "no_player"


def test_me_with_an_unknown_token_is_401(client: TestClient, db_path: Path) -> None:
    client.cookies.set(COOKIE, "x" * 43, domain="testserver")
    assert client.get(ME).status_code == 401
    assert _users(db_path) == []  # no player is created on the way


def test_second_registration_is_409(player: Callable[[str], TestClient], db_path: Path) -> None:
    alice = player("Alice")
    r = alice.post(USERS, json={"nickname": "Bob"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_registered"
    assert len(_users(db_path)) == 1


def test_a_taken_nickname_is_409(player: Callable[[str], TestClient], client: TestClient) -> None:
    player("Alice")
    r = client.post(USERS, json={"nickname": "Alice"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "nickname_taken"
    assert COOKIE not in client.cookies


@pytest.mark.parametrize("nickname", ["abc", "a-b", "_x_", "Flo Mo", "a" * 16, "a1 b2 c3 d4 e5 f"])
def test_nickname_accepted(client: TestClient, nickname: str) -> None:
    r = client.post(USERS, json={"nickname": nickname})
    assert r.status_code == 201 and r.json() == {"nickname": nickname}


@pytest.mark.parametrize(
    "nickname",
    ["ab", "a" * 17, "", "<script>", "FlоMo", "Flo\U0001f600", "Flo\tMo", " Flo", "Flo ", "Flo  Mo", "Flo\nMo", "Flo\n"],
)
def test_nickname_refused(client: TestClient, db_path: Path, nickname: str) -> None:
    r = client.post(USERS, json={"nickname": nickname})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_nickname"
    assert "<script>" not in r.text  # never echoed back
    assert _users(db_path) == []


@pytest.mark.parametrize(
    "body", [{}, {"nickname": 5}, {"nickname": None}, {"nickname": "Alice", "user_id": "x"}, ["Alice"]]
)
def test_malformed_registration_is_422(client: TestClient, db_path: Path, body: object) -> None:
    r = client.post(USERS, json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert _users(db_path) == []


# --- check_post: JSON only, from an allowed Origin (S2) ---

@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://testserver", ORIGIN + ".evil.example"])
def test_foreign_origin_is_403(client: TestClient, db_path: Path, origin: str) -> None:
    before = db_snapshot(db_path)
    r = client.post(USERS, json={"nickname": "Alice"}, headers={"Origin": origin})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    assert db_snapshot(db_path) == before


def test_missing_origin_is_403(app: FastAPI, client: TestClient, db_path: Path) -> None:
    with TestClient(app, base_url=ORIGIN) as bare:
        assert "origin" not in bare.headers
        r = bare.post(USERS, json={"nickname": "Alice"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    assert _users(db_path) == []


@pytest.mark.parametrize(
    "content_type, body",
    [
        ("text/plain", b'{"nickname": "Alice"}'),
        ("application/x-www-form-urlencoded", b"nickname=Alice"),
        ("multipart/form-data; boundary=x", b"--x--"),
        (None, b'{"nickname": "Alice"}'),
    ],
)
def test_non_json_is_415(client: TestClient, db_path: Path, content_type: str | None, body: bytes) -> None:
    headers = {"Content-Type": content_type} if content_type else {}
    r = client.post(USERS, content=body, headers=headers)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"
    assert _users(db_path) == []


def test_json_with_charset_is_accepted(client: TestClient) -> None:
    r = client.post(USERS, content=b'{"nickname": "Alice"}', headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 201
