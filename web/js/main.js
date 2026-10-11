// Boot, light/dark mode and screen switching.

import { api, ApiError } from "./api.js";
import { startGame } from "./game.js";

const THEME_KEY = "trur-theme";
const root = document.documentElement;
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

// --- light/dark: follows the system until the player picks one; the pick is a per-browser convenience ---

function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

let pageTheme = null; // the pick for this page view, used when storage is blocked

function applyTheme() {
  const chosen = storedTheme() || pageTheme;
  if (chosen) root.dataset.theme = chosen;
  else delete root.dataset.theme;
  const dark = chosen ? chosen === "dark" : darkQuery.matches;
  root.classList.toggle("is-dark", dark);
  document.querySelector('[data-testid="theme-toggle"]')
    .setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
}

function toggleTheme() {
  pageTheme = root.classList.contains("is-dark") ? "light" : "dark";
  try {
    localStorage.setItem(THEME_KEY, pageTheme);
  } catch {
    // storage blocked (private window, previews): the switch still holds for this page view
  }
  applyTheme();
}

// --- screens ---

const SCREENS = ["loading", "nickname", "game"];

export function showScreen(name) {
  for (const screen of SCREENS) {
    document.getElementById(`screen-${screen}`).hidden = screen !== name;
  }
  for (const el of document.querySelectorAll("[data-game-only]")) el.hidden = name !== "game";
}

function showStatus(text) {
  document.querySelector('[data-testid="status"]').textContent = text;
  showScreen("loading");
}

// --- nickname prompt (full rules and lookalike checks arrive with T16) ---

function askNickname() {
  const form = document.getElementById("nick-form");
  const input = document.getElementById("nick");
  const error = document.getElementById("nick-error");
  showScreen("nickname");
  input.focus();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    try {
      await api.register(input.value.trim());
      await play();
    } catch (err) {
      error.textContent = err instanceof ApiError ? err.message : "Something went wrong.";
      error.hidden = false;
    }
  });
}

async function play() {
  showScreen("game");
  await startGame("free");
}

async function boot() {
  applyTheme();
  darkQuery.addEventListener("change", applyTheme);
  document.querySelector('[data-testid="theme-toggle"]').addEventListener("click", toggleTheme);
  try {
    await api.me();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return askNickname();
    return showStatus(err.message);
  }
  try {
    await play();
  } catch (err) {
    showStatus(err instanceof ApiError ? err.message : "Something went wrong.");
  }
}

boot();
