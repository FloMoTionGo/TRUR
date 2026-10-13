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

export function showStatus(text) {
  document.querySelector('[data-testid="status"]').textContent = text;
  showScreen("loading");
}
