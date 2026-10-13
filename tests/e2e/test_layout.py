"""Desktop baseline (docs/test-strategy.md §7, step P1): what "desktop unchanged" means for phase 9.

D1 records the rect and key styles of every visible element under #app in 12 states, at two desktop sizes,
light and dark. D3 records the tile size, gap and grid rect per grid shape. Both compare with
tests/e2e/baselines/desktop/, exactly. D1 keeps one file per platform (geometry.linux.json is CI's): Linux
Chromium rounds text widths to whole pixels and Windows doesn't, which moves text-sized boxes by up to 8 px.
D3 has no text, so tile_sizes.json is shared. `--update-baselines` rewrites the files, only in P1 or in a commit that names
the desktop change the owner approved. A mismatch also writes what was measured under the Playwright output
directory (baselines/desktop/), so a failed CI run carries a fresh Linux baseline in its trace artifact.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e.conftest import FakeApi, watch_errors
from tests.e2e.screens import FRAMES, SEL, STATES, State, reach, tap
from trur.solver import Lexicon

pytestmark = pytest.mark.e2e

BASELINES = Path(__file__).parent / "baselines" / "desktop"
GEOMETRY = f"geometry.{sys.platform}.json"
SIZES = [(1000, 800), (640, 640)]
SCHEMES = ["light", "dark"]
PLANTED = ".grid { --tile-size: 47px !important; }"  # P1.2

# Every visible element under #app: [path, x, y, width, height, styles]. A path segment is tag, id,
# classes and test id, with ":n" when siblings share it; an element with an id or test id starts a new
# path, since both are unique. 1×1 boxes are visually hidden and skipped.
MEASURE = """() => {
  const STYLES = ["font-family", "font-size", "font-weight", "line-height", "letter-spacing", "color",
    "background-color", "border-width", "border-style", "border-color", "border-radius", "box-shadow", "gap"];
  const round = (v) => Math.round(v * 100) / 100;
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += `#${el.id}`;
    const cls = el.getAttribute("class");
    if (cls) s += `.${cls.trim().split(/\\s+/).join(".")}`;
    if (el.dataset && el.dataset.testid) s += `[${el.dataset.testid}]`;
    return s;
  };
  const out = [];
  const walk = (el, path) => {
    if (!el.checkVisibility()) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 && r.height <= 1) return;
    const cs = getComputedStyle(el);
    const style = {};
    for (const p of STYLES) style[p] = cs.getPropertyValue(p);
    if (el.id === "grid") for (const p of ["--tile-size", "--gap"]) style[p] = cs.getPropertyValue(p).trim();
    out.push([path, round(r.x), round(r.y), round(r.width), round(r.height), style]);
    if (el instanceof SVGElement) return;
    const names = [...el.children].map(name);
    const total = {};
    for (const n of names) total[n] = (total[n] || 0) + 1;
    const seen = {};
    [...el.children].forEach((child, i) => {
      const n = names[i];
      seen[n] = (seen[n] || 0) + 1;
      const own = child.id || (child.dataset && child.dataset.testid);
      walk(child, own ? n : `${path} > ${n}${total[n] > 1 ? `:${seen[n]}` : ""}`);
    });
  };
  walk(document.getElementById("app"), "#app");
  return out;
}"""

TILE_SIZE = """() => {
  const grid = document.getElementById("grid");
  const cs = getComputedStyle(grid);
  const r = grid.getBoundingClientRect();
  return { "tile-size": cs.getPropertyValue("--tile-size").trim(), gap: cs.getPropertyValue("--gap").trim(),
           grid: [r.x, r.y, r.width, r.height].map((v) => Math.round(v * 100) / 100) };
}"""



def plant(css: str) -> Callable[[Route], None]:
    """Serve app.css with extra rules appended (the CSP blocks injected <style>)."""
    def handler(route: Route) -> None:
        response = route.fetch()
        route.fulfill(response=response, body=response.text() + "\n" + css)
    return handler


def capture(new_context, base_url: str, lex: Lexicon, state: State, size: tuple[int, int], scheme: str,
            css: str | None = None) -> list:
    context = new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme,
                          reduced_motion="reduce", timezone_id="UTC", locale="en-US")
    page = context.new_page()
    errors = watch_errors(page)
    if css:
        page.route("**/css/app.css", plant(css))
    reach(page, base_url, lex, state)
    rows = page.evaluate(MEASURE)
    assert errors == []
    context.close()
    return rows


# --- shapes (D3) ---

PUSH_SHAPES = [(5, 5, False), (6, 6, False), (7, 7, True), (8, 10, False),
               (10, 5, False), (5, 10, False), (10, 10, False), (10, 10, True)]
ALL_SHAPES = [(c, r, won) for c in range(5, 11) for r in range(5, 11) for won in (False, True)]


def shape_key(cols: int, rows: int, won: bool) -> str:
    return f"{cols}x{rows}{' won' if won else ''}"


def measure_shapes(new_context, base_url: str, lex: Lexicon, shapes: list, css: str | None = None) -> dict:
    context = new_context(viewport={"width": 1000, "height": 800}, color_scheme="light",
                          reduced_motion="reduce", timezone_id="UTC", locale="en-US")
    page = context.new_page()
    errors = watch_errors(page)
    current: dict[str, FakeApi] = {}
    page.route("**/api/**", lambda route: current["api"].handle(route))
    if css:
        page.route("**/css/app.css", plant(css))
    got = {}
    for cols, rows, won in shapes:
        api = FakeApi(lex, word1="ABCDEFGHIJ"[:cols], word2="KLMNOPQRST"[:rows])
        if won:
            api.mode, api.daily_date, api.won = "daily", "2026-10-11", True
        current["api"] = api
        page.goto(base_url + "/")
        page.locator(".tile").first.wait_for()
        page.evaluate(FRAMES)
        got[shape_key(cols, rows, won)] = page.evaluate(TILE_SIZE)
    assert errors == []
    context.close()
    return got


# --- baseline files ---

def encode_geometry(states: dict[str, list]) -> str:
    """One row per line; identical style sets are stored once and referenced by index."""
    styles: list[str] = []
    index: dict[str, int] = {}
    blocks = []
    for key in sorted(states):
        rows = []
        for path, x, y, w, h, style in states[key]:
            s = json.dumps(style, sort_keys=True, ensure_ascii=False)
            if s not in index:
                index[s] = len(styles)
                styles.append(s)
            rows.append(json.dumps([path, x, y, w, h, index[s]], ensure_ascii=False))
        blocks.append(f"  {json.dumps(key)}: [\n    " + ",\n    ".join(rows) + "\n  ]")
    return ('{\n "styles": [\n  ' + ",\n  ".join(styles) + '\n ],\n "states": {\n'
            + ",\n".join(blocks) + "\n }\n}\n")


def decode_geometry(text: str) -> dict[str, list]:
    data = json.loads(text)
    return {key: [[*row[:5], data["styles"][row[5]]] for row in rows] for key, rows in data["states"].items()}


def encode_sizes(sizes: dict[str, dict]) -> str:
    def order(key: str) -> tuple:
        c, r = key.split()[0].split("x")
        return int(c), int(r), key
    lines = [f"  {json.dumps(k)}: {json.dumps(sizes[k])}" for k in sorted(sizes, key=order)]
    return "{\n" + ",\n".join(lines) + "\n}\n"


FILES = {
    GEOMETRY: (encode_geometry, decode_geometry),
    "tile_sizes.json": (encode_sizes, json.loads),
}


def load(directory: Path, name: str) -> dict:
    path = directory / name
    return FILES[name][1](path.read_text(encoding="utf-8")) if path.exists() else {}


def save(directory: Path, name: str, entries: dict) -> None:
    """Merge entries into the file (each D1 test owns its own keys)."""
    merged = load(directory, name) or (load(BASELINES, name) if directory != BASELINES else {})
    merged.update(entries)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(FILES[name][0](merged), encoding="utf-8", newline="\n")


def diff_geometry(want: list, got: list) -> list[str]:
    want_by = {row[0]: row for row in want}
    got_by = {row[0]: row for row in got}
    out = [f"missing {p}" for p in want_by if p not in got_by]
    out += [f"extra {p}" for p in got_by if p not in want_by]
    for p in (p for p in want_by if p in got_by):
        w, g = want_by[p], got_by[p]
        if w[1:5] != g[1:5]:
            out.append(f"{p}: rect {w[1:5]} -> {g[1:5]}")
        for prop in sorted(w[5].keys() | g[5].keys()):
            if w[5].get(prop) != g[5].get(prop):
                out.append(f"{p}: {prop} {w[5].get(prop)!r} -> {g[5].get(prop)!r}")
    return out


def diff_size(want: dict, got: dict) -> list[str]:
    out = [f"{k} {want[k]!r} -> {got[k]!r}" for k in ("tile-size", "gap") if want[k] != got[k]]
    if want["grid"] != got["grid"]:
        out.append(f"grid rect {want['grid']} -> {got['grid']}")
    return out


@pytest.fixture
def baseline(pytestconfig: pytest.Config):
    """check(file, measured): compare with the baseline, or rewrite it under --update-baselines."""
    update = pytestconfig.getoption("update_baselines")
    out = Path(pytestconfig.getoption("output")) / "baselines" / "desktop"
    diff = {GEOMETRY: diff_geometry, "tile_sizes.json": diff_size}

    def check(name: str, got: dict) -> None:
        if update:
            save(BASELINES, name, got)
            return
        want = load(BASELINES, name)
        problems = []
        for key, value in got.items():
            if key not in want:
                problems.append(f"{key}: not in {BASELINES / name}")
            else:
                problems += [f"{key}: {line}" for line in diff[name](want[key], value)]
        if problems:
            save(out, name, got)
            shown = "\n".join(problems[:25]) + (f"\n... {len(problems) - 25} more" if len(problems) > 25 else "")
            pytest.fail(f"{name} differs from the desktop baseline ({len(problems)} differences; "
                        f"measured values in {out}):\n{shown}", pytrace=False)

    return check


# --- the tests ---

@pytest.mark.parametrize("name", list(STATES))
def test_d1_desktop_geometry(name: str, new_context, live_server, lex: Lexicon, baseline) -> None:
    got = {f"{name} {w}x{h} {scheme}": capture(new_context, live_server.base_url, lex, STATES[name], (w, h), scheme)
           for w, h in SIZES for scheme in SCHEMES}
    baseline(GEOMETRY, got)


def test_d3_tile_sizes(new_context, live_server, lex: Lexicon, baseline, pytestconfig: pytest.Config) -> None:
    every = pytestconfig.getoption("all_shapes") or pytestconfig.getoption("update_baselines")
    baseline("tile_sizes.json", measure_shapes(new_context, live_server.base_url, lex, ALL_SHAPES if every else PUSH_SHAPES))


DEMOCRATIC = [(4, 2), (1, 0), (2, 4), (6, 5), (0, 3)]  # DE MO CR AT IC on MEDICAL × CORRECT


def test_won_banner_refits_without_rebuilding(page: Page, live_server, fake_api: FakeApi, console_errors) -> None:
    """P2.2: winning the daily shrinks the tiles in place; the tile buttons survive."""
    fake_api.mode, fake_api.daily_date, fake_api.word1, fake_api.word2 = "daily", "2026-10-11", "MEDICAL", "CORRECT"
    fake_api.win_on_accept = True
    page.set_viewport_size({"width": 1000, "height": 800})
    page.goto(live_server.base_url + "/")
    page.locator(".tile").first.wait_for()
    page.evaluate("document.querySelector('.tile').trurMarker = 1")

    tap(page, *DEMOCRATIC)
    page.locator(SEL.format("submit")).click()
    expect(page.locator(SEL.format("won"))).to_be_visible()

    assert page.evaluate("document.querySelector('.tile').trurMarker") == 1
    want = load(BASELINES, "tile_sizes.json")["7x7 won"]["tile-size"]
    assert page.evaluate(TILE_SIZE)["tile-size"] == want
    assert console_errors == []


def test_error_collector_catches_resize_observer_loops(page: Page, live_server, console_errors) -> None:
    """P2.3's oracle: a ResizeObserver that resizes what it observes is reported."""
    page.goto(live_server.base_url + "/")
    page.evaluate("""() => new Promise((done) => {
      const box = document.getElementById("app");
      new ResizeObserver(() => { box.style.setProperty("width", `${box.offsetWidth + 1}px`); }).observe(box);
      setTimeout(done, 200);
    })""")
    assert any("ResizeObserver loop" in e for e in console_errors)


def test_planted_tile_size_fails_d1_and_d3(new_context, live_server, lex: Lexicon, pytestconfig: pytest.Config) -> None:
    """P1.2: the oracle notices a 1-px tile change and names the tiles."""
    if pytestconfig.getoption("update_baselines"):
        pytest.skip("the baseline is being rewritten")
    key = "game-6x6 1000x800 light"
    got = capture(new_context, live_server.base_url, lex, STATES["game-6x6"], (1000, 800), "light", css=PLANTED)
    d1 = diff_geometry(load(BASELINES, GEOMETRY)[key], got)
    assert any(".tile" in line and ": rect " in line for line in d1), d1[:10]

    sizes = measure_shapes(new_context, live_server.base_url, lex, [(6, 6, False)], css=PLANTED)
    d3 = diff_size(load(BASELINES, "tile_sizes.json")["6x6"], sizes["6x6"])
    assert "tile-size '48px' -> '47px'" in d3
