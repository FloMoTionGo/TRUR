"""Read-only checks against a deployed instance (docs/test-strategy.md, "Smoke test after a Fly deploy").

    pytest tests/smoke --base-url https://trur.fly.dev

Run by hand right after `fly deploy`; no Fly token lives in CI (S11). Nothing here writes: the one
POST is refused by the Origin check before it reaches a route. Skipped without --base-url.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

pytestmark = pytest.mark.smoke

COLD_START_S = 15


@pytest.fixture(scope="module")
def site(base_url: str | None):
    if not base_url:
        pytest.skip("needs --base-url, e.g. https://trur.fly.dev")
    with httpx.Client(base_url=base_url, timeout=COLD_START_S, follow_redirects=False) as c:
        yield c


@pytest.fixture(scope="module")
def today() -> datetime:
    return datetime.now(timezone.utc)


def test_healthz(site: httpx.Client) -> None:
    r = site.get("/healthz")  # first: it may wake a stopped machine
    assert r.status_code == 200
    assert "set-cookie" not in r.headers


def test_page_has_every_security_header(site: httpx.Client) -> None:
    r = site.get("/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "max-age=" in r.headers["strict-transport-security"]
    # Fly's edge adds its own "server: Fly/..."; the app (--no-server-header) must not add uvicorn's
    assert "uvicorn" not in r.headers.get("server", "").lower()


@pytest.mark.parametrize("path", ["/trur.db", "/data/trur.db", "/docs", "/redoc", "/openapi.json"])
def test_no_database_or_docs(site: httpx.Client, path: str) -> None:
    assert site.get(path).status_code == 404


def test_http_redirects_to_https(site: httpx.Client) -> None:
    if not str(site.base_url).startswith("https://"):
        pytest.skip("only for an https deploy")
    r = httpx.get(str(site.base_url.copy_with(scheme="http")).rstrip("/") + "/healthz", timeout=COLD_START_S)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].startswith("https://")


def test_post_from_another_origin_is_refused(site: httpx.Client) -> None:
    r = site.post("/api/users", json={"nickname": "Smoke"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_monthly_board_rows(site: httpx.Client, today: datetime) -> None:
    month = today.strftime("%Y-%m")
    r = site.get("/api/rankings", params={"mode": "free", "month": month})
    assert r.status_code == 200
    assert r.json()["month"] == month
    assert all(set(row) == {"rank", "nickname", "score"} for row in r.json()["entries"])


def test_daily_answers_open_after_the_day(site: httpx.Client, today: datetime) -> None:
    assert site.get(f"/api/daily/{today.date().isoformat()}/answers").status_code == 403
    r = site.get(f"/api/daily/{(today - timedelta(days=1)).date().isoformat()}/answers")
    if r.status_code == 404:
        pytest.skip("no daily challenge yesterday (the first day after go-live)")
    assert r.status_code == 200
    assert r.json()["words"]
