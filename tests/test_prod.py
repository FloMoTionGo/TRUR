"""Go-live: production settings, fly.toml, the Docker image's contents and the route inventory
(TS §3 step 7; S9, S11, S12). Docker itself runs in CI's `docker` job."""

import tomllib
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount

from tests.conftest import FIXTURES, ROOT, FakeClock, make_client
from trur.api import router as api_router
from trur.app import create_app
from trur.config import Settings, load_settings
from trur.security import HSTS

FLY = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

# docs/architecture.md §3 plus /healthz, and the multiplayer routes (docs/multiplayer.md §6.4).
# A new route has to be added here on purpose (S11).
ROUTES = {
    ("GET", "/healthz"),
    ("POST", "/api/users"),
    ("GET", "/api/users/me"),
    ("POST", "/api/games"),
    ("GET", "/api/games/current"),
    ("POST", "/api/games/{game_id}/submit"),
    ("POST", "/api/games/{game_id}/end"),
    ("POST", "/api/events"),
    ("GET", "/api/daily/{day}/answers"),
    ("GET", "/api/rankings"),
    ("GET", "/api/rankings/daily"),
    ("POST", "/api/match/queue"),
    ("POST", "/api/match/queue/leave"),
    ("GET", "/api/match/current"),
    ("GET", "/api/match/stream"),
    ("POST", "/api/match/{match_id}/submit"),
    ("POST", "/api/match/{match_id}/showcase"),
    ("POST", "/api/match/{match_id}/vote"),
    ("GET", "/api/match/{match_id}/results"),
}


def prod_settings(tmp_path: Path) -> Settings:
    """fly.toml's [env], with the Dockerfile's paths swapped for local ones and no network fetch."""
    env = dict(FLY["env"])
    env.update(DATA_DIR=str(tmp_path), LEXICON_DIR=str(FIXTURES), WEB_DIR=str(ROOT / "web"),
               DAILY_JSON_SOURCE=str(FIXTURES / "daily" / "valid.json"))
    return load_settings(env)


# --- fly.toml ---

def test_fly_toml() -> None:
    assert (FLY["app"], FLY["primary_region"]) == ("trur", "fra")
    service = FLY["http_service"]
    assert service["force_https"] is True
    assert service["internal_port"] == 8080 and '"--port","8080"' in DOCKERFILE
    assert service["concurrency"] == {"type": "requests", "soft_limit": 50, "hard_limit": 100}
    assert [c["path"] for c in service["checks"]] == ["/healthz"]
    assert FLY["mounts"] == {"source": "trur_data", "destination": "/data"}
    assert FLY["env"]["DATA_DIR"] == "/data"


def test_fly_env_is_the_production_column() -> None:
    s = load_settings(FLY["env"])
    assert s.db_path == Path("/data/trur.db")
    assert s.allowed_origins == ("https://trur.fly.dev",)
    assert s.daily_json_source.startswith("https://raw.githubusercontent.com/FloMoTionGo/TRUR/main/")
    assert (s.cookie_secure, s.hsts, s.trust_fly_client_ip, s.debug) == (True, True, True, False)
    assert set(FLY["env"]) & {"FLY_API_TOKEN", "SECRET_KEY"} == set()  # Fly needs no secrets


# --- production settings (TS step 7) ---

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_off_in_production(tmp_path: Path, clock: FakeClock, path: str) -> None:
    with make_client(create_app(prod_settings(tmp_path), clock)) as c:
        assert c.get(path).status_code == 404


def test_production_sends_hsts(tmp_path: Path, clock: FakeClock) -> None:
    with make_client(create_app(prod_settings(tmp_path), clock)) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert r.headers["strict-transport-security"] == HSTS


# --- S11: route inventory ---

def test_routes_are_the_api_tables_plus_static_files(tmp_path: Path, clock: FakeClock) -> None:
    assert {(m, r.path) for r in api_router.routes if isinstance(r, APIRoute) for m in r.methods} == ROUTES
    app = create_app(prod_settings(tmp_path), clock)
    *included, static = app.routes
    assert len(included) == 1  # the API router, and no docs, redoc or openapi routes
    assert isinstance(static, Mount) and static.path == ""  # web/ at /, mounted last so the API wins


# --- the image (S9) ---

def test_dockerfile_installs_the_lock_and_runs_one_worker() -> None:
    assert "FROM python:3.12-slim" in DOCKERFILE
    assert "pip install --no-cache-dir --require-hashes -r requirements.txt" in DOCKERFILE
    assert '"--workers","1"' in DOCKERFILE and '"--no-server-header"' in DOCKERFILE
    assert '"--timeout-graceful-shutdown","2"' in DOCKERFILE  # open match streams can't hold up a deploy
    copied = {line.split()[1] for line in DOCKERFILE.splitlines() if line.startswith("COPY ")}
    assert copied == {"requirements.txt", "trur", "web", "data", "scripts"}  # never tests/, var/ or the DB


def test_dockerignore_keeps_the_database_and_secrets_out() -> None:
    assert {".git", "var/", "*.db", "*.db-wal", "*.db-shm", ".venv/", "tests/", "build/"} <= set(DOCKERIGNORE)


# --- S13: the privacy note ---

def test_the_page_has_a_privacy_note() -> None:
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    note = page.split('data-testid="privacy">', 1)[1].split("</p>", 1)[0]
    for promise in ("cookie", "90 days", "IP address is never stored", "deleted"):
        assert promise in note
