// The game screen: grid, selection, submit and feedback, for free and daily games.
// All text goes in with textContent (S8).

import { api, ApiError } from "./api.js";
import { addDays, dayLabel, showAnswers, showBoards } from "./boards.js";
import { logEvent } from "./events.js";
import { buildGrid, fitGrid, observeGrid, PHONE_QUERY } from "./grid.js";
import { showScreen } from "./screens.js";
import { openSheet } from "./sheet.js";

const grid = document.getElementById("grid");
const foundList = document.getElementById("found-list");
const chips = document.getElementById("chips");
const preview = document.getElementById("preview");
const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`);
const submitButton = byTestId("submit");
const clearButton = byTestId("clear");

const REJECT_MS = 420;
const TOAST_MS = 3200;
const FLASH_MS = 1000;

let game = null;       // GameState from the API
let selection = [];    // [[row, col], …] in click order
let busy = false;
let toastTimer = 0;
let flashTimer = 0;
let wonDismissed = false; // phones can dismiss the won banner to get its room back for the grid
const coarse = window.matchMedia("(hover: none) and (pointer: coarse)");

observeGrid(grid);

// Text that only changes now and then is written only when it changes, so live regions stay quiet.
function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

// Announcements go through two persistent regions; clearing first lets the same message be read again.
const regions = { status: document.getElementById("sr-status"), alert: document.getElementById("sr-alert") };
function announce(kind, text) {
  regions[kind].textContent = "";
  requestAnimationFrame(() => { regions[kind].textContent = text; });
}

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
  wonDismissed = false;
  for (const button of document.querySelectorAll(".modes button")) {
    if (button.dataset.mode === game.mode) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (game.game_over) return showGameOver();
  buildGrid(grid, game.word1, game.word2);
  render();
  showScreen("game");
  fitGrid(grid); // the grid was built while hidden; on phones it can only be measured now
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
  const hint = coarse.matches ? "Tap tiles to build a word" : "Click tiles to build a word";
  preview.textContent = game.game_over ? "Game over" : word ? `→ ${word}` : hint;

  foundList.replaceChildren(...game.found.map((f) => wordItem(f.word, f.points)));
  setText(byTestId("counter"), `Found ${game.found_count} of ${game.total_words}`);
  setText(byTestId("found-open").firstElementChild, `Found ${game.found_count} of ${game.total_words}`);
  byTestId("score").textContent = String(game.score);
  const tries = byTestId("tries");
  tries.setAttribute("aria-label", `${game.tries_left} of 3 tries left`);
  [...tries.children].forEach((dot, i) => { dot.dataset.full = String(i < game.tries_left); });

  submitButton.disabled = busy || game.game_over || selection.length < 2;
  clearButton.disabled = busy || selection.length === 0;

  const won = byTestId("won");
  won.hidden = !isWon() || wonDismissed;
  fitGrid(grid); // the banner takes room from the grid
  if (isWon()) {
    const word = game.found.map((f) => f.word).find((w) => w.length >= 10);
    setText(document.getElementById("won-title"), `Daily challenge won · ${dayLabel(game.daily_date)}`);
    setText(document.getElementById("won-body"),
      word ? `You found ${word} (${word.length} letters). Keep going for points.` : "Keep going for points.");
  }
  document.getElementById("found-foot").hidden = game.mode !== "daily";
  if (game.mode === "daily") byTestId("answers-note").textContent = answersNote();
}

// --- feedback popup ---

function showToast(kind, title, body, hint = "") {
  const toast = document.getElementById("toast");
  toast.dataset.kind = kind;
  document.getElementById("toast-title").textContent = title;
  document.getElementById("toast-body").textContent = body;
  const hintEl = document.getElementById("toast-hint");
  hintEl.textContent = hint;
  hintEl.hidden = !hint;
  toast.hidden = false;
  announce(kind === "rejected" ? "alert" : "status", `${title}. ${body}`); // the toast itself has no role
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, TOAST_MS);
}

function hideToast() {
  clearTimeout(toastTimer);
  document.getElementById("toast").hidden = true;
}

function triesText(left) {
  if (left === 0) return "That was your last try.";
  return `You lose a try: ${left} ${left === 1 ? "try" : "tries"} left.`;
}

// --- actions ---

const phone = window.matchMedia(PHONE_QUERY);

grid.addEventListener("click", (event) => {
  const tile = event.target.closest(".tile");
  if (!tile || !game || busy || game.game_over) return;
  if (phone.matches) hideToast(); // on phones the toast covers the action area, so the next tap clears it
  const row = Number(tile.dataset.row);
  const col = Number(tile.dataset.col);
  const i = isSelected(row, col);
  selection = i >= 0 ? selection.filter((_, j) => j !== i) : [...selection, [row, col]];
  render();
  logEvent({ type: "tile_clicked", game_id: game.game_id, row, col, selected: i < 0 });
});

byTestId("won-close").addEventListener("click", () => {
  wonDismissed = true;
  render();
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
      flashFound(result.points);
      announce("status", `${result.word}, ${result.points} points. ${result.found_count} of ${result.total_words} found.`);
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
byTestId("boards-open").addEventListener("click", () => showBoards(() => show(game)));

// --- the found list: a side panel on desktop, a sheet on phones (docs/mobile.md §6.4) ---

const foundOpen = byTestId("found-open");
foundOpen.addEventListener("click", () => openSheet(document.querySelector(".found"), foundOpen));

// "+4" on the Found button; data-flash alternates a/b so a second word restarts the animation.
function flashFound(points) {
  foundOpen.lastElementChild.textContent = `+${points}`;
  foundOpen.dataset.flash = foundOpen.dataset.flash === "a" ? "b" : "a";
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { delete foundOpen.dataset.flash; }, FLASH_MS);
}

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
