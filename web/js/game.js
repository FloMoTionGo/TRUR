// The game screen: grid, selection, submit and feedback, for free and daily games.
// All text goes in with textContent (S8).

import { api, ApiError } from "./api.js";
import { addDays, dayLabel, showAnswers } from "./boards.js";
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
const WON_HEIGHT = 70;    // the daily "won" banner, margin included
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

// Free resumes the open game or starts one. Daily always asks for today's game, which the
// server creates once and then returns, open or over.
export async function startGame(mode) {
  if (mode === "daily") return show(await api.newGame("daily"));
  const current = await api.currentGame("free");
  show(current.game || (await api.newGame("free")));
}

function show(state) {
  game = state;
  selection = [];
  for (const button of document.querySelectorAll(".modes button")) {
    if (button.dataset.mode === game.mode) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (game.game_over) return showGameOver();
  buildGrid();
  render();
  showScreen("game");
}

export function notify(title, body) {
  showToast("rejected", title, body);
}

const isWon = () => game.mode === "daily" && game.won;

// "after midnight UTC (8:00 PM your time)": the reader's own clock for the moment the day ends.
function answersNote() {
  const end = new Date(`${addDays(game.daily_date, 1)}T00:00:00Z`);
  const local = end.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `The full answer list appears after midnight UTC (${local} your time).`;
}

// --- rendering ---

function buildGrid() {
  const cols = game.word1.length;
  const rows = game.word2.length;
  const gap = Math.max(cols, rows) >= 8 ? 4 : 6;
  const size = Math.min(
    MAX_TILE,
    Math.floor((GRID_WIDTH - HEAD - gap * cols) / cols),
    Math.floor((GRID_HEIGHT - (isWon() ? WON_HEIGHT : 0) - HEAD - gap * rows) / rows),
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

  const won = byTestId("won");
  won.hidden = !isWon();
  if (isWon()) {
    const word = game.found.map((f) => f.word).find((w) => w.length >= 10);
    document.getElementById("won-title").textContent = `Daily challenge won · ${dayLabel(game.daily_date)}`;
    document.getElementById("won-body").textContent =
      word ? `You found ${word} (${word.length} letters). Keep going for points.` : "Keep going for points.";
  }
  document.getElementById("found-foot").hidden = game.mode !== "daily";
  if (game.mode === "daily") byTestId("answers-note").textContent = answersNote();
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
    const wasWon = isWon();
    Object.assign(game, {
      score: result.score, tries_left: result.tries_left, found_count: result.found_count,
      total_words: result.total_words, won: result.won, game_over: result.game_over,
      missed_words: result.missed_words,
    });
    if (result.status === "accepted") {
      game.found = [...game.found, { word: result.word, points: result.points }];
      if (!wasWon && isWon()) buildGrid(); // the banner takes room from the grid
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
    if (game && game.mode === "daily") await startGame("free"); // one daily game a day: offer free play
    else show(await api.newGame("free"));
  } catch (err) {
    showToast("rejected", "Couldn’t start a game", err instanceof ApiError ? err.message : "Something went wrong.");
  }
});

const openAnswers = () => showAnswers(game.daily_date, () => show(game)); // back to the screen it came from
byTestId("past-answers").addEventListener("click", openAnswers);
byTestId("past-answers-over").addEventListener("click", openAnswers);

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
  const prefix = isWon() ? "Daily challenge won · " : "";
  byTestId("over-reason").textContent = `${prefix}${reason} · ${game.word1} × ${game.word2}`;
  byTestId("over-score").textContent = String(game.score);
  byTestId("over-meta").textContent = `points · ${game.found_count} of ${game.total_words} words`;
  byTestId("over-found").replaceChildren(...game.found.map((f) => wordItem(f.word, f.points)));
  const daily = game.mode === "daily";
  const missed = game.missed_words || [];
  document.getElementById("over-missed-head").textContent = `Missed words · ${missed.length}`;
  byTestId("missed").replaceChildren(...missed.map((w) => wordItem(w, wordPoints(w))));
  document.getElementById("over-missed-section").hidden = daily; // daily answers stay hidden until midnight UTC
  document.getElementById("over-hidden").hidden = !daily;
  if (daily) byTestId("over-answers-note").textContent = answersNote();
  byTestId("over-note").textContent = daily ? "A new pair comes at midnight UTC." : "";
  byTestId("past-answers-over").hidden = !daily;
  byTestId("new-game").textContent = daily ? "Play free" : "New game";
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
