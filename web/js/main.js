// Boot, light/dark mode and screen switching.

import { api, ApiError } from "./api.js";
import { notify, startGame } from "./game.js";
import { leaveMatch, openMatch, resumeMatch } from "./match.js";
import { showScreen, showStatus } from "./screens.js";

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
  // the browser bar on phones follows the in-app pick, not just the system setting
  document.querySelector('meta[name="theme-color"]')
    .setAttribute("content", getComputedStyle(root).getPropertyValue("--ground").trim());
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

// --- nickname prompt: shown when /api/users/me says 401; the server checks the rules ---

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
  await startGame("free");
}

// --- the Free | Daily | Multi switch ---

for (const button of document.querySelectorAll(".modes button")) {
  button.addEventListener("click", async () => {
    if (button.dataset.mode === "match") return openMatch();
    try {
      await leaveMatch(); // leaves the queue; a running match goes on and Multi returns to it
      await startGame(button.dataset.mode);
    } catch (err) {
      notify(`Couldn’t open ${button.textContent}`, err instanceof ApiError ? err.message : "Something went wrong.");
    }
  });
}

// iOS applies :active (the press feedback on phones) only to pages with a touch listener.
document.addEventListener("touchstart", () => {}, { passive: true });

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
    if (await resumeMatch()) return; // a results link, or a queue or match still running after a reload
    await play();
  } catch (err) {
    showStatus(err instanceof ApiError ? err.message : "Something went wrong.");
  }
}

boot();
