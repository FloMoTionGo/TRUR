"""Phones (docs/test-strategy.md §7): cases per P task, on the --devices set (portrait only until P14)."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import FakeApi, long_words, watch_errors
from tests.e2e.devices import BY_ID
from tests.e2e.layout import l1, l2, l4, l6, l7, l8, pitch, press, snapshot
from tests.e2e.test_layout import BASELINES, GEOMETRY, decode_geometry
from trur.solver import Lexicon

pytestmark = [pytest.mark.e2e, pytest.mark.mobile]

SEL = '[data-testid="{}"]'


def open_page(phone: Page, live_server, lex: Lexicon, ready: str = ".tile", **fields) -> FakeApi:
    """Load the page against a FakeApi with the given fields and wait until `ready` shows."""
    api = FakeApi(lex)
    for name, value in fields.items():
        setattr(api, name, value)
    phone.route("**/api/**", api.handle)
    phone.goto(live_server.base_url + "/")
    phone.locator(ready).first.wait_for()
    return api


def tile(page: Page, row: int, col: int):
    return page.locator(f'.tile[data-row="{row}"][data-col="{col}"]')


# --- P3: page shell ---

def test_p3_2_theme_color_follows_the_toggle(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    meta = phone.locator('meta[name="theme-color"]')
    toggle = phone.locator(SEL.format("theme-toggle"))
    seen = []
    for _ in range(3):  # light → dark → light
        ground = phone.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ground').trim()")
        assert meta.get_attribute("content") == ground
        seen.append(ground)
        toggle.dispatch_event("click")  # the header fits phones from P4 on
    assert seen == ["#FFFFFF", "#171A12", "#FFFFFF"]


@pytest.mark.parametrize("screen", ["game", "nickname"])
def test_p3_3_app_fills_the_viewport(phone: Page, live_server, lex: Lexicon, phone_device, screen: str) -> None:
    if screen == "nickname":
        open_page(phone, live_server, lex, ready="#nick", nickname=None)
    else:
        open_page(phone, live_server, lex)
    box = phone.locator("#app").bounding_box()
    want = {"x": 0, "y": 0, "width": phone_device.width, "height": phone_device.height}
    assert all(abs(box[k] - v) <= 0.5 for k, v in want.items()), box
    assert l1(snapshot(phone)) == []


def test_p3_4_double_tap_toggles_and_never_zooms(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    target = next(c for c in snapshot(phone)["controls"] if c["tile"] and c["hit"])  # the grid fits phones from P6 on
    t = tile(phone, target["row"], target["col"])
    press(t)
    phone.wait_for_timeout(150)
    press(t)
    expect(t).to_have_attribute("data-state", "default")
    assert phone.evaluate("visualViewport.scale") == 1


def test_p3_5_touch_styles(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    styles = phone.evaluate("""() => {
      const cs = (sel) => getComputedStyle(document.querySelector(sel));
      return {
        touchAction: cs("html").touchAction,
        tapHighlight: cs("html").webkitTapHighlightColor,
        userSelect: cs(".grid").userSelect,
        overscroll: [".found-list", ".over-grid", ".answers-list", ".boards-scroll"].map((s) => cs(s).overscrollBehaviorY),
      };
    }""")
    assert styles == {
        "touchAction": "manipulation",
        "tapHighlight": "rgba(0, 0, 0, 0)",
        "userSelect": "none",
        "overscroll": ["contain"] * 4,
    }


# --- P4: header and status strip ---

HEADER = """() => {
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const visible = (el) => el.checkVisibility();
  const bar = document.querySelector(".bar");
  const row2 = [...bar.querySelectorAll(":scope > .stat, :scope > .found-open, :scope > .bar-title")].filter(visible);
  const controls = [...bar.querySelectorAll("button")].filter(visible).map((el) => {
    const r = el.getBoundingClientRect();
    const at = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
    return { name: el.dataset.testid || el.textContent.trim(), rect: rect(el), hit: Boolean(at) && el.contains(at) };
  });
  const open = document.querySelector('[data-testid="found-open"]');
  const reach = (dy) => {
    const r = open.getBoundingClientRect();
    const y = dy < 0 ? r.top + dy : r.bottom + dy;
    return document.elementFromPoint((r.left + r.right) / 2, y) === open;
  };
  return { bar: rect(bar), row1: [".brand", ".bar-end"].map((s) => rect(bar.querySelector(s))),
           row2: row2.map((el) => ({ name: el.dataset.testid || el.className, rect: rect(el) })),
           controls, openReach: visible(open) ? [reach(-5), reach(5)] : null };
}"""


def visible_testid_counts(page: Page) -> dict[str, int]:
    return page.evaluate("""() => {
      const n = {};
      for (const el of document.querySelectorAll("[data-testid]")) if (el.checkVisibility()) n[el.dataset.testid] = (n[el.dataset.testid] || 0) + 1;
      return n;
    }""")


def to_answers(phone: Page) -> None:
    phone.locator(SEL.format("past-answers-over")).dispatch_event("click")  # the game-over screen fits phones from P11
    phone.locator(f'{SEL.format("answers-list")} li').first.wait_for()


def to_boards(phone: Page) -> None:
    press(phone.locator(SEL.format("boards-open")))
    phone.locator(SEL.format("boards-period")).filter(has_text="October").wait_for()


def daily_over(api_fields: dict) -> dict:
    return {**api_fields, "mode": "daily", "daily_date": "2026-10-11", "word1": "MEDICAL", "word2": "CORRECT",
            "found": ["DEMOCRATIC"], "won": True, "over": True}


@pytest.mark.parametrize("screen", ["game", "answers", "boards"])
def test_p4_1_header_rows(phone: Page, live_server, lex: Lexicon, screen: str) -> None:
    if screen == "answers":
        open_page(phone, live_server, lex, ready=SEL.format("over-score"), **daily_over({}))
        to_answers(phone)
    else:
        open_page(phone, live_server, lex)
        if screen == "boards":
            to_boards(phone)
    h = phone.evaluate(HEADER)
    top = h["bar"][1]
    for left, row_top, right, bottom in h["row1"]:
        assert abs(row_top - top) <= 0.5 and abs(bottom - row_top - 48) <= 0.5
    assert h["row2"], "row 2 is empty"
    for item in h["row2"]:
        r = item["rect"]
        assert abs(r[1] - (top + 48)) <= 0.5 and abs(r[3] - r[1] - 32) <= 0.5, item


@pytest.mark.parametrize("screen", ["game", "answers", "boards"])
def test_p4_2_header_controls_are_big_enough_and_uncovered(phone: Page, live_server, lex: Lexicon, screen: str) -> None:
    if screen == "answers":
        open_page(phone, live_server, lex, ready=SEL.format("over-score"), **daily_over({}))
        to_answers(phone)
    else:
        open_page(phone, live_server, lex)
        if screen == "boards":
            to_boards(phone)
    h = phone.evaluate(HEADER)
    for c in h["controls"]:
        left, top, right, bottom = c["rect"]
        assert c["hit"], f"{c['name']} is covered"
        if c["name"] == "found-open":  # 32 px in the strip; ::after extends the hit area to 44
            assert h["openReach"] == [True, True]
        else:
            assert right - left >= 44 - 0.5 and bottom - top >= 44 - 0.5, c
        assert left >= -0.5 and right <= phone.viewport_size["width"] + 0.5, f"{c['name']} is clipped"


def test_p4_3_wordmark_from_400_px(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex)
    wordmark = phone.locator(".wordmark")
    assert wordmark.is_visible() == (phone_device.width >= 400)
    for width, shown in ((399, False), (400, True)):
        phone.set_viewport_size({"width": width, "height": phone_device.height})
        assert wordmark.is_visible() == shown, width


def test_p4_4_status_strip(phone: Page, live_server, lex: Lexicon) -> None:
    api = open_page(phone, live_server, lex, found=["GEAR"])
    expect(phone.locator(SEL.format("found-open"))).to_have_text(f"Found 1 of {len(api.answers)}")
    row2 = {item["name"] for item in phone.evaluate(HEADER)["row2"]}
    assert row2 == {"stat", "found-open"}
    for testid in ("score", "tries", "found-open"):
        expect(phone.locator(SEL.format(testid))).to_be_visible()
    assert all(n == 1 for n in visible_testid_counts(phone).values()), visible_testid_counts(phone)


@pytest.mark.parametrize("screen", ["answers", "boards"])
def test_p4_5_back_in_row_1_and_title_in_row_2(phone: Page, live_server, lex: Lexicon, screen: str) -> None:
    if screen == "answers":
        open_page(phone, live_server, lex, ready=SEL.format("over-score"), **daily_over({}))
        to_answers(phone)
    else:
        open_page(phone, live_server, lex)
        to_boards(phone)
    h = phone.evaluate(HEADER)
    top = h["bar"][1]
    back = next(c for c in h["controls"] if c["name"] == f"{screen}-back")
    assert back["rect"][3] <= top + 48 + 0.5
    assert [item["name"] for item in h["row2"]] == ["bar-title"]
    rects = [c["rect"] for c in h["controls"]] + [item["rect"] for item in h["row2"]]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            overlap_x = min(a[2], b[2]) - max(a[0], b[0])
            overlap_y = min(a[3], b[3]) - max(a[1], b[1])
            assert not (overlap_x > 0.5 and overlap_y > 0.5), (a, b)


@pytest.mark.parametrize("size", [(1000, 800), (640, 640)])
def test_p4_4_one_visible_element_per_testid_on_desktop(page: Page, live_server, fake_api, size) -> None:
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(live_server.base_url + "/")
    page.locator(".tile").first.wait_for()
    expect(page.locator(SEL.format("found-open"))).to_be_hidden()
    assert all(n == 1 for n in visible_testid_counts(page).values())


# --- P5: found sheet ---

GE, AR = (4, 0), (5, 1)


def open_sheet(phone: Page) -> None:
    press(phone.locator(SEL.format("found-open")))
    expect(phone.locator("dialog#sheet")).to_have_attribute("open", "")
    phone.wait_for_function("() => document.getElementById('sheet').getAnimations().length === 0")  # the slide-in; a bare string would need unsafe-eval


def api_calls(phone: Page) -> list[str]:
    calls: list[str] = []
    phone.on("request", lambda r: calls.append(r.url) if "/api/" in r.url and "/api/events" not in r.url else None)
    return calls


def test_p5_1_closed_sheet_is_hidden(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    expect(phone.locator("#sheet")).to_be_hidden()
    expect(phone.locator(".found")).to_be_hidden()  # the side panel lives in the sheet on phones


def test_p5_2_found_opens_a_docked_modal_sheet(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex)
    open_sheet(phone)
    assert phone.evaluate("document.getElementById('sheet').matches(':modal')")
    s = snapshot(phone, sheet="#sheet")
    assert l7(s, "sheet") == []
    top, bottom = s["named"]["sheet"]["rect"][1], s["named"]["sheet"]["rect"][3]
    assert bottom - top <= 0.6 * phone_device.height + 0.5
    assert phone.evaluate("document.activeElement.dataset.testid") == "sheet-close"
    expect(phone.locator("#sheet .found " + SEL.format("counter"))).to_be_visible()


def test_p5_3_long_list_scrolls_inside_the_sheet(phone: Page, live_server, lex: Lexicon) -> None:
    words = long_words(40)
    open_page(phone, live_server, lex, words=words, found=list(words))
    open_sheet(phone)
    scroll = phone.evaluate("""() => { const list = document.getElementById('found-list');
      const before = list.scrollHeight > list.clientHeight;
      list.scrollTop = list.scrollHeight;
      return [before, window.scrollY]; }""")
    assert scroll == [True, 0]
    last = phone.locator("#found-list li").last
    assert phone.evaluate("""(el) => { const r = el.getBoundingClientRect();
      const at = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
      return Boolean(at) && el.contains(at); }""", last.element_handle())


def test_p5_4_the_background_is_inert(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    open_sheet(phone)
    assert any(c["tile"] and not c["hit"] for c in snapshot(phone)["controls"])
    tiles_hit = [c for c in snapshot(phone)["controls"] if c["tile"] and c["hit"]]
    assert tiles_hit == []
    for _ in range(30):
        phone.keyboard.press("Tab")
        assert phone.evaluate("""() => { const a = document.activeElement;
          return a === document.body || document.getElementById('sheet').contains(a); }""")


@pytest.mark.parametrize("how", ["button", "escape", "backdrop"])
def test_p5_5_closing_returns_focus_without_click_through(phone: Page, live_server, lex: Lexicon, how: str) -> None:
    open_page(phone, live_server, lex)
    open_sheet(phone)
    calls = api_calls(phone)
    if how == "button":
        press(phone.locator(SEL.format("sheet-close")))
    elif how == "escape":
        phone.keyboard.press("Escape")
    else:
        phone.touchscreen.tap(phone.viewport_size["width"] / 2, 8)
    expect(phone.locator("#sheet")).to_be_hidden()
    assert phone.evaluate("document.activeElement.dataset.testid") == "found-open"
    expect(phone.locator('.tile[data-state="selected"]')).to_have_count(0)
    expect(phone.locator(".game-main > .found")).to_have_count(1)  # the panel is home again
    assert calls == []


def test_p5_6_past_answers_closes_the_sheet_first(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex, mode="daily", daily_date="2026-10-11", word1="MEDICAL", word2="CORRECT")
    open_sheet(phone)
    press(phone.locator(f"#sheet {SEL.format('past-answers')}"))
    expect(phone.locator("#screen-answers")).to_be_visible()
    expect(phone.locator("dialog[open]")).to_have_count(0)


def test_p5_7_desktop_width_closes_the_sheet(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    open_sheet(phone)
    phone.set_viewport_size({"width": 1000, "height": 800})
    expect(phone.locator("#sheet")).to_be_hidden()
    expect(phone.locator(".game-main > .found")).to_be_visible()
    box = phone.locator(".game-main > .found").bounding_box()
    d1 = decode_geometry((BASELINES / GEOMETRY).read_text(encoding="utf-8"))["game-6x6 1000x800 light"]
    want = next(row for row in d1 if row[0] == "section#screen-game.screen > div.game-main > aside.found")
    assert [round(box[k], 2) for k in ("x", "y", "width", "height")] == want[1:5]


def test_p5_8_accepted_word_flashes_the_found_button(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    found = phone.locator(SEL.format("found-open"))
    flash = phone.locator(".found-flash")
    for rc in (GE, AR):
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(found).to_have_attribute("data-flash", "a")
    expect(flash).to_have_text("+4")
    assert phone.evaluate("getComputedStyle(document.querySelector('.found-flash')).animationName") == "flash-a"

    phone.emulate_media(reduced_motion="reduce")
    for rc in ((4, 3), (5, 4)):  # DE ER
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(found).to_have_attribute("data-flash", "b")
    assert phone.evaluate("getComputedStyle(document.querySelector('.found-flash')).animationName") == "none"
    expect(found).not_to_have_attribute("data-flash", re.compile(".*"))  # it clears after about a second


# --- P6: game screen ---

SHAPES = {"5x5": ("GREAT", "SUMMR"), "6x6": ("GARDEN", "SUMMER"), "10x10": ("ABCDEFGHIJ", "KLMNOPQRST"),
          "5x10": ("ABCDE", "KLMNOPQRST"), "10x5": ("ABCDEFGHIJ", "KLMNO")}
DIAGONAL = [(i, i) for i in range(10)]
DEMOCRATIC = [(4, 2), (1, 0), (2, 4), (6, 5), (0, 3)]  # DE MO CR AT IC on MEDICAL × CORRECT


def tile_size(page: Page) -> str:
    return page.evaluate("getComputedStyle(document.getElementById('grid')).getPropertyValue('--tile-size').trim()")


def device_page(new_context, playwright, device_id: str) -> tuple[Page, list[str]]:
    """A page on one named device, for the cases the plan ties to a device."""
    page = new_context(**BY_ID[device_id].context_args(playwright)).new_page()
    return page, watch_errors(page)


def selected_tiles(page: Page) -> list[tuple[int, int]]:
    cells = page.evaluate("""() => [...document.querySelectorAll('.tile[data-state="selected"]')]
      .map((t) => [Number(t.dataset.row), Number(t.dataset.col)])""")
    return [tuple(c) for c in cells]


def gap_point(page: Page, row: int, col: int, dx: int) -> tuple[float, float]:
    """dx px right of the boundary between tile (row, col) and its right neighbour, at mid-height."""
    box = tile(page, row, col).bounding_box()
    return box["x"] + box["width"] + dx, box["y"] + box["height"] / 2


@pytest.mark.parametrize("shape", list(SHAPES))
def test_p6_1_every_shape_fits(phone: Page, live_server, lex: Lexicon, shape: str) -> None:
    word1, word2 = SHAPES[shape]
    open_page(phone, live_server, lex, word1=word1, word2=word2)
    s = snapshot(phone)
    assert l1(s) + l2(s) + l4(s) == []
    assert phone.evaluate("document.documentElement.scrollHeight <= document.documentElement.clientHeight")


def test_p6_2_10x10_pitch_and_font(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex, word1="ABCDEFGHIJ", word2="KLMNOPQRST")
    p = pitch(snapshot(phone))
    font = phone.evaluate("parseFloat(getComputedStyle(document.querySelector('.tile')).fontSize)")
    assert p >= 24
    if phone_device.width >= 360:
        # 31, not 32, since the source-word tiles widened the head to 22 px (owner's design, TS P6.2)
        assert p >= 31 and font >= 12, (p, font)


def test_p6_3_5x5_tiles_reach_56(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex, word1="GREAT", word2="SUMMR")
    if phone_device.width >= 375:
        assert tile_size(phone) == "56px"


def test_p6_4_submit_and_clear_are_reachable(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    s = snapshot(phone, submit=SEL.format("submit"), clear=SEL.format("clear"))
    assert l6(s, "submit") + l6(s, "clear") == []
    widths = {name: s["named"][name]["rect"][2] - s["named"][name]["rect"][0] for name in ("submit", "clear")}
    assert widths["submit"] >= widths["clear"]


def test_p6_5_hint_says_tap(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    expect(phone.locator("#preview")).to_have_text("Tap tiles to build a word")


def test_p6_6_ten_tiles_selected(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex, word1="ABCDEFGHIJ", word2="KLMNOPQRST")
    for rc in DIAGONAL:
        press(tile(phone, *rc))
    s = snapshot(phone, submit=SEL.format("submit"))
    assert l1(s) == [] and s["named"]["submit"]["hit"]


def test_p6_7_gap_clicks_pick_the_nearer_tile(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex, word1="ABCDEFGHIJ", word2="KLMNOPQRST")
    with phone.expect_request("**/api/events") as request:
        phone.mouse.click(*gap_point(phone, 4, 4, -1))
        phone.mouse.click(*gap_point(phone, 6, 4, 1))
    assert selected_tiles(phone) == [(4, 4), (6, 5)]
    events = request.value.post_data_json["events"]
    assert [(e["row"], e["col"]) for e in events if e["type"] == "tile_clicked"] == [(4, 4), (6, 5)]


def test_p6_8_keyboard_toggles_the_focused_tile(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    target = tile(phone, 5, 5)
    target.focus()
    phone.keyboard.press("Enter")
    expect(target).to_have_attribute("data-state", "selected")
    phone.keyboard.press("Space")
    expect(target).to_have_attribute("data-state", "default")
    assert selected_tiles(phone) == []


def test_p6_9_heads_and_busy_gaps_select_nothing(phone: Page, live_server, lex: Lexicon) -> None:
    api = open_page(phone, live_server, lex)
    box = phone.locator(".grid .head-col").first.bounding_box()
    phone.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert selected_tiles(phone) == []

    held = []
    phone.route("**/submit", lambda route: held.append(route))  # keeps the submit pending: the game is busy
    for rc in (GE, AR):
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator(SEL.format("submit"))).to_be_disabled()
    assert held
    phone.mouse.click(*gap_point(phone, 2, 2, 1))
    assert sorted(selected_tiles(phone)) == [GE, AR]
    api.handle(held[0])
    expect(phone.locator(".found-count")).to_contain_text("Found 1 of")


def test_p6_10_width_round_trip(new_context, playwright, live_server, lex: Lexicon) -> None:
    page, errors = device_page(new_context, playwright, "ip17-bars")
    open_page(page, live_server, lex, word1="ABCDEFGHIJ", word2="KLMNOPQRST")
    for rc in (GE, AR):
        press(tile(page, *rc))
    before = tile_size(page)
    for width, height in ((360, 780), (402, 681)):
        assert l8(page, width, height) == []
        assert l4(snapshot(page)) == []
        assert sorted(selected_tiles(page)) == [GE, AR]
    assert tile_size(page) == before
    assert errors == []


def test_p6_11_browser_bars_round_trip(new_context, playwright, live_server, lex: Lexicon) -> None:
    page, errors = device_page(new_context, playwright, "se-bars")
    open_page(page, live_server, lex, word1="ABCDE", word2="KLMNOPQRST")
    short = tile_size(page)
    assert l8(page, 375, 667) == []
    tall = tile_size(page)
    assert l8(page, 375, 553) == []
    assert float(tall[:-2]) > float(short[:-2]) and tile_size(page) == short
    assert errors == []


@pytest.mark.parametrize("game", ["daily", "10x10"])
def test_p6_12_won_banner(new_context, playwright, live_server, lex: Lexicon, game: str) -> None:
    page, errors = device_page(new_context, playwright, "se-bars")
    if game == "daily":
        open_page(page, live_server, lex, mode="daily", daily_date="2026-10-11", word1="MEDICAL", word2="CORRECT",
                  win_on_accept=True)
        word = DEMOCRATIC
    else:
        open_page(page, live_server, lex, mode="daily", daily_date="2026-10-11", word1="ABCDEFGHIJ",
                  word2="KLMNOPQRST", words=["AKBL", "CMDN"], win_on_accept=True)
        word = [(0, 0), (1, 1)]  # AK BL
    before = tile_size(page)
    for rc in word:
        press(tile(page, *rc))
    press(page.locator(SEL.format("submit")))
    expect(page.locator(SEL.format("won"))).to_be_visible()
    s = snapshot(page, won=SEL.format("won"))
    assert l4(s) == []
    banner = s["named"]["won"]["rect"]
    assert all(t["rect"][1] >= banner[3] - 0.5 for t in s["controls"] if t["tile"])
    assert float(tile_size(page)[:-2]) <= float(before[:-2])
    press(page.locator(SEL.format("won-close")))
    expect(page.locator(SEL.format("won"))).to_be_hidden()
    page.wait_for_function("(want) => getComputedStyle(document.getElementById('grid'))"
                           ".getPropertyValue('--tile-size').trim() === want", arg=before)
    assert errors == []


def test_p6_13_focus_survives_a_resize(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex)
    tile(phone, 3, 3).focus()
    phone.set_viewport_size({"width": phone_device.width, "height": phone_device.height - 100})
    phone.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
    assert phone.evaluate("[document.activeElement.dataset.row, document.activeElement.dataset.col]") == ["3", "3"]


def test_p6_14_resize_while_hidden(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex, word1="ABCDEFGHIJ", word2="KLMNOPQRST")
    to_boards(phone)  # the game screen is hidden
    phone.set_viewport_size({"width": phone_device.width, "height": phone_device.height - 120})
    press(phone.locator(SEL.format("boards-back")))
    phone.locator(".tile").first.wait_for()
    assert l4(snapshot(phone)) == []
    after = tile_size(phone)
    phone.reload()
    phone.locator(".tile").first.wait_for()
    assert tile_size(phone) == after  # the same as a fresh load at this size


def test_p6_15_resize_during_the_rejected_flash(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex)
    for rc in ((0, 0), (0, 1)):  # GSAS
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(tile(phone, 0, 0)).to_have_attribute("data-state", "rejected")
    phone.set_viewport_size({"width": phone_device.width, "height": phone_device.height - 80})
    expect(phone.locator('.tile[data-state="rejected"]')).to_have_count(0)
    assert l4(snapshot(phone)) == []


# --- P7: screen-reader announcements (all viewports) ---

FRAME = {"light": "rgb(30, 30, 30)", "dark": "rgb(242, 237, 224)"}  # --frame: #1E1E1E and #F2EDE0


def test_p7_1_accepted_word_is_announced(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    for rc in (GE, AR):
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator("#sr-status")).to_have_text("GEAR, 4 points. 1 of 9 found.")


def test_p7_2_rejected_word_goes_to_the_alert_region(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    for rc in ((0, 0), (0, 1)):
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator("#sr-alert")).to_contain_text("GSAS isn’t in the dictionary")


def test_p7_3_quiet_regions_and_names(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex, mode="daily", daily_date="2026-10-11", word1="MEDICAL", word2="CORRECT",
              found=["DEMOCRATIC"], won=True)
    assert phone.locator(SEL.format("solution")).get_attribute("aria-live") is None
    assert phone.locator(SEL.format("toast")).get_attribute("role") is None
    expect(phone.locator("#grid")).to_have_attribute("role", "group")
    expect(phone.locator("#grid")).to_have_attribute("aria-label", "Letter grid: MEDICAL across, CORRECT down")
    phone.evaluate("""() => { window.__wonMutations = 0;
      new MutationObserver((list) => { window.__wonMutations += list.length; })
        .observe(document.getElementById('won'), { childList: true, subtree: true, characterData: true }); }""")
    press(tile(phone, 0, 0))
    expect(tile(phone, 0, 0)).to_have_attribute("data-state", "selected")
    assert phone.evaluate("window.__wonMutations") == 0


def focus_ring_colours(page: Page) -> list[str]:
    """Outline colours after keyboard focus on a tile, a selected tile and Submit."""
    colours = []
    outline = "getComputedStyle(document.activeElement).outlineColor"
    tile(page, 0, 0).focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")  # keyboard focus, so :focus-visible matches
    colours.append(page.evaluate(outline))
    page.keyboard.press("Enter")
    colours.append(page.evaluate(outline))
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")  # tile 0,1: two tiles selected, so Submit is enabled
    page.locator(SEL.format("submit")).focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.dataset.testid") == "submit"
    colours.append(page.evaluate(outline))
    return colours


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("where", ["desktop", "se-bars"])
def test_p7_4_focus_ring_uses_the_frame_colour(new_context, playwright, live_server, lex: Lexicon,
                                               where: str, scheme: str) -> None:
    if where == "desktop":
        args = {"viewport": {"width": 1000, "height": 800}}
    else:
        args = BY_ID[where].context_args(playwright)
    page = new_context(**{**args, "color_scheme": scheme}).new_page()
    errors = watch_errors(page)
    open_page(page, live_server, lex)
    assert focus_ring_colours(page) == [FRAME[scheme]] * 3

    page.reload()
    page.locator(".tile").first.wait_for()
    tile(page, 2, 2).click()  # a mouse or touch press shows no ring
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "none"
    assert errors == []


# --- P8: toast on phones ---

def test_p8_1_the_tallest_toast_stays_off_the_grid(phone: Page, live_server, lex: Lexicon, phone_device) -> None:
    open_page(phone, live_server, lex, word1="ABCDEFGHIJ", word2="KLMNOPQRST")
    for rc in ((0, 0), (0, 1)):  # AKBK: rejected, the longest message
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator(SEL.format("toast"))).to_be_visible()
    s = snapshot(phone, toast=SEL.format("toast"))
    left, top, right, bottom = s["named"]["toast"]["rect"]
    width = phone_device.width
    assert left >= -0.5 and top >= -0.5 and right <= width + 0.5 and bottom <= phone_device.height + 0.5
    assert right - left <= min(380, width - 24) + 0.5
    assert abs((left + right) / 2 - width / 2) <= 1
    assert top >= s["area"][3] - 0.5, f"the toast (top {top}) overlaps the grid area (bottom {s['area'][3]})"


def test_p8_2_next_tile_tap_hides_the_toast(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    for rc in ((0, 0), (0, 1)):
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator(SEL.format("toast"))).to_be_visible()
    expect(tile(phone, 0, 0)).to_have_attribute("data-state", "default")  # the flash is over: play goes on
    press(tile(phone, 2, 2))
    expect(phone.locator(SEL.format("toast"))).to_be_hidden()
    expect(tile(phone, 2, 2)).to_have_attribute("data-state", "selected")


def test_p8_3_desktop_toast_is_unchanged(page: Page, live_server, fake_api) -> None:
    page.set_viewport_size({"width": 1000, "height": 800})
    page.emulate_media(reduced_motion="reduce")
    page.goto(live_server.base_url + "/")
    page.locator(".tile").first.wait_for()
    for rc in ((0, 0), (0, 1)):
        tile(page, *rc).click()
    page.locator(SEL.format("submit")).click()
    expect(page.locator('.tile[data-state="rejected"]')).to_have_count(0)
    tile(page, 2, 2).click()  # on desktop a tap doesn't hide it
    box = page.locator(SEL.format("toast")).bounding_box()
    d1 = decode_geometry((BASELINES / GEOMETRY).read_text(encoding="utf-8"))["rejected-toast 1000x800 light"]
    want = next(row for row in d1 if row[0] == "div#toast.toast[toast]")
    assert [round(box[k], 2) for k in ("x", "y", "width", "height")] == want[1:5]


@pytest.mark.parametrize("device_id", ["env-corner", "se-bars"])
def test_p8_4_flows_2_and_4_by_touch(new_context, playwright, live_server, device_id: str) -> None:
    """Phase-1 gate: flows 2 and 4 on the real backend, with taps."""
    page, errors = device_page(new_context, playwright, device_id)
    page.goto(live_server.base_url + "/")
    page.locator("#nick").fill("Tester")
    press(page.get_by_role("button", name="Play"))
    page.locator(".tile").first.wait_for()

    for rc in (GE, AR):  # flow 2
        press(tile(page, *rc))
    expect(page.locator("#preview")).to_have_text("→ GEAR")
    press(page.locator(SEL.format("submit")))
    expect(page.locator(SEL.format("score"))).to_have_text("4")
    expect(page.locator(".found-count")).to_have_text("Found 1 of 9")
    expect(page.locator('.tile[data-state="selected"]')).to_have_count(0)

    for rc in ((0, 0), (0, 1)):  # flow 4
        press(tile(page, *rc))
    press(page.locator(SEL.format("submit")))
    expect(tile(page, 0, 0)).to_have_attribute("data-state", "rejected")
    expect(page.locator(SEL.format("toast"))).to_contain_text("GSAS isn’t in the dictionary")
    expect(page.locator(SEL.format("toast"))).to_contain_text("2 tries left")
    expect(tile(page, 0, 0)).to_have_attribute("data-state", "default")
    expect(page.locator(SEL.format("tries"))).to_have_attribute("aria-label", "2 of 3 tries left")
    assert errors == []


# --- Checkpoint H: phones playable ---

def test_checkpoint_h_free_and_daily_by_touch(phone: Page, live_server) -> None:
    """A free game and a daily game, start to finish by touch, on the real backend."""
    phone.goto(live_server.base_url + "/")
    phone.locator("#nick").fill("Tester")
    press(phone.get_by_role("button", name="Play"))
    phone.locator(".tile").first.wait_for()

    for rc in (GE, AR):
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator(SEL.format("score"))).to_have_text("4")
    press(phone.locator(SEL.format("end")))
    expect(phone.locator(SEL.format("over-reason"))).to_have_text("You ended the game · GARDEN × SUMMER")

    press(phone.locator(SEL.format("mode-daily")))
    expect(phone.locator(".grid .head-col")).to_have_text(list("MEDICAL"))
    for rc in DEMOCRATIC:
        press(tile(phone, *rc))
    press(phone.locator(SEL.format("submit")))
    expect(phone.locator(SEL.format("won"))).to_be_visible()
    assert l4(snapshot(phone)) == []
    press(phone.locator(SEL.format("end")))
    expect(phone.locator(SEL.format("over-score"))).to_have_text("25")


# --- P13: back gesture ---

@pytest.mark.parametrize("screen", ["answers", "boards"])
def test_p13_1_back_returns_to_the_game(phone: Page, live_server, lex: Lexicon, screen: str) -> None:
    if screen == "answers":
        open_page(phone, live_server, lex, ready=SEL.format("over-score"), **daily_over({}))
        to_answers(phone)
        came_from = "#screen-over"
    else:
        open_page(phone, live_server, lex)
        to_boards(phone)
        came_from = "#screen-game"
    phone.go_back()
    expect(phone.locator(came_from)).to_be_visible()
    assert phone.url.startswith(live_server.base_url)


def test_p13_1_in_app_back_leaves_no_extra_entry(phone: Page, live_server, lex: Lexicon) -> None:
    open_page(phone, live_server, lex)
    length = phone.evaluate("history.length")
    to_boards(phone)
    press(phone.locator(SEL.format("boards-back")))
    expect(phone.locator("#screen-game")).to_be_visible()
    to_boards(phone)  # a second visit reuses the entry rather than stacking another
    phone.go_back()
    expect(phone.locator("#screen-game")).to_be_visible()
    assert phone.evaluate("history.length") == length + 1
    assert phone.evaluate("history.state") is None
