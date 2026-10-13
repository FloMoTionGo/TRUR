"""Screen recipes shared by the desktop baseline (test_layout.py) and the shots helper (scripts/mobile_shots.py).

Each State configures a FakeApi, acts on the loaded page, and names what shows once the screen is reached.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from playwright.sync_api import Page, expect

from tests.e2e.conftest import FakeApi, long_board, long_words
from trur.solver import Lexicon

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)  # the FakeClock start, a Sunday
SEL = '[data-testid="{}"]'
GE, AR = (4, 0), (5, 1)
GSAS = [(0, 0), (0, 1)]
FRAMES = "() => document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))"


def tap(page: Page, *tiles: tuple[int, int]) -> None:
    for row, col in tiles:
        page.locator(f'.tile[data-row="{row}"][data-col="{col}"]').click()


def submit_paused(page: Page, *tiles: tuple[int, int]) -> None:
    """Submit with the page clock stopped, so the toast stays up; the rejected flash (420 ms) runs out."""
    page.clock.pause_at(NOW + timedelta(minutes=1))
    tap(page, *tiles)
    page.locator(SEL.format("submit")).click()
    expect(page.locator(SEL.format("toast"))).to_be_visible()
    page.clock.run_for(1000)
    expect(page.locator('.tile[data-state="rejected"]')).to_have_count(0)


def daily(api: FakeApi, over: bool = False) -> None:
    """Today's daily, MEDICAL × CORRECT, won with DEMOCRATIC."""
    api.mode, api.daily_date, api.word1, api.word2 = "daily", "2026-10-11", "MEDICAL", "CORRECT"
    api.found, api.won, api.over = ["DEMOCRATIC"], True, over


def over_free(api: FakeApi) -> None:
    api.found, api.over = ["GEAR"], True
    api.words = [*api.answers, *long_words(22)]  # 30 missed words


def answers(api: FakeApi) -> None:
    daily(api, over=True)
    api.words = ["DEMOCRATIC", *long_words(39)]


def nothing(_: object) -> None:
    pass


@dataclass(frozen=True)
class State:
    ready: str                                    # shown once the state is reached
    setup: Callable[[FakeApi], None] = nothing
    act: Callable[[Page], None] = nothing
    paused: bool = False                          # the page clock is stopped


def shape(word1: str, word2: str) -> Callable[[FakeApi], None]:
    def setup(api: FakeApi) -> None:
        api.word1, api.word2 = word1, word2
    return setup


STATES = {
    "nickname": State("#nick", setup=lambda api: setattr(api, "nickname", None)),
    "game-6x6": State(".tile"),
    "selected": State('.tile[data-state="selected"]', act=lambda page: tap(page, GE, AR)),
    "rejected-toast": State(SEL.format("toast"), act=lambda page: submit_paused(page, *GSAS), paused=True),
    "duplicate-toast": State(SEL.format("toast"), setup=lambda api: api.found.append("GEAR"),
                             act=lambda page: submit_paused(page, GE, AR), paused=True),
    "game-5x5": State(".tile", setup=shape("GREAT", "SUMMR")),
    "game-10x10": State(".tile", setup=shape("ABCDEFGHIJ", "KLMNOPQRST")),
    "daily-won": State(SEL.format("won"), setup=daily),
    "over-free": State(f'{SEL.format("missed")} li', setup=over_free),
    "over-daily": State(SEL.format("over-answers-note"), setup=lambda api: daily(api, over=True)),
    "answers": State(f'{SEL.format("answers-list")} li', setup=answers,
                     act=lambda page: page.locator(SEL.format("past-answers-over")).click()),
    "boards": State(f'{SEL.format("boards-rows")} tr', setup=lambda api: setattr(api, "board", long_board(25)),
                    act=lambda page: page.locator(SEL.format("boards-open")).click()),
}


def reach(page: Page, base_url: str, lex: Lexicon, state: State) -> FakeApi:
    """Load the page against a FakeApi set up for `state`, act, and wait until the state shows and settles."""
    api = FakeApi(lex)
    state.setup(api)
    page.route("**/api/**", api.handle)
    page.clock.install(time=NOW)
    page.goto(base_url + "/")
    expect(page.locator("#screen-loading")).to_be_hidden()
    state.act(page)
    page.locator(state.ready).first.wait_for()
    if state.paused:  # the page clock is stopped, so frames come from run_for
        page.evaluate("() => document.fonts.ready.then(() => 0)")
        page.clock.run_for(50)
    else:
        page.evaluate(FRAMES)
    return api


# Screens that exist on phones only (the shots helper; P9 designs them).
PHONE_STATES = {
    "found-sheet": State("#sheet .found-list li", setup=lambda api: api.found.extend(["GEAR", "DEARER"]),
                         act=lambda page: page.locator(SEL.format("found-open")).click()),
}
