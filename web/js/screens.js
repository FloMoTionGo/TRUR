// Which screen is showing, and which header parts go with it.

import { closeSheet } from "./sheet.js";

const SCREENS = ["loading", "nickname", "game", "over", "answers", "boards"];

export function showScreen(name) {
  closeSheet(); // a screen change never leaves the found sheet open over it
  for (const screen of SCREENS) {
    document.getElementById(`screen-${screen}`).hidden = screen !== name;
  }
  for (const el of document.querySelectorAll("[data-game-only]")) el.hidden = name !== "game";
  for (const el of document.querySelectorAll("[data-play-only]")) el.hidden = name !== "game" && name !== "over";
  for (const el of document.querySelectorAll("[data-answers-only]")) el.hidden = name !== "answers";
  for (const el of document.querySelectorAll("[data-boards-only]")) el.hidden = name !== "boards";
}

// Answers and boards open over the game with a history entry of their own (docs/mobile.md §6.7), so the
// Back gesture returns to the screen they came from instead of leaving the site. The URL stays the same,
// so a reload shows the game.
let goBack = null;

export function pushScreen(back) {
  if (goBack === null) history.pushState({ trur: "over-game" }, "");
  goBack = back;
}

// The in-app Back button: undo the entry, and popstate does the rest.
export function popScreen() {
  if (goBack !== null) history.back();
}

window.addEventListener("popstate", () => {
  const back = goBack;
  goBack = null;
  if (back) back();
});

export function showStatus(text) {
  document.querySelector('[data-testid="status"]').textContent = text;
  showScreen("loading");
}
