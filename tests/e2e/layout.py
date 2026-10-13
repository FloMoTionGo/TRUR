"""Layout checks L1–L8 (docs/test-strategy.md §7, "Layout checks").

`snapshot(page)` measures the active screen in one evaluate; the l1–l7 functions check a snapshot and
return a list of problems, empty when the check holds. Rects are [left, top, right, bottom] in CSS px.
"""

from playwright.sync_api import Locator, Page

EPS = 0.5
SHADOW = 3  # tiles and primary buttons draw a 3 px shadow to the right and below

SNAPSHOT = """(extra) => {
  const W = document.documentElement.clientWidth;
  const H = document.documentElement.clientHeight;
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const hit = (el) => {
    const r = el.getBoundingClientRect();
    const at = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
    return Boolean(at) && (at === el || el.contains(at));
  };
  const name = (el) => el.dataset.testid || el.id
    || (el.classList.contains("tile") ? `tile ${el.dataset.row},${el.dataset.col}` : "")
    || `${el.tagName.toLowerCase()} "${el.textContent.trim().slice(0, 24)}"`;
  const visible = (el) => el.checkVisibility({ visibilityProperty: true });
  const item = (el) => ({ name: name(el), rect: rect(el), hit: hit(el),
                          shadow: el.matches(".tile, .button-primary"), tile: el.classList.contains("tile"),
                          row: Number(el.dataset.row), col: Number(el.dataset.col) });

  scrollTo(9999, 0);
  const scrollX = window.scrollX;
  scrollTo(0, 0);
  const screen = document.querySelector(".screen:not([hidden])");
  const grid = screen && screen.querySelector(".grid");
  const solution = screen && screen.querySelector(".solution");
  const area = grid && grid.closest(".grid-area");
  const named = {};
  for (const [key, selector] of Object.entries(extra || {})) {
    const el = document.querySelector(selector);
    named[key] = el && visible(el) ? item(el) : null;
  }
  return {
    W, H, scrollX,
    scale: window.visualViewport ? visualViewport.scale : 1,
    docScroll: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
    bodyScroll: [document.body.scrollWidth, document.body.clientWidth],
    screen: screen ? screen.id : null,
    controls: screen ? [...screen.querySelectorAll("button, input, a, [role=tab], .tile")].filter(visible).map(item) : [],
    cols: grid ? Number(grid.style.getPropertyValue("--cols")) : 0,
    rows: grid ? Number(grid.style.getPropertyValue("--rows")) : 0,
    area: area ? rect(area) : null,
    top: rect(document.querySelector(".bar"))[3],
    bottom: solution && visible(solution) ? rect(solution)[1] : H,
    named,
  };
}"""


def snapshot(page: Page, **extra: str) -> dict:
    """The active screen's measurements; extra=name→selector adds named elements for l5–l7."""
    return page.evaluate(SNAPSHOT, extra)


def _extent(c: dict) -> list[float]:
    left, top, right, bottom = c["rect"]
    return [left, top, right + SHADOW, bottom + SHADOW] if c["shadow"] else [left, top, right, bottom]


def _inside(r: list[float], box: list[float]) -> bool:
    return r[0] >= box[0] - EPS and r[1] >= box[1] - EPS and r[2] <= box[2] + EPS and r[3] <= box[3] + EPS


def l1(s: dict) -> list[str]:
    """No sideways scroll and no zoom."""
    out = []
    if s["docScroll"][0] > s["docScroll"][1]:
        out.append(f"L1: document scrollWidth {s['docScroll'][0]} > clientWidth {s['docScroll'][1]}")
    if s["bodyScroll"][0] > s["bodyScroll"][1]:
        out.append(f"L1: body scrollWidth {s['bodyScroll'][0]} > clientWidth {s['bodyScroll'][1]}")
    if s["scale"] != 1:
        out.append(f"L1: visualViewport.scale is {s['scale']}")
    if s["scrollX"] != 0:
        out.append(f"L1: scrolled sideways to x={s['scrollX']}")
    return out


def l2(s: dict) -> list[str]:
    """Every visible control of the active screen lies inside the viewport."""
    view = [0, 0, s["W"], s["H"]]
    return [f"L2: {c['name']} at {[round(v, 1) for v in c['rect']]} is clipped by the {s['W']}x{s['H']} viewport"
            for c in s["controls"] if not _inside(_extent(c), view)]


def l3(s: dict) -> list[str]:
    """Every visible control is the element at its own centre (nothing covers it)."""
    return [f"L3: {c['name']} is covered at its centre" for c in s["controls"] if not c["hit"]]


def l4(s: dict) -> list[str]:
    """All tiles are there, inside the grid area and the viewport, between header and action area, apart."""
    tiles = [c for c in s["controls"] if c["tile"]]
    out = []
    if len(tiles) != s["cols"] * s["rows"]:
        out.append(f"L4: {len(tiles)} tiles for a {s['cols']}x{s['rows']} grid")
    band = [0, s["top"], s["W"], s["bottom"]]
    for t in tiles:
        r = _extent(t)
        if s["area"] is None or not _inside(r, s["area"]):
            out.append(f"L4: {t['name']} is outside the grid area")
        if not _inside(r, band):
            out.append(f"L4: {t['name']} is outside the viewport between header and action area")
        if not t["hit"]:
            out.append(f"L4: {t['name']} is covered at its centre")
    at = {(t["row"], t["col"]): t["rect"] for t in tiles}
    for (row, col), r in at.items():
        right, below = at.get((row, col + 1)), at.get((row + 1, col))
        if right and right[0] < r[2] - EPS:
            out.append(f"L4: tile {row},{col} overlaps its right neighbour")
        if below and below[1] < r[3] - EPS:
            out.append(f"L4: tile {row},{col} overlaps the tile below")
    return out


def l5(s: dict, a: str, b: str) -> list[str]:
    """Named elements a and b don't overlap."""
    ra, rb = s["named"][a]["rect"], s["named"][b]["rect"]
    if min(ra[2], rb[2]) - max(ra[0], rb[0]) > EPS and min(ra[3], rb[3]) - max(ra[1], rb[1]) > EPS:
        return [f"L5: {a} overlaps {b}"]
    return []


def l6(s: dict, name: str) -> list[str]:
    """The named control is in view, uncovered and at least 48 px high."""
    c = s["named"][name]
    if c is None:
        return [f"L6: {name} is not visible"]
    out = [p for p in l2({**s, "controls": [c]}) + l3({**s, "controls": [c]})]
    if c["rect"][3] - c["rect"][1] < 48 - EPS:
        out.append(f"L6: {name} is {c['rect'][3] - c['rect'][1]:.1f} px high, under 48")
    return out


def l7(s: dict, name: str) -> list[str]:
    """The named element is docked to the bottom of the viewport."""
    bottom = s["named"][name]["rect"][3]
    return [] if abs(bottom - s["H"]) <= 1 else [f"L7: {name} ends at {bottom:.1f}, not at {s['H']}"]


def pitch(s: dict) -> float:
    """The smallest distance between neighbouring tile origins, across or down."""
    at = {(t["row"], t["col"]): t["rect"] for t in s["controls"] if t["tile"]}
    steps = [at[(r, c + 1)][0] - x[0] for (r, c), x in at.items() if (r, c + 1) in at]
    steps += [at[(r + 1, c)][1] - x[1] for (r, c), x in at.items() if (r + 1, c) in at]
    return min(steps)


L8_WATCH = """() => {
  const grid = document.getElementById("grid");
  window.__l8 = { mutations: 0 };
  new MutationObserver((list) => { window.__l8.mutations += list.length; })
    .observe(grid, { attributes: true, attributeFilter: ["style"] });
}"""

L8_SAMPLE = """() => new Promise((done) => {
  const grid = document.getElementById("grid");
  const sizes = [];
  let frame = 0;
  const step = () => {
    frame += 1;
    if (frame > 2) sizes.push(getComputedStyle(grid).getPropertyValue("--tile-size").trim());
    if (frame < 12) requestAnimationFrame(step); else done({ sizes, mutations: window.__l8.mutations });
  };
  requestAnimationFrame(step);
})"""


def l8(page: Page, width: int, height: int) -> list[str]:
    """Resize to width×height: the tile size settles within two frames, with at most 2 style writes.
    ResizeObserver loop errors are caught by the page's error collector (conftest.watch_errors)."""
    page.evaluate(L8_WATCH)
    page.set_viewport_size({"width": width, "height": height})
    got = page.evaluate(L8_SAMPLE)
    out = []
    if len(set(got["sizes"])) != 1:
        out.append(f"L8: --tile-size still changing after the resize: {got['sizes']}")
    if got["mutations"] > 2:
        out.append(f"L8: {got['mutations']} style writes on the grid after one resize")
    return out


def press(locator: Locator) -> None:
    """Tap with touch, click without it, so one flow serves phones and desktop."""
    if locator.page.evaluate("navigator.maxTouchPoints > 0"):
        locator.tap()
    else:
        locator.click()
