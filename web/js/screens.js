// Which screen is showing, and which header parts go with it.

const SCREENS = ["loading", "nickname", "game", "over", "answers"];

export function showScreen(name) {
  for (const screen of SCREENS) {
    document.getElementById(`screen-${screen}`).hidden = screen !== name;
  }
  for (const el of document.querySelectorAll("[data-game-only]")) el.hidden = name !== "game";
  for (const el of document.querySelectorAll("[data-play-only]")) el.hidden = name !== "game" && name !== "over";
  for (const el of document.querySelectorAll("[data-answers-only]")) el.hidden = name !== "answers";
}

export function showStatus(text) {
  document.querySelector('[data-testid="status"]').textContent = text;
  showScreen("loading");
}
