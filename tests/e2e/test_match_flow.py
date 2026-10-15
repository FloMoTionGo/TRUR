"""E2E flow 10 (docs/test-strategy.md §6, step M6): two browsers play a match on the real backend.

live_match_server shares its FakeClock with the test, so the fill window, the 3-2-1 and the end of play
pass only when the test advances the clock. Timer text isn't asserted: the fake clock and the browsers'
own clocks differ.

The board is read off the page. Each player's first free game draws from the same seeded rng as the match,
so the match board isn't GARDEN × SUMMER here; it is the same on both screens and on every run.
"""

import time

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.conftest import serve
from tests.e2e.conftest import register, watch_errors
from tests.e2e.devices import BY_ID
from trur import match
from trur.solver import Lexicon

pytestmark = pytest.mark.e2e

SEL = '[data-testid="{}"]'
FILL_S = 10  # Settings.match_fill_s
DESKTOP = {"viewport": {"width": 1000, "height": 800}, "timezone_id": "UTC", "locale": "en-US", "color_scheme": "light"}


def tiles_for(word1: str, word2: str, word: str) -> list[tuple[int, int]]:
    """The (row, col) tiles that spell `word`, each tile once."""
    pairs = [word[i:i + 2] for i in range(0, len(word), 2)]
    out: list[tuple[int, int]] = []

    def walk(i: int, used: frozenset) -> bool:
        if i == len(pairs):
            return True
        for row, b in enumerate(word2):
            for col, a in enumerate(word1):
                if a + b == pairs[i] and (row, col) not in used:
                    out.append((row, col))
                    if walk(i + 1, used | {(row, col)}):
                        return True
                    out.pop()
        return False

    assert walk(0, frozenset()), word
    return out


def tile(page: Page, row: int, col: int) -> Locator:
    return page.locator(f'#m-grid .tile[data-row="{row}"][data-col="{col}"]')


def pcts(page: Page) -> list[str]:
    return page.eval_on_selector_all("#m-grid .tile", "ts => ts.map((t) => `${t.dataset.row},${t.dataset.col}=${t.dataset.pct}`)")


def board(page: Page) -> tuple[str, str]:
    page.locator("#m-grid .tile").first.wait_for()
    across = "".join(page.locator("#m-grid .head-col").all_text_contents())
    down = "".join(page.locator("#m-grid .head:not(.head-col)").all_text_contents())
    return across, down


def flat(text: str) -> str:
    return " ".join(text.split())


def join(page: Page) -> None:
    """Press Multi and wait until the stream is open. The server subscribes it before it answers, and a
    queued player without a stream is dropped once the fake clock jumps past MATCH_STREAM_GONE_S."""
    with page.expect_response(lambda r: "/api/match/stream" in r.url and r.status == 200):
        page.locator(SEL.format("mode-match")).click()


@pytest.mark.parametrize("second", ["desktop", "se-bars"])
def test_flow_10_a_match_between_two_browsers(second: str, new_context, playwright, live_match_server, lex: Lexicon) -> None:
    server = live_match_server
    ann = new_context(**DESKTOP).new_page()
    bob = new_context(**(DESKTOP if second == "desktop" else BY_ID[second].context_args(playwright))).new_page()
    errors = {"Ann": watch_errors(ann), "Bob": watch_errors(bob)}
    streams: list[float] = []
    for page in (ann, bob):
        page.on("request", lambda r: streams.append(time.monotonic()) if "/api/match/stream" in r.url else None)
    for page, name in ((ann, "Ann"), (bob, "Bob")):
        page.goto(server.base_url + "/")
        register(page, name)

    # Both queue and see the fill countdown.
    join(ann)
    expect(ann.locator(SEL.format("lobby-text"))).to_contain_text("Waiting for a second player")
    join(bob)
    for page in (ann, bob):
        expect(page.locator(SEL.format("lobby-text"))).to_contain_text("2 players waiting")

    # The fill window ends: the players and 3-2-1, without a grid.
    server.clock.advance(FILL_S)
    for page in (ann, bob):
        expect(page.locator(SEL.format("lobby-title"))).to_have_text("Match found")
        expect(page.locator(f'{SEL.format("lobby-players")} li')).to_have_text(["Ann", "Bob"])
        expect(page.locator(SEL.format("lobby-count"))).to_be_visible()
        assert page.locator("#m-grid .tile").count() == 0

    # GO: the same board on both screens, every tile at 100 %.
    server.clock.advance(match.COUNTDOWN_S)
    word1, word2 = board(ann)
    assert board(bob) == (word1, word2)
    assert set(v.split("=")[1] for v in pcts(bob)) == {"100"}
    word = min(lex.solve(word1, word2), key=len)
    used = tiles_for(word1, word2, word)

    # Ann plays a word: Bob gets the popup, its tiles halve on both screens and lock for Ann only.
    for r, c in used:
        tile(ann, r, c).click()
    ann.locator(SEL.format("m-submit")).click()
    expect(bob.locator(SEL.format("m-popups"))).to_contain_text(f"Ann found a {len(word)}-letter word")
    for r, c in used:
        for page in (ann, bob):
            expect(tile(page, r, c)).to_have_attribute("data-pct", "50")
        expect(tile(ann, r, c)).to_have_attribute("data-locked", "true")
        expect(tile(bob, r, c)).to_have_attribute("data-locked", "false")
    expect(ann.locator(SEL.format("m-found-list"))).to_have_text(word)

    # Each sets a showcase word on fresh tiles: the other gets a popup, and no tile changes its value.
    free = [(r, c) for r in range(len(word2)) for c in range(len(word1)) if (r, c) not in used]
    shows = {"Ann": free[:2], "Bob": free[2:4]}
    spelled = {name: "".join(word1[c] + word2[r] for r, c in t) for name, t in shows.items()}
    for page, name, other in ((ann, "Ann", bob), (bob, "Bob", ann)):
        before = pcts(other)
        for r, c in shows[name]:
            tile(page, r, c).click()
        page.locator(SEL.format("m-showcase-button")).click()
        expect(other.locator(SEL.format("m-popups"))).to_contain_text(f"{name} set a showcase word")
        assert pcts(other) == before
        expect(page.locator(SEL.format("m-showcase"))).to_have_text(spelled[name])

    # Play ends: each votes on the other's word, shown without names; the vote ends once both have voted.
    server.clock.advance(match.DURATION_S + 1)
    for page, other in ((ann, "Bob"), (bob, "Ann")):
        expect(page.locator("#screen-vote")).to_be_visible()
        expect(page.locator("[data-candidate]")).to_have_text([spelled[other]])
        text = page.locator("#screen-vote").inner_text()
        assert "Ann" not in text and "Bob" not in text
    server.clock.advance(1)  # ballots count only after the close (§3.1), and the fake clock stood still
    for page in (ann, bob):
        page.locator("[data-candidate]").first.click()
        page.locator(SEL.format("vote-send")).click()
    for page in (ann, bob):
        expect(page.locator("#screen-results")).to_be_visible()
    ended = time.monotonic()

    # The same results on both screens, with each showcase word, its votes and the bonus.
    results = [flat(page.locator(SEL.format("res-players")).inner_text()) for page in (ann, bob)]
    assert results[0] == results[1]
    for name in ("Ann", "Bob"):
        assert f"Showcase {spelled[name]} · 5 vote points → +5" in results[0]
    assert word in results[0]

    # No stream is opened again after the end, and the results link shows the same results after a reload.
    ann.wait_for_timeout(5000)
    assert [t for t in streams if t > ended] == []
    assert "#match/" in ann.url
    ann.reload()
    expect(ann.locator("#screen-results")).to_be_visible()
    assert flat(ann.locator(SEL.format("res-players")).inner_text()) == results[0]
    assert errors == {"Ann": [], "Bob": []}


def test_restart_mid_match_shows_match_cancelled(
    new_context, live_match_server, db_path, clock, garden_seed, live_overrides, lex: Lexicon,
) -> None:
    """Restart detection (TS §6, step M5, built with stream.js in M6): a deploy ends every stream without
    an event. The page reconnects, gets 204 from the new server, finds itself idle and shows "Match
    cancelled", then stops asking for the stream."""
    server = live_match_server
    ann = new_context(**DESKTOP).new_page()
    bob = new_context(**DESKTOP).new_page()
    streams: list[float] = []
    for page, name in ((ann, "Ann"), (bob, "Bob")):
        page.on("request", lambda r: streams.append(time.monotonic()) if "/api/match/stream" in r.url else None)
        page.goto(server.base_url + "/")
        register(page, name)
        join(page)
    server.clock.advance(FILL_S)
    expect(ann.locator(SEL.format("lobby-title"))).to_have_text("Match found")
    server.clock.advance(match.COUNTDOWN_S)
    for page in (ann, bob):
        expect(page.locator("#screen-match")).to_be_visible()

    port = int(server.base_url.rsplit(":", 1)[1])
    server.stop()
    restarted = serve(db_path, clock, garden_seed, {"match_tick_ms": 10, **live_overrides}, port=port, timeout_graceful_shutdown=2)
    next(restarted)
    try:
        for page in (ann, bob):
            expect(page.locator(SEL.format("lobby-title"))).to_have_text("Match cancelled", timeout=20_000)
        settled = time.monotonic()
        ann.wait_for_timeout(5000)
        assert [t for t in streams if t > settled] == []
    finally:
        restarted.close()
