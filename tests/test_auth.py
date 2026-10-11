"""The player cookie, nickname rules and the JSON/Origin check (T11, T16; TS step 4 "Players", S2, S8)."""

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FIXTURES, ORIGIN, ROOT, db_snapshot
from trur.auth import COOKIE, COOKIE_MAX_AGE, check_nickname, fold_nickname, load_blocklist
from trur.errors import ApiError

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

    assert _users(db_path) == [("Alice", "allce", hashlib.sha256(token.encode()).hexdigest())]
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


# --- S8: lookalikes and the blocklist (T16) ---

@pytest.mark.parametrize("lookalike", ["FIoMo", "F1oMo", "Fl0M0", "FLOMO", "flomo"])
def test_a_lookalike_of_a_taken_nickname_is_409(
    player: Callable[[str], TestClient], client: TestClient, db_path: Path, lookalike: str,
) -> None:
    player("FloMo")
    r = client.post(USERS, json={"nickname": lookalike})
    assert r.status_code == 409 and r.json()["error"]["code"] == "nickname_taken"
    assert [(nick, key) for nick, key, _ in _users(db_path)] == [("FloMo", "flomo")]


@pytest.mark.parametrize("nick, key", [("FloMo", "flomo"), ("Fl0 M1_x-I", "flo ml_x-l"), ("ABC", "abc")])
def test_fold_nickname(nick: str, key: str) -> None:
    assert fold_nickname(nick) == key


@pytest.mark.parametrize(
    "nickname",
    [
        "porn", "PORN", "p0rn", "Fuck", "f_u_c_k", "fu-ck",  # short words: the whole nickname
        "ass_king", "Big ass", "tit-man", "x p0rn x",  # short words: one part between separators
        "asshole", "MrAsshole99", "assh0le", "ass_hole", "Bullsh1t", "Bullshlt",  # long words: anywhere
    ],
)
def test_blocked_nickname_is_422(client: TestClient, db_path: Path, nickname: str) -> None:
    r = client.post(USERS, json={"nickname": nickname})
    assert r.status_code == 422 and r.json()["error"]["code"] == "nickname_not_allowed"
    assert nickname not in r.text  # never echoed back
    assert _users(db_path) == []


# Short words inside a longer part are allowed (owner, 2026-10-11): that keeps real names, and lets
# a few compounds like "bigass" through.
@pytest.mark.parametrize("nickname", ["Cassandra", "Titus", "Glass_Bass", "Pornelius", "Titanic 1912", "bigass"])
def test_short_blocklist_word_inside_a_part_is_allowed(client: TestClient, nickname: str) -> None:
    assert client.post(USERS, json={"nickname": nickname}).status_code == 201


@pytest.mark.parametrize(
    "nickname", ["Cassandra", "Titus", "Cucumber", "Scunthorpe", "Dickens", "Grape", "Spicy", "Raccoon", "Massimo"],
)
def test_real_names_pass_the_real_blocklist(nickname: str) -> None:
    assert check_nickname(nickname, load_blocklist(ROOT / "data")) == nickname


@pytest.mark.parametrize("nickname", ["fuck", "motherfucker99", "Cunt", "big_dick", "bullsh1t"])
def test_the_real_blocklist_blocks(nickname: str) -> None:
    with pytest.raises(ApiError) as exc:
        check_nickname(nickname, load_blocklist(ROOT / "data"))
    assert exc.value.code == "nickname_not_allowed"


def test_load_blocklist_folds_and_skips_comments() -> None:
    assert load_blocklist(FIXTURES) == {"ass", "tlt", "porn", "fuck", "asshole", "bullshlt"}


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
