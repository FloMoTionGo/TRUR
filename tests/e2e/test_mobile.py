"""Phones (docs/test-strategy.md §7): cases per P task, on the --devices set (portrait only until P14)."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import FakeApi
from tests.e2e.layout import l1, press, snapshot
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
