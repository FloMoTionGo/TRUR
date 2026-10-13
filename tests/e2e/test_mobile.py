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
