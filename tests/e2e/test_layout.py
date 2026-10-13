"""Desktop baseline (docs/test-strategy.md §7, step P1): what "desktop unchanged" means for phase 9.

D1 records the rect and key styles of every visible element under #app in 12 states, at two desktop sizes,
light and dark. D3 records the tile size, gap and grid rect per grid shape. Both compare with
tests/e2e/baselines/desktop/. `--update-baselines` rewrites the files, only in P1 or in a commit that names
the desktop change the owner approved. A mismatch also writes what was measured under the Playwright output
directory (baselines/desktop/), so a failed CI run carries a fresh Linux baseline in its trace artifact.
"""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from tests.e2e.conftest import FakeApi, long_board, long_words, watch_errors
from trur.solver import Lexicon

pytestmark = pytest.mark.e2e

BASELINES = Path(__file__).parent / "baselines" / "desktop"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)  # the FakeClock start, a Sunday
SIZES = [(1000, 800), (640, 640)]
SCHEMES = ["light", "dark"]
SEL = '[data-testid="{}"]'
GE, AR = (4, 0), (5, 1)
GSAS = [(0, 0), (0, 1)]
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

FRAMES = "() => document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))"


# --- the 12 states ---

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
    "selected": State(".chip", act=lambda page: tap(page, GE, AR)),
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
    api = FakeApi(lex)
    state.setup(api)
    page.route("**/api/**", api.handle)
    if css:
        page.route("**/css/app.css", plant(css))
    page.clock.install(time=NOW)
    page.goto(base_url + "/")
    expect(page.locator("#screen-loading")).to_be_hidden()
    state.act(page)
    page.locator(state.ready).first.wait_for()
    if state.paused:
        page.evaluate("() => document.fonts.ready.then(() => 0)")
        page.clock.run_for(50)
    else:
        page.evaluate(FRAMES)
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
    "geometry.json": (encode_geometry, decode_geometry),
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


def tolerance() -> float:
    """0 px on the platform that took the baseline (Linux, as CI runs), 1 px elsewhere."""
    meta = BASELINES / "meta.json"
    platform = json.loads(meta.read_text(encoding="utf-8"))["platform"] if meta.exists() else None
    return 0.0 if platform == sys.platform else 1.0


def diff_geometry(want: list, got: list, tol: float) -> list[str]:
    want_by = {row[0]: row for row in want}
    got_by = {row[0]: row for row in got}
    out = [f"missing {p}" for p in want_by if p not in got_by]
    out += [f"extra {p}" for p in got_by if p not in want_by]
    for p in (p for p in want_by if p in got_by):
        w, g = want_by[p], got_by[p]
        if any(abs(a - b) > tol + 1e-9 for a, b in zip(w[1:5], g[1:5])):
            out.append(f"{p}: rect {w[1:5]} -> {g[1:5]}")
        for prop in sorted(w[5].keys() | g[5].keys()):
            if w[5].get(prop) != g[5].get(prop):
                out.append(f"{p}: {prop} {w[5].get(prop)!r} -> {g[5].get(prop)!r}")
    return out


def diff_size(want: dict, got: dict, tol: float) -> list[str]:
    out = [f"{k} {want[k]!r} -> {got[k]!r}" for k in ("tile-size", "gap") if want[k] != got[k]]
    if any(abs(a - b) > tol + 1e-9 for a, b in zip(want["grid"], got["grid"])):
        out.append(f"grid rect {want['grid']} -> {got['grid']}")
    return out


@pytest.fixture
def baseline(pytestconfig: pytest.Config, browser: Browser):
    """check(file, measured): compare with the baseline, or rewrite it under --update-baselines."""
    update = pytestconfig.getoption("update_baselines")
    out = Path(pytestconfig.getoption("output")) / "baselines" / "desktop"
    diff = {"geometry.json": diff_geometry, "tile_sizes.json": diff_size}

    def check(name: str, got: dict) -> None:
        if update:
            save(BASELINES, name, got)
            meta = {"platform": sys.platform, "browser": f"{browser.browser_type.name} {browser.version}"}
            (BASELINES / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
            return
        want = load(BASELINES, name)
        tol = tolerance()
        problems = []
        for key, value in got.items():
            if key not in want:
                problems.append(f"{key}: not in the baseline")
            else:
                problems += [f"{key}: {line}" for line in diff[name](want[key], value, tol)]
        if problems:
            save(out, name, got)
            shown = "\n".join(problems[:25]) + (f"\n... {len(problems) - 25} more" if len(problems) > 25 else "")
            pytest.fail(f"{name} differs from the desktop baseline ({len(problems)} differences, tolerance {tol} px; "
                        f"measured values in {out}):\n{shown}", pytrace=False)

    return check


# --- the tests ---

@pytest.mark.parametrize("name", list(STATES))
def test_d1_desktop_geometry(name: str, new_context, live_server, lex: Lexicon, baseline) -> None:
    got = {f"{name} {w}x{h} {scheme}": capture(new_context, live_server.base_url, lex, STATES[name], (w, h), scheme)
           for w, h in SIZES for scheme in SCHEMES}
    baseline("geometry.json", got)


def test_d3_tile_sizes(new_context, live_server, lex: Lexicon, baseline, pytestconfig: pytest.Config) -> None:
    every = pytestconfig.getoption("all_shapes") or pytestconfig.getoption("update_baselines")
    baseline("tile_sizes.json", measure_shapes(new_context, live_server.base_url, lex, ALL_SHAPES if every else PUSH_SHAPES))


def test_planted_tile_size_fails_d1_and_d3(new_context, live_server, lex: Lexicon, pytestconfig: pytest.Config) -> None:
    """P1.2: the oracle notices a 1-px tile change and names the tiles."""
    if pytestconfig.getoption("update_baselines"):
        pytest.skip("the baseline is being rewritten")
    key = "game-6x6 1000x800 light"
    got = capture(new_context, live_server.base_url, lex, STATES["game-6x6"], (1000, 800), "light", css=PLANTED)
    d1 = diff_geometry(load(BASELINES, "geometry.json")[key], got, tolerance())
    assert any(".tile" in line and ": rect " in line for line in d1), d1[:10]

    sizes = measure_shapes(new_context, live_server.base_url, lex, [(6, 6, False)], css=PLANTED)
    d3 = diff_size(load(BASELINES, "tile_sizes.json")["6x6"], sizes["6x6"], tolerance())
    assert "tile-size '48px' -> '47px'" in d3
