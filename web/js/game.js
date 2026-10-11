// The game screen: grid, selection, submit and feedback. All text goes in with textContent (S8).

import { api, ApiError } from "./api.js";
import { logEvent } from "./events.js";
import { showScreen } from "./screens.js";

const grid = document.getElementById("grid");
const foundList = document.getElementById("found-list");
const chips = document.getElementById("chips");
const preview = document.getElementById("preview");
const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`);
const submitButton = byTestId("submit");
const clearButton = byTestId("clear");

const GRID_WIDTH = 410;   // px available for the grid beside the found list
const GRID_HEIGHT = 470;  // px available between the header and the solution area
const HEAD = 24;          // row-letter column and column-letter row
const MAX_TILE = 48;
const REJECT_MS = 420;
const TOAST_MS = 3200;

let game = null;       // GameState from the API
let selection = [];    // [[row, col], …] in click order
let busy = false;
let toastTimer = 0;

const pair = ([row, col]) => game.word1[col] + game.word2[row];
const isSelected = (row, col) => selection.findIndex(([r, c]) => r === row && c === col);

export async function startGame(mode) {
  const current = await api.currentGame(mode);
  show(current.game || (await api.newGame(mode)));
}

function show(state) {
  game = state;
  selection = [];
  buildGrid();
  render();
  showScreen("game");
}

// --- rendering ---

function buildGrid() {
  const cols = game.word1.length;
  const rows = game.word2.length;
  const gap = Math.max(cols, rows) >= 8 ? 4 : 6;
  const size = Math.min(
    MAX_TILE,
    Math.floor((GRID_WIDTH - HEAD - gap * cols) / cols),
    Math.floor((GRID_HEIGHT - HEAD - gap * rows) / rows),
  );
  grid.style.setProperty("--cols", String(cols));
  grid.style.setProperty("--rows", String(rows));
  grid.style.setProperty("--tile-size", `${size}px`);
  grid.style.setProperty("--gap", `${gap}px`);
  grid.replaceChildren(document.createElement("span"));
  for (const letter of game.word1) grid.append(head(letter, "head head-col"));
  [...game.word2].forEach((rowLetter, row) => {
    grid.append(head(rowLetter, "head"));
    [...game.word1].forEach((colLetter, col) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "tile";
      tile.dataset.row = String(row);
      tile.dataset.col = String(col);
      tile.dataset.state = "default";
      tile.textContent = colLetter + rowLetter;
      tile.setAttribute("aria-pressed", "false");
      grid.append(tile);
    });
  });
}

function head(letter, className) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = letter;
  return span;
}

function render() {
  for (const tile of grid.querySelectorAll(".tile")) {
    if (tile.dataset.state === "rejected") continue;
    const on = isSelected(Number(tile.dataset.row), Number(tile.dataset.col)) >= 0;
    tile.dataset.state = on ? "selected" : "default";
    tile.setAttribute("aria-pressed", String(on));
    tile.disabled = game.game_over;
  }
  chips.replaceChildren(...selection.map((t) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = pair(t);
    return chip;
  }));
  const word = selection.map(pair).join("");
  preview.dataset.empty = String(!word);
  preview.textContent = game.game_over ? "Game over" : word ? `→ ${word}` : "Click tiles to build a word";

  foundList.replaceChildren(...game.found.map((f) => wordItem(f.word, f.points)));
  byTestId("counter").textContent = `Found ${game.found_count} of ${game.total_words}`;
  byTestId("score").textContent = String(game.score);
  const tries = byTestId("tries");
  tries.setAttribute("aria-label", `${game.tries_left} of 3 tries left`);
  [...tries.children].forEach((dot, i) => { dot.dataset.full = String(i < game.tries_left); });

  submitButton.disabled = busy || game.game_over || selection.length < 2;
  clearButton.disabled = busy || selection.length === 0;
}

// --- feedback popup ---

function showToast(kind, title, body, hint = "") {
  const toast = document.getElementById("toast");
  toast.dataset.kind = kind;
  toast.setAttribute("role", kind === "rejected" ? "alert" : "status");
  document.getElementById("toast-title").textContent = title;
  document.getElementById("toast-body").textContent = body;
  const hintEl = document.getElementById("toast-hint");
  hintEl.textContent = hint;
  hintEl.hidden = !hint;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, TOAST_MS);
}

function triesText(left) {
  if (left === 0) return "That was your last try.";
  return `You lose a try: ${left} ${left === 1 ? "try" : "tries"} left.`;
}

// --- actions ---

grid.addEventListener("click", (event) => {
  const tile = event.target.closest(".tile");
  if (!tile || !game || busy || game.game_over) return;
  const row = Number(tile.dataset.row);
  const col = Number(tile.dataset.col);
  const i = isSelected(row, col);
  selection = i >= 0 ? selection.filter((_, j) => j !== i) : [...selection, [row, col]];
  render();
  logEvent({ type: "tile_clicked", game_id: game.game_id, row, col, selected: i < 0 });
});

clearButton.addEventListener("click", () => {
  selection = [];
  render();
  if (game) logEvent({ type: "selection_cleared", game_id: game.game_id });
});

submitButton.addEventListener("click", async () => {
  if (busy || !game || selection.length < 2) return;
  const tiles = selection;
  busy = true;
  render();
  try {
    const result = await api.submit(game.game_id, tiles);
    Object.assign(game, {
      score: result.score, tries_left: result.tries_left, found_count: result.found_count,
      total_words: result.total_words, won: result.won, game_over: result.game_over,
      missed_words: result.missed_words,
    });
    if (result.status === "accepted") {
      game.found = [...game.found, { word: result.word, points: result.points }];
    } else if (result.status === "duplicate") {
      showToast("duplicate", `${result.word} is already in your list`, "You found it earlier. No points, and no try used.");
    } else {
      showToast("rejected", `${result.word} isn’t in the dictionary`,
        `Words are checked against the Canadian English dictionary. ${triesText(result.tries_left)}`,
        "Every word alternates a column letter and a row letter, so it always has an even number of letters.");
      await flashRejected(tiles);
    }
  } catch (err) {
    if (err instanceof ApiError && err.code === "rate_limited") {
      showToast("duplicate", "Not so fast", "Wait a second between words. No try used.");
    } else if (err instanceof ApiError && (err.code === "game_over" || err.code === "day_closed")) {
      game.game_over = true;
      showToast("duplicate", "This game is over", err.message);
    } else {
      showToast("rejected", "Couldn’t submit", err instanceof ApiError ? err.message : "Something went wrong.");
    }
  } finally {
    selection = [];
    busy = false;
    render();
  }
  if (game.game_over) showGameOver();
});

byTestId("end").addEventListener("click", async () => {
  if (busy || !game || game.game_over) return;
  busy = true;
  render();
  try {
    game = await api.end(game.game_id);
  } catch (err) {
    if (!(err instanceof ApiError && (err.code === "game_over" || err.code === "day_closed"))) {
      showToast("rejected", "Couldn’t end the game", err instanceof ApiError ? err.message : "Something went wrong.");
      return;
    }
    game.game_over = true;
  } finally {
    busy = false;
    render();
  }
  showGameOver();
});

byTestId("new-game").addEventListener("click", async () => {
  try {
    show(await api.newGame(game ? game.mode : "free"));
  } catch (err) {
    showToast("rejected", "Couldn’t start a game", err instanceof ApiError ? err.message : "Something went wrong.");
  }
});

// --- game over ---

const wordPoints = (word) => (word.length / 2) ** 2;

function wordItem(word, points) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  const pts = document.createElement("span");
  name.textContent = word;
  pts.className = "pts";
  pts.textContent = String(points);
  li.append(name, pts);
  return li;
}

function showGameOver() {
  document.getElementById("toast").hidden = true;
  const reason = game.tries_left === 0 ? "Three wrong words"
    : game.found_count === game.total_words ? "Every word found" : "You ended the game";
  byTestId("over-reason").textContent = `${reason} · ${game.word1} × ${game.word2}`;
  byTestId("over-score").textContent = String(game.score);
  byTestId("over-meta").textContent = `points · ${game.found_count} of ${game.total_words} words`;
  byTestId("over-found").replaceChildren(...game.found.map((f) => wordItem(f.word, f.points)));
  const missed = game.missed_words || [];
  document.getElementById("over-missed-head").textContent = `Missed words · ${missed.length}`;
  byTestId("missed").replaceChildren(...missed.map((w) => wordItem(w, wordPoints(w))));
  showScreen("over");
}

function flashRejected(tiles) {
  for (const [row, col] of tiles) {
    const tile = grid.querySelector(`.tile[data-row="${row}"][data-col="${col}"]`);
    tile.dataset.state = "rejected";
  }
  return new Promise((resolve) => setTimeout(() => {
    for (const tile of grid.querySelectorAll('.tile[data-state="rejected"]')) tile.dataset.state = "default";
    resolve();
  }, REJECT_MS));
}
