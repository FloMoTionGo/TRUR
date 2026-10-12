// Past answers: a finished day's full word list, with the player's own finds marked (docs/plan.md, screen 6).
// Then the leaderboards (screen 4). All text goes in with textContent (S8).

import { api, ApiError } from "./api.js";
import { showScreen } from "./screens.js";

const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`);
const list = byTestId("answers-list");
const empty = byTestId("answers-empty");
const you = byTestId("answers-you");
const meta = byTestId("answers-meta");
const prev = byTestId("answers-prev");
const next = byTestId("answers-next");
const legend = document.getElementById("answers-legend");

const DAY_MS = 86_400_000;
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

let day = null;      // the date on screen, YYYY-MM-DD
let lastDay = null;  // the latest day whose answers are out
let onBack = null;

export const addDays = (iso, n) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * DAY_MS).toISOString().slice(0, 10);

export function dayLabel(iso, withYear = false) {
  const d = new Date(`${iso}T00:00:00Z`);
  const label = `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  return withYear ? `${label} ${d.getUTCFullYear()}` : label;
}

async function load(iso) {
  try {
    return { kind: "ok", data: await api.answers(iso) };
  } catch (err) {
    if (err instanceof ApiError && err.code === "day_not_over") return { kind: "hidden" };
    if (err instanceof ApiError && err.status === 404) return { kind: "missing" };
    return { kind: "error", message: err instanceof ApiError ? err.message : "Couldn’t load the answers." };
  }
}

// Opens on the game's own day once it's over, else on the day before.
export async function showAnswers(gameDay, back) {
  onBack = back;
  lastDay = gameDay;
  let result = await load(gameDay);
  if (result.kind === "hidden") {
    lastDay = addDays(gameDay, -1);
    result = await load(lastDay);
  }
  render(lastDay, result);
  showScreen("answers");
}

function render(iso, result) {
  day = iso;
  byTestId("answers-date").textContent = dayLabel(iso, true);
  next.disabled = iso >= lastDay;
  list.replaceChildren();
  you.hidden = true;
  if (result.kind !== "ok") {
    meta.textContent = "";
    legend.hidden = true;
    empty.textContent = {
      missing: "There was no daily challenge on that day.",
      hidden: "These answers appear after midnight UTC.",
    }[result.kind] || result.message;
    empty.hidden = false;
    return;
  }
  const answers = result.data;
  const mine = new Set(answers.found || []);
  const total = answers.words.reduce((sum, w) => sum + w.points, 0);
  meta.textContent = `${answers.word1} × ${answers.word2} · ${answers.total_words} words · ${total} points`;
  legend.hidden = false;
  empty.hidden = true;
  list.replaceChildren(...answers.words.map((w) => {
    const li = document.createElement("li");
    const word = document.createElement("span");
    const points = document.createElement("span");
    word.textContent = w.word;
    points.textContent = String(w.points);
    li.dataset.mine = String(mine.has(w.word));
    li.append(word, points);
    return li;
  }));
  if (answers.found) {
    const points = answers.words.filter((w) => mine.has(w.word)).reduce((sum, w) => sum + w.points, 0);
    you.textContent = `You: ${answers.found.length} ${answers.found.length === 1 ? "word" : "words"} · ${points}`;
    you.hidden = false;
  }
}

async function go(step) {
  const iso = addDays(day, step);
  render(iso, await load(iso));
}

prev.addEventListener("click", () => go(-1));
next.addEventListener("click", () => { if (day < lastDay) go(1); });
byTestId("answers-back").addEventListener("click", () => { if (onBack) onBack(); });

// --- leaderboards (docs/plan.md, screen 4): monthly per mode and one day's daily board ---

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];
const BOARDS = {
  free: { hint: "Best single game", daily: false, fetch: (month) => api.monthlyBoard("free", month) },
  daily: { hint: "Best daily game", daily: false, fetch: (month) => api.monthlyBoard("daily", month) },
  today: { hint: "One game per player", daily: true, fetch: (iso) => api.dailyBoard(iso) },
};
const tabs = [...document.querySelectorAll('#screen-boards [role="tab"]')];
const table = document.getElementById("boards-table");
const rows = byTestId("boards-rows");
const boardEmpty = byTestId("boards-empty");
const boardPrev = byTestId("boards-prev");
const boardNext = byTestId("boards-next");

// month is shared by both monthly tabs; null asks the server for the current month / today (UTC)
const board = { tab: "free", month: null, day: null, lastMonth: null, lastDay: null, me: null, request: 0, back: null };

const addMonths = (ym, n) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 7);
};
const monthLabel = (ym) => `${MONTH_NAMES[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;

// Opens on this month's free-play board.
export async function showBoards(back) {
  Object.assign(board, { back, tab: "free", month: null, day: null, lastMonth: null, lastDay: null });
  if (board.me === null) {
    try {
      board.me = (await api.me()).nickname;
    } catch {
      board.me = ""; // the boards still work; no row is marked as yours
    }
  }
  await loadBoard();
  showScreen("boards");
}

async function loadBoard() {
  const spec = BOARDS[board.tab];
  const request = ++board.request;
  let data = null;
  let message = null;
  try {
    data = await spec.fetch(spec.daily ? board.day : board.month);
  } catch (err) {
    message = err instanceof ApiError ? err.message : "Couldn’t load the board.";
  }
  if (request === board.request) renderBoard(spec, data, message); // a later click wins
}

function renderBoard(spec, data, message) {
  for (const tab of tabs) {
    const on = tab.dataset.tab === board.tab;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
  }
  byTestId("boards-hint").textContent = spec.hint;
  if (data && spec.daily) {
    board.day = data.date;
    board.lastDay ??= data.date;
  } else if (data) {
    board.month = data.month;
    board.lastMonth ??= data.month;
  }
  const period = spec.daily ? board.day : board.month;
  const last = spec.daily ? board.lastDay : board.lastMonth;
  byTestId("boards-period").textContent = period ? (spec.daily ? dayLabel(period, true) : monthLabel(period)) : "";
  boardPrev.disabled = !period;
  boardNext.disabled = !period || period >= last;

  const entries = data ? data.entries : [];
  rows.replaceChildren(...entries.map((entry) => {
    const tr = document.createElement("tr");
    const me = entry.nickname === board.me;
    tr.dataset.me = String(me);
    for (const text of [String(entry.rank), me ? `${entry.nickname} (you)` : entry.nickname, String(entry.score)]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    return tr;
  }));
  table.hidden = entries.length === 0;
  boardEmpty.textContent = message || "No scores yet.";
  boardEmpty.hidden = entries.length > 0;
}

function pickTab(tab) {
  board.tab = tab.dataset.tab;
  tab.focus();
  loadBoard();
}

function step(n) {
  if (BOARDS[board.tab].daily) board.day = addDays(board.day, n);
  else board.month = addMonths(board.month, n);
  loadBoard();
}

for (const tab of tabs) tab.addEventListener("click", () => pickTab(tab));
// Arrow keys move between the tabs (the ARIA tabs pattern).
document.querySelector("#screen-boards [role=tablist]").addEventListener("keydown", (event) => {
  const move = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
  if (!move) return;
  event.preventDefault();
  const i = tabs.findIndex((tab) => tab.dataset.tab === board.tab);
  pickTab(tabs[(i + move + tabs.length) % tabs.length]);
});
boardPrev.addEventListener("click", () => step(-1));
boardNext.addEventListener("click", () => step(1));
byTestId("boards-back").addEventListener("click", () => { if (board.back) board.back(); });
