// A daily result to share, without spoilers (tasks/todo.md L2): how the game went, and no word, letter
// or source word. Built in the browser from the game state the page already has.

import { dayLabel } from "./boards.js";

const BLOCK = "▮"; // ▮, one per tile of a found word

// TRUR daily · Sun 11 Oct
// 42 points · 7 of 23 words · longest 10 letters
// ▮▮ ▮▮ ▮▮▮ ▮▮▮ ▮▮▮ ▮▮▮▮ ▮▮▮▮▮
// trur.fly.dev
export function shareText(game, host) {
  const lengths = game.found.map((f) => f.word.length).sort((a, b) => a - b); // shortest first: no play order either
  const facts = [
    `${game.score} ${game.score === 1 ? "point" : "points"}`,
    `${game.found_count} of ${game.total_words} words`,
  ];
  if (lengths.length) facts.push(`longest ${lengths[lengths.length - 1]} letters`);
  const lines = [`TRUR daily · ${dayLabel(game.daily_date)}`, facts.join(" · ")];
  if (lengths.length) lines.push(lengths.map((n) => BLOCK.repeat(n / 2)).join(" "));
  lines.push(host);
  return lines.join("\n");
}

// The share sheet on touch devices that have one, the clipboard elsewhere (a desktop share dialog is more
// in the way than a paste). Resolves to "shared", "copied", "cancelled" or "failed".
export async function share(text) {
  const touch = window.matchMedia("(hover: none) and (pointer: coarse)").matches;
  if (touch && navigator.share) {
    try {
      await navigator.share({ text });
      return "shared";
    } catch (err) {
      if (err && err.name === "AbortError") return "cancelled"; // the player closed the sheet
    }
  }
  try {
    await navigator.clipboard.writeText(text);
    return "copied";
  } catch {
    return "failed";
  }
}
