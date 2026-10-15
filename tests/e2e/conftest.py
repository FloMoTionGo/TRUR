"""Browser flows against the real page with a mocked API (docs/test-strategy.md §5, step 2).

The page is served by the live_server fixture; every /api/ request is answered by FakeApi through
page.route, so these flows don't depend on the backend's state.
"""

import json
import re
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import ConsoleMessage, Page, Route

from tests.e2e.devices import Device, selected
from trur.solver import Lexicon, points, word_from_tiles


def by_points(words) -> list[str]:
    """The server's answer order: points, then A–Z."""
    return sorted(words, key=lambda w: (-points(w), w))


def long_words(n: int) -> list[str]:
    """n distinct made-up answers of 4–12 letters, for long answer and missed-word lists."""
    letters = "AEIORSTLNDMC"
    return [
        "".join(letters[(i + 5 * k) % len(letters)] for k in range(4 + 2 * (i % 5))) + letters[i // len(letters) % len(letters)] * 2
        for i in range(n)
    ]


def long_board(n: int) -> list[tuple[str, int]]:
    """n ranking rows, best first."""
    return [(f"Player {i + 1}", 400 - 7 * i) for i in range(n)]


@dataclass
class FakeApi:
    lex: Lexicon
    word1: str = "GARDEN"
    word2: str = "SUMMER"
    found: list[str] = field(default_factory=list)
    tries: int = 3
    over: bool = False
    mode: str = "free"
    daily_date: str | None = None    # set with mode="daily"
    won: bool = False
    win_on_accept: bool = False      # the next accepted word wins the daily
    words: list[str] | None = None   # the answers; None: the solver's answers for the pair
    nickname: str | None = "Tester"  # None: no player yet, so /api/users/me answers 401
    board: list[tuple[str, int]] = field(default_factory=list)  # (nickname, score), best first
    calls: list[str] = field(default_factory=list)

    @property
    def answers(self) -> tuple[str, ...]:
        return tuple(self.words) if self.words is not None else self.lex.solve(self.word1, self.word2)

    def state(self) -> dict:
        score = sum(points(w) for w in self.found)
        missed = by_points(w for w in self.answers if w not in self.found) if self.over and self.mode == "free" else None
        return {
            "game_id": "00000000-0000-4000-8000-000000000001", "mode": self.mode, "daily_date": self.daily_date,
            "word1": self.word1, "word2": self.word2, "tries_left": self.tries,
            "total_words": len(self.answers), "found": [{"word": w, "points": points(w)} for w in self.found],
            "found_count": len(self.found), "score": score, "won": self.won, "game_over": self.over,
            "missed_words": missed,
        }

    def entries(self) -> list[dict]:
        return [{"rank": i + 1, "nickname": n, "score": s} for i, (n, s) in enumerate(self.board)]

    def submit(self, tiles: list[list[int]]) -> dict:
        word = word_from_tiles(self.word1, self.word2, [tuple(t) for t in tiles])
        if word in self.found:
            status = "duplicate"
        elif word in self.answers:
            status = "accepted"
            self.found.append(word)
            self.won = self.won or self.win_on_accept
        else:
            status = "rejected"
            self.tries -= 1
        self.over = self.tries == 0 or len(self.found) == len(self.answers)
        s = self.state()
        return {"status": status, "word": word, "points": points(word) if status == "accepted" else 0,
                **{k: s[k] for k in ("score", "tries_left", "found_count", "total_words", "won", "game_over", "missed_words")}}

    def handle(self, route: Route) -> None:
        request = route.request
        path = re.sub(r"^https?://[^/]+", "", request.url).split("?")[0]
        self.calls.append(f"{request.method} {path}")
        if request.method == "GET" and path == "/api/users/me":
            if self.nickname is None:
                return route.fulfill(status=401, json={"error": {"code": "not_authenticated", "message": "No player."}})
            return route.fulfill(json={"nickname": self.nickname})
        if request.method == "POST" and path == "/api/users":
            self.nickname = json.loads(request.post_data)["nickname"]
            return route.fulfill(status=201, json={"nickname": self.nickname})
        if request.method == "GET" and path == "/api/games/current":
            return route.fulfill(json={"game": None})
        if request.method == "GET" and path == "/api/match/current":  # boot asks, to resume a match after a reload
            return route.fulfill(json={"state": "idle", "queue": None, "match": None})
        if request.method == "POST" and path == "/api/games":
            return route.fulfill(status=201, json=self.state())
        if request.method == "POST" and path.endswith("/submit"):
            return route.fulfill(json=self.submit(json.loads(request.post_data)["tiles"]))
        if request.method == "POST" and path.endswith("/end"):
            self.over = True
            return route.fulfill(json=self.state())
        if request.method == "GET" and (m := re.fullmatch(r"/api/daily/([\d-]+)/answers", path)):
            words = [{"word": w, "points": points(w)} for w in by_points(self.answers)]
            return route.fulfill(json={
                "date": m[1], "word1": self.word1, "word2": self.word2, "total_words": len(words), "words": words,
                "found": list(self.found) if self.mode == "daily" else None,
            })
        if request.method == "GET" and path == "/api/rankings":
            return route.fulfill(json={"mode": "free", "month": "2026-10", "entries": self.entries()})
        if request.method == "GET" and path == "/api/rankings/daily":
            return route.fulfill(json={"date": "2026-10-11", "entries": self.entries()})
        if request.method == "POST" and path == "/api/events":
            return route.fulfill(status=204)
        route.fulfill(status=404, json={"error": {"code": "not_found", "message": "Not found."}})


@pytest.fixture
def console_errors(page: Page) -> list[str]:
    """Console errors (including CSP violations) and uncaught exceptions; each flow asserts none."""
    return watch_errors(page)


def watch_errors(page: Page) -> list[str]:
    """Collect console errors and uncaught exceptions on any page; the caller asserts the list is empty."""
    errors: list[str] = []

    def on_console(msg: ConsoleMessage) -> None:
        url = msg.location.get("url", "")
        # By design: a first visit has no player yet (401 on /me); past answers answer 403 before
        # midnight UTC and 404 for a day without a challenge, and the screen shows that as text.
        expected_401 = "status of 401" in msg.text and url.endswith("/api/users/me")
        expected_answers = re.search(r"status of 40[34]\b", msg.text) and re.search(r"/api/daily/[^/]+/answers$", url)
        if msg.type == "error" and not expected_401 and not expected_answers:
            errors.append(msg.text)

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    # "ResizeObserver loop" errors reach only window's error event, never the console (TS §7, L8).
    page.add_init_script(
        "addEventListener('error', (e) => { if (/ResizeObserver/.test(e.message)) console.error(e.message); });"
    )
    return errors


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """A test that takes `phone_device` runs once per selected portrait device (--devices; landscape from P14)."""
    if "phone_device" in metafunc.fixturenames:
        devices = [d for d in selected(metafunc.config) if d.portrait]
        metafunc.parametrize("phone_device", devices, ids=[d.id for d in devices])


@pytest.fixture
def phone(phone_device: Device, new_context, playwright) -> Page:
    """A page in a context for `phone_device`; console errors and ResizeObserver loops fail the test."""
    page = new_context(**phone_device.context_args(playwright)).new_page()
    errors = watch_errors(page)
    yield page
    assert errors == []


@pytest.fixture
def fake_api(page: Page, lex: Lexicon) -> FakeApi:
    api = FakeApi(lex)
    page.route("**/api/**", api.handle)
    return api


def register(page: Page, nickname: str = "Tester") -> None:
    """Flow 1's first step: the nickname prompt, then the first free game."""
    page.locator("#nick").fill(nickname)
    page.get_by_role("button", name="Play").click()
    page.locator('[data-testid="grid"] .tile').first.wait_for()


@pytest.fixture
def game_page(page: Page, live_server, console_errors: list[str]) -> Page:
    """A registered player in their first free game (GARDEN × SUMMER) on the real backend."""
    page.set_viewport_size({"width": 1000, "height": 800})
    page.goto(live_server.base_url + "/")
    register(page)
    yield page
    assert console_errors == []
