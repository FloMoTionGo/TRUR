"""E2E flows 2, 3, 4 and 9 (docs/test-strategy.md §5), plus the light/dark toggle."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

SEL = '[data-testid="{}"]'


def tile(page: Page, row: int, col: int):
    return page.locator(f'.tile[data-row="{row}"][data-col="{col}"]')


def test_grid_matches_the_words(game_page: Page) -> None:
    expect(game_page.locator(".tile")).to_have_count(36)
    expect(tile(game_page, 0, 0)).to_have_text("GS")  # column letter first, row letter second
    expect(tile(game_page, 4, 2)).to_have_text("RE")
    expect(game_page.locator(SEL.format("counter"))).to_have_text("Found 0 of 9")
    expect(game_page.locator(SEL.format("score"))).to_have_text("0")


def test_flow_2_play_a_word(game_page: Page) -> None:
    tile(game_page, 4, 0).click()  # GE
    tile(game_page, 5, 1).click()  # AR
    expect(tile(game_page, 4, 0)).to_have_attribute("data-state", "selected")
    expect(tile(game_page, 5, 1)).to_have_attribute("data-state", "selected")
    expect(game_page.locator("#preview")).to_have_text("→ GEAR")

    game_page.locator(SEL.format("submit")).click()

    expect(game_page.locator(SEL.format("found-list"))).to_contain_text("GEAR4")
    expect(game_page.locator(SEL.format("score"))).to_have_text("4")
    expect(game_page.locator(SEL.format("counter"))).to_have_text("Found 1 of 9")
    expect(game_page.locator('.tile[data-state="selected"]')).to_have_count(0)


def test_flow_3_undo_and_clear_send_nothing(game_page: Page, fake_api) -> None:
    for row, col in [(4, 0), (5, 1), (4, 5), (5, 1)]:  # GE, AR, NE, then AR again
        tile(game_page, row, col).click()
    expect(game_page.locator("#preview")).to_have_text("→ GENE")
    game_page.locator(SEL.format("clear")).click()
    expect(game_page.locator("#preview")).to_have_text("Click tiles to build a word")
    expect(game_page.locator('.tile[data-state="selected"]')).to_have_count(0)
    assert not any(call.endswith("/submit") for call in fake_api.calls)


def test_flow_4_rejected_word(game_page: Page) -> None:
    tile(game_page, 0, 0).click()  # GS
    tile(game_page, 0, 1).click()  # AS
    game_page.locator(SEL.format("submit")).click()

    expect(tile(game_page, 0, 0)).to_have_attribute("data-state", "rejected")
    expect(game_page.locator(SEL.format("toast"))).to_contain_text("GSAS isn’t in the dictionary")
    expect(game_page.locator(SEL.format("toast"))).to_contain_text("2 tries left")
    expect(tile(game_page, 0, 0)).to_have_attribute("data-state", "default")
    expect(game_page.locator(SEL.format("tries"))).to_have_attribute("aria-label", "2 of 3 tries left")
    expect(game_page.locator(".chip")).to_have_count(0)


def test_duplicate_shows_the_already_found_popup(game_page: Page) -> None:
    for _ in range(2):
        tile(game_page, 4, 0).click()
        tile(game_page, 5, 1).click()
        game_page.locator(SEL.format("submit")).click()
        expect(game_page.locator(".chip")).to_have_count(0)
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
