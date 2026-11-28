// The player's daily record (tasks/todo.md L1): four numbers and the last 30 days as blocks, shown on the
// daily game-over screen and on the leaderboards' "You" tab, plus the streak badge on the header button.
// All text goes in with textContent (S8).

import { api } from "./api.js";

const opener = document.querySelector('[data-testid="boards-open"]');
const badge = document.querySelector('[data-testid="streak"]');
const OPENER_LABEL = "Leaderboards and your statistics";
const BADGE_FROM = 2; // a streak of one day is no news yet

export const daysText = (n) => (n === 1 ? "1 day in a row" : `${n} days in a row`);

// The server's clock, as far as the last statistics response told us: a device clock that is hours off
// must not move the countdown to the next daily. Until then the device's own time.
let clockOffset = 0;
export const serverNow = () => Date.now() + clockOffset;

function make(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

// The badge is drawn over the button and hidden from screen readers; the button's label says the same.
function showStreak(streak) {
  const on = streak >= BADGE_FROM;
  opener.dataset.streak = String(streak);
  opener.setAttribute("aria-label", on ? `${OPENER_LABEL}, ${daysText(streak)}` : OPENER_LABEL);
  badge.textContent = String(streak);
  badge.hidden = !on;
}

// The record, or null when it can't be loaded: the game goes on without it.
export async function loadStats() {
  try {
    const data = await api.stats();
    const serverTime = Date.parse(data.server_time);
    if (!Number.isNaN(serverTime)) clockOffset = serverTime - Date.now();
    showStreak(data.current_streak);
    return data;
  } catch {
    return null;
  }
}

function number(key, label, value) {
  const item = make("div");
  item.dataset.stat = key;
  item.append(make("dt", "", label), make("dd", "", value));
  return item;
}

export function renderStats(root, data) {
  if (!data) return root.replaceChildren(make("p", "stats-more", "Couldn’t load your statistics."));
  const rate = data.played ? Math.round((100 * data.won) / data.played) : 0;
  const numbers = make("dl", "stats-nums");
  numbers.append(
    number("played", "Played", String(data.played)),
    number("rate", "Win %", String(rate)),
    number("streak", "Streak", String(data.current_streak)),
    number("best", "Best streak", String(data.best_streak)),
  );

  const head = make("div", "stats-days-head");
  const legend = make("span", "stats-legend");
  for (const kind of ["played", "won"]) {
    const swatch = make("span", "stats-swatch");
    swatch.dataset.day = kind;
    legend.append(swatch, kind);
  }
  head.append(make("span", "", `Last ${data.days.length} days`), legend);

  const days = make("div", "stats-days");
  const played = data.days.filter((d) => d.played).length;
  const won = data.days.filter((d) => d.won).length;
  days.setAttribute("role", "img");
  days.setAttribute("aria-label", `Last ${data.days.length} days: played on ${played}, won on ${won}. Today is last.`);
  days.append(...data.days.map((d) => {
    const block = make("span");
    block.dataset.day = d.won ? "won" : d.played ? "played" : "none";
    return block;
  }));

  const more = data.played
    ? `Best score ${data.best_score} · Longest word ${data.longest_word || "–"}`
    : "Find a word in the daily to start a streak.";
  root.replaceChildren(numbers, head, days, make("p", "stats-more", more));
}
