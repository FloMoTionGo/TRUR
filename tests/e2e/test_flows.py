"""E2E flows (docs/test-strategy.md §5) on the live server; only flow 9 keeps its mocked API."""

import sqlite3

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
    """Undo and Clear are client-side: no game request. The batched event log is the only API traffic."""
    api_calls: list[str] = []
    game_page.on("request", lambda r: api_calls.append(r.url) if "/api/" in r.url and "/api/events" not in r.url else None)
    for rc in (GE, AR, NE, AR):  # GE, AR, NE, then AR again
        tile(game_page, rc).click()
    expect(game_page.locator("#preview")).to_have_text("→ GENE")
    game_page.locator(SEL.format("clear")).click()
    expect(game_page.locator("#preview")).to_have_text("Click tiles to build a word")
    expect(game_page.locator('.tile[data-state="selected"]')).to_have_count(0)
    assert api_calls == []


def test_tile_clicks_reach_the_event_log(game_page: Page, live_server) -> None:
    with game_page.expect_response("**/api/events") as info:
        tile(game_page, GE).click()
        tile(game_page, AR).click()
        game_page.locator(SEL.format("clear")).click()
    assert info.value.status == 204
    batch = info.value.request.post_data_json["events"]
    assert [e["type"] for e in batch] == ["tile_clicked", "tile_clicked", "selection_cleared"]
    assert batch[0]["selected"] is True and {"row", "col", "game_id", "client_ms"} <= set(batch[0])


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


# --- daily (T24): MEDICAL across, CORRECT down on the fake clock's 2026-10-11 ---

DEMOCRATIC = [(4, 2), (1, 0), (2, 4), (6, 5), (0, 3)]  # DE MO CR AT IC


def open_daily(page: Page) -> None:
    page.locator(SEL.format("mode-daily")).click()
    expect(page.locator(".grid .head-col")).to_have_text(list("MEDICAL"))
    expect(page.locator('.modes [data-mode="daily"]')).to_have_attribute("aria-current", "page")


def test_flow_7_daily_game(game_page: Page) -> None:
    open_daily(game_page)
    expect(game_page.locator(".grid .head:not(.head-col)")).to_have_text(list("CORRECT"))
    expect(game_page.locator(SEL.format("answers-note"))).to_contain_text("after midnight UTC")
    expect(game_page.locator(SEL.format("won"))).to_be_hidden()

    play(game_page, *DEMOCRATIC)
    won = game_page.locator(SEL.format("won"))
    expect(won).to_contain_text("Daily challenge won · Sun 11 Oct")
    expect(won).to_contain_text("You found DEMOCRATIC (10 letters)")
    expect(game_page.locator(SEL.format("score"))).to_have_text("25")
    expect(game_page.locator(".tile").first).to_be_enabled()  # play continues after the win

    game_page.locator(SEL.format("end")).click()
    expect(game_page.locator(SEL.format("over-score"))).to_have_text("25")
    expect(game_page.locator(SEL.format("missed"))).to_be_hidden()  # no missed words in daily
    expect(game_page.locator(SEL.format("over-answers-note"))).to_contain_text("after midnight UTC")
    expect(game_page.locator(SEL.format("new-game"))).to_have_text("Play free")


def test_daily_tab_again_shows_the_finished_game(game_page: Page) -> None:
    open_daily(game_page)
    game_page.locator(SEL.format("end")).click()
    expect(game_page.locator(SEL.format("over-score"))).to_be_visible()
    game_page.locator('.modes [data-mode="free"]').click()
    expect(game_page.locator(".grid .head-col")).to_have_text(list("GARDEN"))
    game_page.locator(SEL.format("mode-daily")).click()
    expect(game_page.locator(SEL.format("over-answers-note"))).to_be_visible()  # one daily game a day


def test_past_answers_before_midnight_show_yesterday(game_page: Page) -> None:
    open_daily(game_page)
    game_page.locator(SEL.format("past-answers")).click()
    expect(game_page.locator(SEL.format("answers-date"))).to_have_text("Sat 10 Oct 2026")
    expect(game_page.locator(SEL.format("answers-meta"))).to_contain_text("STRONG × MEANING")
    expect(game_page.locator(SEL.format("answers-list")).locator("li").first).to_have_text("GENERATION25")
    expect(game_page.locator(SEL.format("answers-next"))).to_be_disabled()  # today's answers aren't out
    expect(game_page.locator(SEL.format("answers-you"))).to_be_hidden()  # no daily game that day
    game_page.locator(SEL.format("answers-back")).click()
    expect(game_page.locator(".grid .head-col")).to_have_text(list("MEDICAL"))


def test_past_answers_after_midnight_mark_your_finds(game_page: Page, live_server) -> None:
    open_daily(game_page)
    play(game_page, *DEMOCRATIC)
    game_page.locator(SEL.format("end")).click()
    expect(game_page.locator(SEL.format("over-score"))).to_have_text("25")
    live_server.clock.advance(12 * 3600)  # 2026-10-12T00:00Z: yesterday's answers are out

    game_page.locator(SEL.format("past-answers-over")).click()
    expect(game_page.locator(SEL.format("answers-date"))).to_have_text("Sun 11 Oct 2026")
    expect(game_page.locator(SEL.format("answers-meta"))).to_contain_text("MEDICAL × CORRECT")
    expect(game_page.locator(SEL.format("answers-you"))).to_have_text("You: 1 word · 25")
    mine = game_page.locator(f'{SEL.format("answers-list")} li[data-mine="true"]')
    expect(mine).to_have_text(["DEMOCRATIC25"])
    expect(game_page.locator(SEL.format("answers-next"))).to_be_disabled()

    game_page.locator(SEL.format("answers-prev")).click()
    expect(game_page.locator(SEL.format("answers-date"))).to_have_text("Sat 10 Oct 2026")
    expect(game_page.locator(SEL.format("answers-you"))).to_be_hidden()
    game_page.locator(SEL.format("answers-prev")).click()
    expect(game_page.locator(SEL.format("answers-empty"))).to_have_text("There was no daily challenge on that day.")
    game_page.locator(SEL.format("answers-back")).click()
    expect(game_page.locator(SEL.format("over-score"))).to_have_text("25")  # back where it came from


# --- leaderboards ---

XSS = "<img src=x onerror=window.__x=1>"


def board_rows(page: Page) -> list[str]:
    return page.locator(f'{SEL.format("boards-rows")} tr').all_inner_texts()


def test_boards_show_your_game_and_go_back(game_page: Page) -> None:
    play(game_page, GE, AR)
    game_page.locator(SEL.format("end")).click()
    expect(game_page.locator(SEL.format("over-score"))).to_have_text("4")

    game_page.locator(SEL.format("boards-open")).click()
    expect(game_page.get_by_role("heading", name="Leaderboards")).to_be_visible()
    expect(game_page.get_by_role("tab", name="Free · month")).to_have_attribute("aria-selected", "true")
    expect(game_page.locator(SEL.format("boards-period"))).to_have_text("October 2026")
    mine = game_page.locator(f'{SEL.format("boards-rows")} tr[data-me="true"]')
    expect(mine.locator("td")).to_have_text(["1", "Tester (you)", "4"])
    expect(game_page.locator(SEL.format("boards-next"))).to_be_disabled()  # no boards from the future

    game_page.locator(SEL.format("boards-prev")).click()
    expect(game_page.locator(SEL.format("boards-period"))).to_have_text("September 2026")
    expect(game_page.locator(SEL.format("boards-empty"))).to_have_text("No scores yet.")
    game_page.locator(SEL.format("boards-next")).click()
    expect(mine).to_have_count(1)

    game_page.get_by_role("tab", name="Today’s daily").click()
    expect(game_page.locator(SEL.format("boards-period"))).to_have_text("Sun 11 Oct 2026")
    expect(game_page.locator(SEL.format("boards-empty"))).to_be_visible()
    game_page.get_by_role("tab", name="Daily · month").click()
    expect(game_page.locator(SEL.format("boards-period"))).to_have_text("October 2026")

    game_page.locator(SEL.format("boards-back")).click()
    expect(game_page.locator(SEL.format("over-score"))).to_have_text("4")  # back where it came from


def test_flow_8_nickname_shows_as_text_on_the_board(game_page: Page, live_server) -> None:
    conn = sqlite3.connect(live_server.app.state.settings.db_path)
    with conn:
        conn.execute(
            "INSERT INTO users (id, token_hash, nickname, nickname_key, created_at) VALUES (?, ?, ?, ?, ?)",
            ("xss-user", "0" * 64, XSS, XSS.lower(), "2026-10-11T00:00:00.000000Z"),
        )
        conn.execute("INSERT INTO rankings VALUES ('xss-user', 'free', '2026-10', 99)")
    conn.close()
    game_page.locator(SEL.format("boards-open")).click()
    cells = game_page.locator(f'{SEL.format("boards-rows")} tr').first.locator("td")
    expect(cells).to_have_text(["1", XSS, "99"])
    expect(game_page.locator("#screen-boards img")).to_have_count(0)
    assert game_page.evaluate("window.__x") is None
