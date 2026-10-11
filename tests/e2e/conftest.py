"""Browser flows against the real page with a mocked API (docs/test-strategy.md §5, step 2).

The page is served by the live_server fixture; every /api/ request is answered by FakeApi through
page.route, so these flows don't depend on the backend's state.
"""

import json
import re
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import ConsoleMessage, Page, Route

from trur.solver import Lexicon, points, word_from_tiles


@dataclass
class FakeApi:
    lex: Lexicon
    word1: str = "GARDEN"
    word2: str = "SUMMER"
    found: list[str] = field(default_factory=list)
    tries: int = 3
    over: bool = False
    calls: list[str] = field(default_factory=list)

    @property
    def answers(self) -> tuple[str, ...]:
        return self.lex.solve(self.word1, self.word2)

    def state(self) -> dict:
        score = sum(points(w) for w in self.found)
        return {
            "game_id": "00000000-0000-4000-8000-000000000001", "mode": "free", "daily_date": None,
            "word1": self.word1, "word2": self.word2, "tries_left": self.tries,
            "total_words": len(self.answers), "found": [{"word": w, "points": points(w)} for w in self.found],
            "found_count": len(self.found), "score": score, "won": False, "game_over": self.over, "missed_words": None,
        }

    def submit(self, tiles: list[list[int]]) -> dict:
        word = word_from_tiles(self.word1, self.word2, [tuple(t) for t in tiles])
        if word in self.found:
            status = "duplicate"
        elif word in self.answers:
            status = "accepted"
            self.found.append(word)
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
            return route.fulfill(json={"nickname": "Tester"})
        if request.method == "GET" and path == "/api/games/current":
            return route.fulfill(json={"game": None})
        if request.method == "POST" and path == "/api/games":
            return route.fulfill(status=201, json=self.state())
        if request.method == "POST" and path.endswith("/submit"):
            return route.fulfill(json=self.submit(json.loads(request.post_data)["tiles"]))
        if request.method == "POST" and path == "/api/events":
            return route.fulfill(status=204)
        route.fulfill(status=404, json={"error": {"code": "not_found", "message": "Not found."}})


@pytest.fixture
def console_errors(page: Page) -> list[str]:
    """Console errors (including CSP violations) and uncaught exceptions; each flow asserts none."""
    errors: list[str] = []

    def on_console(msg: ConsoleMessage) -> None:
        expected_401 = "status of 401" in msg.text and msg.location.get("url", "").endswith("/api/users/me")
        if msg.type == "error" and not expected_401:  # a first visit has no player yet: that 401 is by design
            errors.append(msg.text)

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


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
