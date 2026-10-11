"""E2E flows (docs/test-strategy.md §5) on the live server; only flow 9 keeps its mocked API."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import register

pytestmark = pytest.mark.e2e

SEL = '[data-testid="{}"]'
GE, AR, NE, ER, DE = (4, 0), (5, 1), (4, 5), (5, 4), (4, 3)
WRONG = [[(0, 0), (0, 1)], [(0, 0), (1, 0)], [(0, 2), (0, 3)]]  # GSAS, GSGU, RSDS


def tile(page: Page, rc: tuple[int, int]):
    return page.locator(f'.tile[data-row="{rc[0]}"][data-col="{rc[1]}"]')


def play(page: Page, *tiles: tuple[int, int]) -> None:
    for rc in tiles:
        tile(page, rc).click()
    page.locator(SEL.format("submit")).click()
    expect(page.locator(".chip")).to_have_count(0)  # the selection clears after every submit


def test_flow_1_first_visit(page: Page, live_server, console_errors) -> None:
    page.goto(live_server.base_url + "/")
    expect(page.get_by_role("heading", name="Pick a nickname")).to_be_visible()
    register(page, "Tester")
    expect(page.locator(".tile")).to_have_count(36)
    expect(page.locator(".grid .head-col")).to_have_text(list("GARDEN"))
    expect(tile(page, (0, 0))).to_have_text("GS")  # column letter first, row letter second
    expect(page.locator(SEL.format("score"))).to_have_text("0")
    expect(page.locator(SEL.format("tries"))).to_have_attribute("aria-label", "3 of 3 tries left")
    expect(page.locator(SEL.format("counter"))).to_have_text("Found 0 of 9")
    assert console_errors == []


def test_flow_2_play_a_word(game_page: Page) -> None:
    tile(game_page, GE).click()
    tile(game_page, AR).click()
    expect(tile(game_page, GE)).to_have_attribute("data-state", "selected")
    expect(tile(game_page, AR)).to_have_attribute("data-state", "selected")
    expect(game_page.locator("#preview")).to_have_text("→ GEAR")

    game_page.locator(SEL.format("submit")).click()

    expect(game_page.locator(SEL.format("found-list"))).to_contain_text("GEAR4")
    expect(game_page.locator(SEL.format("score"))).to_have_text("4")
    expect(game_page.locator(SEL.format("counter"))).to_have_text("Found 1 of 9")
    expect(game_page.locator('.tile[data-state="selected"]')).to_have_count(0)


def test_flow_3_undo_and_clear_send_nothing(game_page: Page) -> None:
    api_calls: list[str] = []
    game_page.on("request", lambda r: api_calls.append(r.url) if "/api/" in r.url else None)
    for rc in (GE, AR, NE, AR):  # GE, AR, NE, then AR again
        tile(game_page, rc).click()
    expect(game_page.locator("#preview")).to_have_text("→ GENE")
    game_page.locator(SEL.format("clear")).click()
    expect(game_page.locator("#preview")).to_have_text("Click tiles to build a word")
    expect(game_page.locator('.tile[data-state="selected"]')).to_have_count(0)
    assert api_calls == []


def test_flow_4_rejected_word(game_page: Page) -> None:
    for rc in WRONG[0]:
        tile(game_page, rc).click()
    game_page.locator(SEL.format("submit")).click()

    expect(tile(game_page, (0, 0))).to_have_attribute("data-state", "rejected")
    expect(game_page.locator(SEL.format("toast"))).to_contain_text("GSAS isn’t in the dictionary")
    expect(game_page.locator(SEL.format("toast"))).to_contain_text("2 tries left")
    expect(tile(game_page, (0, 0))).to_have_attribute("data-state", "default")
    expect(game_page.locator(SEL.format("tries"))).to_have_attribute("aria-label", "2 of 3 tries left")
    expect(game_page.locator(".chip")).to_have_count(0)


def test_flow_5_game_over_lists_missed_words(game_page: Page) -> None:
    play(game_page, GE, AR)
    for tiles in WRONG:
        play(game_page, *tiles)
    expect(game_page.locator("#screen-over")).to_be_visible()
    expect(game_page.locator("#screen-game")).to_be_hidden()
    expect(game_page.locator(SEL.format("over-reason"))).to_have_text("Three wrong words · GARDEN × SUMMER")
    expect(game_page.locator(SEL.format("over-score"))).to_have_text("4")
    expect(game_page.locator(SEL.format("missed")).locator("li")).to_have_count(8)
    expect(game_page.locator(SEL.format("missed")).locator("li").first).to_have_text("DEARER9")


def test_flow_6_reload_resumes_the_game(game_page: Page) -> None:
    play(game_page, GE, AR)
    expect(game_page.locator(SEL.format("score"))).to_have_text("4")
    game_page.reload()
    game_page.locator(".tile").first.wait_for()
    expect(game_page.locator(".grid .head-col")).to_have_text(list("GARDEN"))
    expect(game_page.locator(SEL.format("found-list"))).to_contain_text("GEAR4")
    expect(game_page.locator(SEL.format("score"))).to_have_text("4")
    expect(game_page.locator(SEL.format("tries"))).to_have_attribute("aria-label", "3 of 3 tries left")


def test_end_then_new_game(game_page: Page) -> None:
    play(game_page, GE, AR)
    game_page.locator(SEL.format("end")).click()
    expect(game_page.locator(SEL.format("over-reason"))).to_have_text("You ended the game · GARDEN × SUMMER")
    expect(game_page.locator(SEL.format("over-found"))).to_have_text("GEAR4")
    expect(game_page.locator(SEL.format("missed")).locator("li")).to_have_count(8)
    game_page.locator(SEL.format("new-game")).click()
    expect(game_page.locator("#screen-game")).to_be_visible()
    expect(game_page.locator(SEL.format("score"))).to_have_text("0")
    expect(game_page.locator(SEL.format("counter"))).to_contain_text("Found 0 of")


def test_duplicate_shows_the_already_found_popup(game_page: Page) -> None:
    play(game_page, GE, AR)
    play(game_page, GE, AR)
    expect(game_page.locator(SEL.format("toast"))).to_contain_text("GEAR is already in your list")
    expect(game_page.locator(SEL.format("tries"))).to_have_attribute("aria-label", "3 of 3 tries left")


@pytest.mark.parametrize(("word1", "word2"), [("GREAT", "SUMMR"), ("ABCDEFGHIJ", "KLMNOPQRST")])
def test_flow_9_grids_fit_the_game_area(page: Page, live_server, fake_api, console_errors, word1, word2) -> None:
    fake_api.word1, fake_api.word2 = word1, word2
    page.set_viewport_size({"width": 1000, "height": 800})
    page.goto(live_server.base_url + "/")
    page.locator(".tile").first.wait_for()

    app = page.locator("#app").bounding_box()
    found = page.locator(".found").bounding_box()
    boxes = [t.bounding_box() for t in page.locator(".tile").all()]
    assert len(boxes) == len(word1) * len(word2)
    for b in boxes:
        assert app["x"] <= b["x"] and b["x"] + b["width"] + 3 <= found["x"]  # +3: the drop shadow
        assert app["y"] + 64 <= b["y"] and b["y"] + b["height"] + 3 <= app["y"] + 640 - 88
    for a, b in zip(boxes, boxes[1:]):
        if a["y"] == b["y"]:
            assert a["x"] + a["width"] <= b["x"]  # no overlap within a row
    assert console_errors == []


def test_theme_toggle_switches_and_is_remembered(game_page: Page) -> None:
    html = game_page.locator("html")
    toggle = game_page.locator(SEL.format("theme-toggle"))
    expect(html).not_to_have_class("is-dark")  # Chromium in tests reports a light system setting
    toggle.click()
    expect(html).to_have_class("is-dark")
    expect(html).to_have_attribute("data-theme", "dark")
    assert game_page.evaluate("getComputedStyle(document.body).getPropertyValue('--ground').trim()") == "#171A12"
    game_page.reload()
    expect(html).to_have_class("is-dark")
    toggle.click()
    expect(html).not_to_have_class("is-dark")
