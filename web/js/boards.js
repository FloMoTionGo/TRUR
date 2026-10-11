// Past answers: a finished day's full word list, with the player's own finds marked (docs/plan.md, screen 6).
// The leaderboards join this module with T27. All text goes in with textContent (S8).

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
