"""Security tests: S7 body size and rate limits, S4 submit gap, S9 database file, S12 headers and errors (TS §4)."""

import logging
import random
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from tests.conftest import FakeClock, db_snapshot, make_client, make_settings
from trur import app as app_module
from trur.app import create_app
from trur.config import Settings
from trur.errors import ApiError
from trur.security import CSP, HSTS, RateLimiter

MAX = 16_384


def add_test_routes(app: FastAPI) -> None:
    """Test-only routes, put ahead of any static mount at "/"."""

    def boom() -> None:
        raise RuntimeError("secret detail: sqlite syntax error near users")

    def needs_int(x: int) -> dict:
        return {"x": x}

    def echo(body: dict) -> dict:
        return {"keys": len(body)}

    before = len(app.router.routes)
    app.add_api_route("/api/test/boom", boom, methods=["GET"])
    app.add_api_route("/api/test/int", needs_int, methods=["GET"])
    app.add_api_route("/api/test/echo", echo, methods=["POST"])
    new = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = new


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>TRUR</title>", encoding="utf-8")
    (web / "app.js").write_text("export {};", encoding="utf-8")
    return web


@pytest.fixture
def web_client(db_path: Path, web_dir: Path, clock: FakeClock) -> Iterator[TestClient]:
    """The database sits next to web/ in tmp_path, the way a misconfigured root would expose it."""
    app = create_app(make_settings(db_path.parent, web_dir=web_dir), clock)
    add_test_routes(app)
    with make_client(app) as c:
        yield c


@pytest.fixture
def test_client(app: FastAPI) -> Iterator[TestClient]:
    add_test_routes(app)
    with make_client(app) as c:
        yield c


def assert_envelope(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == code
    assert isinstance(response.json()["error"]["message"], str)


# --- startup, health, docs ---

def test_healthz_is_ok_sets_no_cookie_and_writes_nothing(client: TestClient, db_path: Path) -> None:
    before = db_snapshot(db_path)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "set-cookie" not in r.headers
    assert db_snapshot(db_path) == before


def test_startup_creates_the_data_dir_and_database(tmp_path: Path, clock: FakeClock) -> None:
    data_dir = tmp_path / "new" / "data"
    with make_client(create_app(make_settings(data_dir), clock)) as c:
        assert c.get("/healthz").status_code == 200
    conn = sqlite3.connect(data_dir / "trur.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM words WHERE is_source = 1").fetchone()[0] == 6
    finally:
        conn.close()


def test_startup_purges_old_events(db_path: Path, app: FastAPI) -> None:
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO events (type, created_at) VALUES ('tile_clicked', ?)",
        [("2026-07-13T12:00:00.000000Z",), ("2026-07-13T12:00:01.000000Z",)],  # T − 90 d and T − 90 d + 1 s
    )
    conn.commit()
    conn.close()
    with make_client(app):
        pass
    conn = sqlite3.connect(db_path)
    try:
        assert [r[0] for r in conn.execute("SELECT created_at FROM events")] == ["2026-07-13T12:00:01.000000Z"]
    finally:
        conn.close()


def test_startup_refuses_an_old_sqlite(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module.sqlite3, "sqlite_version_info", (3, 36, 0))
    with pytest.raises(RuntimeError, match="STRICT"):
        with make_client(app):
            pass


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_off_unless_debug(client: TestClient, settings: Settings, clock: FakeClock, path: str) -> None:
    assert_envelope(client.get(path), 404, "not_found")
    debug_app = create_app(make_settings(settings.data_dir, debug=True), clock)
    with make_client(debug_app) as c:
        assert c.get(path).status_code == 200


# --- S9: the database file ---

@pytest.mark.parametrize(
    "path",
    [
        "/trur.db",
        "/var/trur.db",
        "/../var/trur.db",
        "/%2e%2e/var/trur.db",
        "/%2e%2e/trur.db",
        "/..%2ftrur.db",
        "/.git/config",
        "/fly.toml",
        "/trur/app.py",
        "/data/words.txt",
    ],
)
def test_database_and_repo_files_are_not_downloadable(web_client: TestClient, path: str) -> None:
    r = web_client.get(path)
    assert r.status_code == 404
    assert b"SQLite format" not in r.content


def test_web_dir_is_served(web_client: TestClient) -> None:
    assert web_client.get("/").text.startswith("<!doctype html>")
    assert web_client.get("/app.js").status_code == 200


def test_nothing_is_mounted_without_a_web_dir(settings: Settings, clock: FakeClock) -> None:
    app = create_app(make_settings(settings.data_dir, web_dir=settings.data_dir / "missing"), clock)
    with make_client(app) as c:
        assert_envelope(c.get("/"), 404, "not_found")
        assert c.get("/healthz").status_code == 200


def test_default_database_location_is_var() -> None:
    s = Settings()
    assert s.db_path == Path("var") / "trur.db"
    assert Path("web") not in s.db_path.parents and Path("data") not in s.db_path.parents


# --- S12: headers and errors ---

@pytest.mark.parametrize(
    "method, path, status",
    [
        ("GET", "/", 200),  # HTML
        ("GET", "/app.js", 200),  # JS
        ("GET", "/healthz", 200),  # JSON
        ("GET", "/no-such-file", 404),
        ("GET", "/api/no-such-route", 404),
        ("POST", "/healthz", 405),
        ("GET", "/api/test/int?x=abc", 422),
        ("GET", "/api/test/boom", 500),
    ],
)
def test_security_headers_on_every_response(web_client: TestClient, method: str, path: str, status: int) -> None:
    r = web_client.request(method, path)
    assert r.status_code == status
    assert r.headers["content-security-policy"] == CSP
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in r.headers  # off outside production
    assert (r.headers.get("cache-control") == "no-store") == path.startswith("/api/")


def test_hsts_when_enabled(settings: Settings, clock: FakeClock) -> None:
    with make_client(create_app(make_settings(settings.data_dir, hsts=True), clock)) as c:
        assert c.get("/healthz").headers["strict-transport-security"] == HSTS


def test_error_envelopes(test_client: TestClient) -> None:
    assert_envelope(test_client.get("/api/nope"), 404, "not_found")
    assert_envelope(test_client.post("/healthz"), 405, "method_not_allowed")
    r = test_client.get("/api/test/int?x=<script>")
    assert_envelope(r, 422, "invalid_request")
    assert r.json()["error"]["fields"] == ["x"]
    assert "<script>" not in r.text  # never echoes the input


def test_a_raising_route_returns_the_generic_envelope(test_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="trur"):
        r = test_client.get("/api/test/boom")
    assert_envelope(r, 500, "internal_error")
    for leak in ("Traceback", "RuntimeError", "secret", "sqlite", "syntax"):
        assert leak not in r.text
    assert "secret detail" in caplog.text  # the details go to the log


# --- S7: body size ---

def test_body_at_the_cap_is_accepted(test_client: TestClient) -> None:
    body = b'{"a":"' + b"x" * (MAX - 8) + b'"}'
    assert len(body) == MAX
    assert test_client.post("/api/test/echo", content=body, headers={"Content-Type": "application/json"}).status_code == 200


def test_body_over_the_cap_is_413(test_client: TestClient, db_path: Path) -> None:
    before = db_snapshot(db_path)
    body = b'{"a":"' + b"x" * (MAX - 7) + b'"}'
    r = test_client.post("/api/test/echo", content=body, headers={"Content-Type": "application/json"})
    assert_envelope(r, 413, "payload_too_large")
    assert r.headers["content-security-policy"] == CSP
    assert db_snapshot(db_path) == before


@pytest.mark.parametrize("chunk", [1024, MAX + 1])
def test_chunked_body_over_the_cap_is_413(test_client: TestClient, chunk: int) -> None:
    total = MAX + 1

    def body() -> Iterator[bytes]:
        sent = 0
        while sent < total:
            part = min(chunk, total - sent)
            sent += part
            yield b"x" * part

    r = test_client.post("/api/test/echo", content=body(), headers={"Content-Type": "application/json"})
    assert "content-length" not in r.request.headers
    assert_envelope(r, 413, "payload_too_large")



# --- S7 and S4: rate limits (T17) ---

USERS = "/api/users"
IP_A, IP_B = "203.0.113.7", "198.51.100.9"
Limited = Callable[..., TestClient]


@pytest.fixture
def limited(db_path: Path, clock: FakeClock, garden_seed: int) -> Iterator[Limited]:
    """limited(rl_...=n) gives a client on an app with those limits switched on (conftest turns them all off)."""
    made: list[TestClient] = []

    def make(**overrides: object) -> TestClient:
        app = create_app(make_settings(db_path.parent, **overrides), clock, random.Random(garden_seed))
        c = make_client(app).__enter__()
        made.append(c)
        return c

    yield make
    for c in made:
        c.__exit__(None, None, None)


def register(c: TestClient, nickname: str, ip: str = IP_A) -> Response:
    c.cookies.clear()  # a new browser each time
    return c.post(USERS, json={"nickname": nickname}, headers={"Fly-Client-IP": ip})


def test_sixth_new_player_from_one_ip_is_429(limited: Limited, clock: FakeClock, db_path: Path) -> None:
    c = limited(trust_fly_client_ip=True, rl_new_players_per_ip_hour=5)
    for nick in ["Anna", "Bert", "Cleo", "Dora", "Emma"]:
        assert register(c, nick).status_code == 201
    before = db_snapshot(db_path)
    r = register(c, "Finn")
    assert_envelope(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "3600"
    assert db_snapshot(db_path) == before
    assert register(c, "Finn", IP_B).status_code == 201  # a different IP
    clock.advance(3600)
    assert register(c, "Gwen").status_code == 201  # the hour has passed


def test_fly_client_ip_is_ignored_unless_trusted(limited: Limited) -> None:
    c = limited(rl_new_players_per_ip_hour=2)
    assert register(c, "Anna", "10.0.0.1").status_code == 201
    assert register(c, "Bert", "10.0.0.2").status_code == 201
    assert register(c, "Cleo", "10.0.0.3").status_code == 429  # same socket peer, whatever the header says


def test_invalid_nicknames_dont_use_up_the_limit(limited: Limited) -> None:
    c = limited(trust_fly_client_ip=True, rl_new_players_per_ip_hour=1)
    assert register(c, "<script>").status_code == 422
    assert register(c, "ab").status_code == 422
    assert register(c, "Anna").status_code == 201
    assert register(c, "Bert").status_code == 429


def test_the_ip_is_never_stored(limited: Limited, db_path: Path) -> None:
    c = limited(trust_fly_client_ip=True, rl_new_players_per_ip_hour=5)
    assert register(c, "Anna").status_code == 201
    raw = b"".join(p.read_bytes() for p in db_path.parent.glob("trur.db*"))
    assert IP_A.encode() not in raw


def test_thirty_first_game_call_in_an_hour_is_429(limited: Limited, clock: FakeClock, db_path: Path) -> None:
    c = limited(rl_games_per_player_hour=30)
    assert register(c, "Anna").status_code == 201
    for _ in range(30):
        assert c.post("/api/games", json={"mode": "free"}).status_code == 201
    before = db_snapshot(db_path)
    r = c.post("/api/games", json={"mode": "free"})
    assert_envelope(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "3600"
    assert db_snapshot(db_path) == before
    clock.advance(3600)
    assert c.post("/api/games", json={"mode": "free"}).status_code == 201


GEAR, DEER = [[4, 0], [5, 1]], [[4, 3], [5, 4]]  # GARDEN × SUMMER


def test_submits_under_the_gap_are_429_and_cost_nothing(limited: Limited, clock: FakeClock, db_path: Path) -> None:
    c = limited(submit_min_gap_s=1.0)
    assert register(c, "Anna").status_code == 201
    game = c.post("/api/games", json={"mode": "free"}).json()
    path = f"/api/games/{game['game_id']}/submit"
    assert c.post(path, json={"tiles": GEAR}).status_code == 200
    clock.advance(0.5)
    before = db_snapshot(db_path)
    r = c.post(path, json={"tiles": DEER})
    assert_envelope(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "1"
    assert db_snapshot(db_path) == before
    clock.advance(0.5)  # 1.0 s after the first submit
    result = c.post(path, json={"tiles": DEER}).json()
    assert result["status"] == "accepted" and result["tries_left"] == 3 and result["found_count"] == 2


def test_the_gap_is_per_game(limited: Limited) -> None:
    c = limited(submit_min_gap_s=1.0)
    games = []
    for nick in ["Anna", "Bert"]:
        assert register(c, nick).status_code == 201
        games.append((dict(c.cookies), c.post("/api/games", json={"mode": "free"}).json()))
    for cookies, game in games:  # the same moment on the FakeClock
        c.cookies.clear()
        c.cookies.update(cookies)
        assert c.post(f"/api/games/{game['game_id']}/submit", json={"tiles": GEAR}).status_code == 200


def test_per_ip_backstop_covers_every_endpoint(limited: Limited) -> None:
    c = limited(trust_fly_client_ip=True, rl_requests_per_ip_min=3)
    for _ in range(3):
        assert c.get("/healthz", headers={"Fly-Client-IP": IP_A}).status_code == 200
    r = c.get("/api/users/me", headers={"Fly-Client-IP": IP_A})
    assert_envelope(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "60"
    assert c.get("/healthz", headers={"Fly-Client-IP": IP_B}).status_code == 200


def test_rate_limiter_sliding_window(clock: FakeClock) -> None:
    limiter = RateLimiter(clock)
    limiter.check("k", 2, 10)
    clock.advance(5)
    limiter.check("k", 2, 10)
    clock.advance(4)  # t = 9
    with pytest.raises(ApiError) as exc:
        limiter.check("k", 2, 10)
    assert exc.value.status == 429 and exc.value.extra["retry_after"] == 1
    clock.advance(1)  # t = 10: the first hit leaves the window
    limiter.check("k", 2, 10)
    limiter.check("other", 2, 10)  # keys are separate


def test_rate_limiter_zero_is_off(clock: FakeClock) -> None:
    limiter = RateLimiter(clock)
    for _ in range(100):
        limiter.check("k", 0, 60)
        limiter.check("k", 1, 0)


def test_rate_limiter_drops_idle_keys(clock: FakeClock) -> None:
    limiter = RateLimiter(clock)
    limiter.SWEEP_EVERY = 2
    limiter.check("old", 1, 60)
    clock.advance(60)
    limiter.check("new", 1, 60)
    assert list(limiter._hits) == ["new"]


def test_second_events_request_within_a_second_is_429(limited: Limited, clock: FakeClock, db_path: Path) -> None:
    c = limited(rl_events_per_player_s=1)
    assert register(c, "Anna").status_code == 201
    game_id = c.post("/api/games", json={"mode": "free"}).json()["game_id"]
    body = {"events": [{"type": "selection_cleared", "game_id": game_id, "client_ms": 0}]}
    assert c.post("/api/events", json=body).status_code == 204
    before = db_snapshot(db_path)
    r = c.post("/api/events", json=body)
    assert_envelope(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "1"
    assert db_snapshot(db_path) == before
    clock.advance(1)
    assert c.post("/api/events", json=body).status_code == 204
