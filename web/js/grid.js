// The letter grid: its tiles and their size (docs/mobile.md §5.2). Shared with multiplayer (M6).
// Sizes reach the page only as custom properties on the grid; the numbers come from app.css.

// Portrait phones. Keep in step with the (max-width: 639px) block in app.css.
export const PHONE_QUERY = "(max-width: 639px)";

const px = (style, name) => parseFloat(style.getPropertyValue(name));

function head(letter, className) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = letter;
  return span;
}

// word1 runs across (one column per letter), word2 down. Only for a new game: resizing never rebuilds.
export function buildGrid(el, word1, word2) {
  el.setAttribute("aria-label", `Letter grid: ${word1} across, ${word2} down`);
  el.style.setProperty("--cols", String(word1.length));
  el.style.setProperty("--rows", String(word2.length));
  el.replaceChildren(document.createElement("span"));
  for (const letter of word1) el.append(head(letter, "head head-col"));
  [...word2].forEach((rowLetter, row) => {
    el.append(head(rowLetter, "head"));
    [...word1].forEach((colLetter, col) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "tile";
      tile.dataset.row = String(row);
      tile.dataset.col = String(col);
      tile.dataset.state = "default";
      tile.textContent = colLetter + rowLetter;
      tile.setAttribute("aria-pressed", "false");
      el.append(tile);
    });
  });
}

// Desktop sizes tiles for a fixed box (--fit-w × --fit-h, minus --won-h under the won banner), so they stay
// exactly as approved. Phones set --fit-w to none and measure the grid area instead. Writes only when the
// size or gap changes, so a resize can't loop.
export function fitGrid(el) {
  const cols = Number(el.style.getPropertyValue("--cols"));
  const rows = Number(el.style.getPropertyValue("--rows"));
  if (!cols || !rows) return;
  const style = getComputedStyle(el);
  const gap = px(style, Math.max(cols, rows) >= 8 ? "--gap-dense" : "--gap-loose");
  let width = px(style, "--fit-w");
  let height;
  if (Number.isNaN(width)) {
    const area = el.parentElement;
    const box = area.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) return; // the screen is hidden
    const pad = getComputedStyle(area);
    width = box.width - px(pad, "padding-left") - px(pad, "padding-right");
    height = box.height - px(pad, "padding-top") - px(pad, "padding-bottom");
  } else {
    const banner = el.closest(".screen")?.querySelector(".won");
    height = px(style, "--fit-h") - (banner && !banner.hidden ? px(style, "--won-h") : 0);
  }
  const headSize = px(style, "--head");
  const size = Math.min(
    px(style, "--max-tile"),
    Math.floor((width - headSize - gap * cols) / cols),
    Math.floor((height - headSize - gap * rows) / rows),
  );
  const key = `${size}/${gap}`;
  if (el.dataset.fit === key) return;
  el.dataset.fit = key;
  el.style.setProperty("--tile-size", `${size}px`);
  el.style.setProperty("--gap", `${gap}px`);
  el.dataset.dense = String(size < 36);
}

// Refit when the grid area changes size: rotation, the won banner, the browser bars.
export function observeGrid(el) {
  new ResizeObserver(() => fitGrid(el)).observe(el.parentElement);
}
