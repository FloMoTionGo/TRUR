// Multiplayer (docs/multiplayer.md): the queue, the 3-2-1, the match, the creativity vote and the results.
// Live updates come from js/stream.js. All text goes in with textContent (S8, M6).

import { api, ApiError } from "./api.js";
import { startGame } from "./game.js";
import { buildGrid, fitGrid, observeGrid } from "./grid.js";
import { showScreen } from "./screens.js";
import { openStream } from "./stream.js";

const $ = (id) => document.getElementById(id);
const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`);

const grid = $("m-grid");
const POPUP_MS = 3500;     // popups stay at least 3 s (TS §6, step M6)
const MAX_POPUPS = 4;
const REJECT_MS = 420;
const WARNING_MS = 10000;  // the 10-s warning (§3.1)
const MIN_TILES = 2;
const RANK_POINTS = [5, 3, 1];
const RANK_NAMES = ["1st", "2nd", "3rd"];
const RESULTS_LINK = /^#match\/([0-9a-f-]{36})$/;
// The phases a stream belongs to; "message" and "results" have none.
const ACTIVE = new Set(["queued", "forming", "countdown", "running", "voting"]);

let phase = "idle";   // idle | queued | forming | countdown | running | voting | results | message
let stream = null;
let match = null;     // { id, players, word1, word2, pct: [row][col], locked: Set("r,c"), found: [], showcase }
let queue = null;     // { waiting, fillAt, timeoutAt }, times on performance.now()
let startsAt = null;
let endsAt = null;
let selection = [];   // [[row, col], …] in click order
let busy = false;
let builtFor = null;  // the match whose grid is on screen: a reconnect repaints instead of rebuilding
let warned = false;
let vote = null;      // { matchId, candidates, ranking: [id], sent, endsAt, progress }

observeGrid(grid);

// Times arrive relative (…_in_ms) and are pinned to this page's clock, so the device's clock setting
// doesn't matter (§6.4).
const at = (ms) => (ms === null || ms === undefined ? null : performance.now() + ms);
const msLeft = (t) => (t === null ? 0 : Math.max(0, t - performance.now()));
const secondsLeft = (t) => Math.ceil(msLeft(t) / 1000);
const clockText = (t) => {
  const s = secondsLeft(t);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

const regions = { status: $("sr-status"), alert: $("sr-alert") };
function announce(kind, text) {
  regions[kind].textContent = "";
  requestAnimationFrame(() => { regions[kind].textContent = text; });
}

function markMode() {
  for (const button of document.querySelectorAll(".modes button")) {
    if (button.dataset.mode === "match") button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}

function show(name) {
  if ($(`screen-${name}`).hidden) showScreen(name);
  markMode();
}

const errorText = (err) => (err instanceof ApiError ? err.message : "Something went wrong.");

// --- entry points (main.js) ---

// The Multi button: join the queue, or come back to the match this player is already in.
export async function openMatch() {
  markMode();
  try {
    applyView(await api.matchQueue());
  } catch (err) {
    if (!(err instanceof ApiError && err.code === "in_match")) return showJoinError(err);
    try {
      applyView(await api.matchCurrent());
    } catch (again) {
      return showJoinError(again);
    }
  }
  if (ACTIVE.has(phase)) connect();
}

function showJoinError(err) {
  if (err instanceof ApiError && err.code === "server_busy") return showMessage("Server busy", "Try again later.");
  if (err instanceof ApiError && err.code === "rate_limited") {
    return showMessage("Too many tries", "You joined the queue a lot this hour. Try again later.");
  }
  return showMessage("Couldn’t join", errorText(err));
}

// At boot: a results link (#match/<id>), or a match or queue this player is still in after a reload.
export async function resumeMatch() {
  const link = RESULTS_LINK.exec(location.hash);
  if (link) {
    await openResults(link[1]);
    return true;
  }
  let view;
  try {
    view = await api.matchCurrent();
  } catch {
    return false;
  }
  if (view.state === "idle") return false;
  applyView(view);
  connect();
  return true;
}

// Free or Daily pressed: leave the queue. Inside a match nothing changes on the server: the player can
// come back with Multi until it ends (§4).
export async function leaveMatch() {
  const queued = phase === "queued";
  closeStream();
  phase = "idle";
  forgetLink();
  if (queued) {
    try {
      await api.matchLeave();
    } catch {
      // the queue drops a player without a stream after 10 s anyway
    }
  }
}

async function openResults(id) {
  markMode();
  try {
    showResults(await api.matchResults(id));
  } catch {
    forgetLink();
    showMessage("Results not found", "Only the players of a match can open its results.");
  }
}

function forgetLink() {
  if (RESULTS_LINK.test(location.hash)) history.replaceState(null, "", location.pathname + location.search);
}

// --- the stream ---

function connect() {
  closeStream();
  stream = openStream({ onEvent, onLost: resync });
}

function closeStream() {
  if (stream) stream.close();
  stream = null;
}

// The stream ended without a terminal event: a restart, a deploy, or a phone that slept. Ask the server
// where this player stands (§6.5).
async function resync() {
  stream = null;
  if (!ACTIVE.has(phase)) return;
  let view;
  try {
    view = await api.matchCurrent();
  } catch {
    return showMessage("Connection lost", "Can’t reach the server. Press Multi to try again.");
  }
  if (view.state !== "idle") {
    applyView(view);
    return connect();
  }
  if (phase === "queued") return showMessage("Out of the queue", "You were taken out of the queue. Press Multi to join again.");
  if (match && (phase === "running" || phase === "voting")) {
    try {
      return showResults(await api.matchResults(match.id)); // it ended while the stream was away
    } catch {
      // not saved: the server restarted and the match is gone
    }
  }
  return showMessage("Match cancelled", "The server restarted, so this match was called off.");
}

// Phones stop the stream when the screen locks; rebuild from a fresh snapshot when the page is back.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && ACTIVE.has(phase) && !(stream && stream.isOpen())) resync();
});

function onEvent(name, data) {
  switch (name) {
    case "snapshot":
      return applyView(data);
    case "queue":
      queue = { ...queue, waiting: data.waiting, fillAt: at(data.fill_ends_in_ms) };
      return renderLobby();
    case "queue_timeout":
      return showMessage("No other players right now", "Nobody else joined within 2 minutes. Try again later, or play solo.");
    case "server_busy":
      return showMessage("Server busy", "Try again later.");
    case "match_found":
      match = matchFor(data.match_id, data.players);
      phase = "countdown";
      startsAt = at(data.starts_in_ms);
      announce("status", `Match found: ${data.players.join(", ")}.`);
      return renderLobby();
    case "match_cancelled":
      return showMessage("Match cancelled", "No board could be picked for this match. Try again.");
    case "start":
      return startPlay(data.word1, data.word2, null, data.ends_in_ms);
    case "tiles":
      return applyChanges(data.changes);
    case "found":
      return popup("other", `${data.nickname} found a ${data.letters}-letter word`);
    case "showcase":
      return popup("other", `${data.nickname} set a showcase word`);
    case "warning":
      endsAt = at(data.ends_in_ms);
      return warn();
    case "voting":
      return showVote(data.candidates, data.ends_in_ms, false);
    case "voted":
      if (vote) vote.progress = data;
      return renderVote();
    case "end":
      return showResults(data);
    case "replaced":
      return showMessage("Multiplayer is open in another tab", "Play on there, or reload this page to play here.");
    default:
      return undefined;
  }
}

// A MatchView (§6.4), from POST /queue, GET /current or the stream's snapshot.
function applyView(view) {
  const m = view.match;
  switch (view.state) {
    case "queued":
      phase = "queued";
      queue = { waiting: view.queue.waiting, fillAt: at(view.queue.fill_ends_in_ms), timeoutAt: at(view.queue.timeout_in_ms) };
      return renderLobby();
    case "forming":
      phase = "forming";
      match = matchFor(m.match_id, m.players);
      return renderLobby();
    case "countdown":
      phase = "countdown";
      match = matchFor(m.match_id, m.players);
      startsAt = at(m.starts_in_ms);
      return renderLobby();
    case "running":
    case "voting":
      match = matchFor(m.match_id, m.players);
      match.locked = new Set(m.locked.map(([row, col]) => `${row},${col}`));
      match.found = m.found;
      match.showcase = m.showcase;
      if (view.state === "running") return startPlay(m.word1, m.word2, m.pct, m.ends_in_ms);
      match.word1 = m.word1;
      match.word2 = m.word2;
      return showVote(m.vote.candidates, m.vote.ends_in_ms, m.vote.voted);
    default:
      return showMessage("Not in a match", "Press Multi to join the queue.");
  }
}

function matchFor(id, players) {
  if (match && match.id === id) return { ...match, players };
  return { id, players, word1: null, word2: null, pct: null, locked: new Set(), found: [], showcase: null };
}

// --- queue, match found and 3-2-1 ---

function renderLobby() {
  const title = $("lobby-title");
  const text = $("lobby-text");
  const count = $("lobby-count");
  const players = $("lobby-players");
  const cancel = $("lobby-cancel");
  $("lobby-again").hidden = true;
  if (phase === "queued") {
    setText(title, "Looking for players…");
    count.hidden = true;
    players.hidden = true;
    const fill = queue.fillAt === null ? null : secondsLeft(queue.fillAt);
    setText(text, fill !== null
      ? `${queue.waiting} players waiting. The match starts in ${fill} s.`
      : "Waiting for a second player. The match starts as soon as someone joins.");
    cancel.hidden = false;
    setText(cancel, "Cancel");
  } else {
    setText(title, "Match found");
    const names = match.players.join("\n");
    if (players.dataset.names !== names) {
      players.dataset.names = names;
      players.replaceChildren(...match.players.map((name) => {
        const li = document.createElement("li");
        li.textContent = name;
        return li;
      }));
    }
    players.hidden = false;
    cancel.hidden = true; // the players are fixed now (§4)
    if (phase === "countdown") {
      const s = secondsLeft(startsAt);
      count.hidden = false;
      setText(count, s > 0 ? String(s) : "GO");
      setText(text, "The grid appears at GO. 60 seconds, then a vote on showcase words.");
    } else {
      count.hidden = true;
      setText(text, "Picking a board…");
    }
  }
  show("lobby");
}

function showMessage(title, text) {
  closeStream();
  phase = "message";
  setText($("lobby-title"), title);
  setText($("lobby-text"), text);
  $("lobby-count").hidden = true;
  $("lobby-players").hidden = true;
  $("lobby-cancel").hidden = false;
  setText($("lobby-cancel"), "Play solo");
  $("lobby-again").hidden = false;
  show("lobby");
  announce("status", `${title}. ${text}`);
}

$("lobby-cancel").addEventListener("click", async () => {
  await leaveMatch();
  try {
    await startGame("free");
  } catch (err) {
    showMessage("Couldn’t start a game", errorText(err));
  }
});
$("lobby-again").addEventListener("click", () => openMatch());

// --- the match ---

const pairOf = ([row, col]) => match.word1[col] + match.word2[row];
const key = (row, col) => `${row},${col}`;
const selectedIndex = (row, col) => selection.findIndex(([r, c]) => r === row && c === col);

function startPlay(word1, word2, pct, endsInMs) {
  phase = "running";
  match.word1 = word1;
  match.word2 = word2;
  match.pct = pct || [...word2].map(() => [...word1].map(() => 100));
  endsAt = at(endsInMs);
  warned = false;
  selection = [];
  busy = false;
  if (builtFor !== match.id) {
    buildGrid(grid, word1, word2);
    builtFor = match.id;
    $("m-popups").replaceChildren();
    delete $("m-timer").dataset.warning;
  }
  render();
  show("match");
  fitGrid(grid); // built while hidden; on phones it can only be measured now
  renderTimer();
  if (!pct) announce("status", `Go! ${word1} across, ${word2} down. 60 seconds.`);
}

function paintTile(tile) {
  const row = Number(tile.dataset.row);
  const col = Number(tile.dataset.col);
  const pct = match.pct[row][col];
  const locked = match.locked.has(key(row, col));
  const on = selectedIndex(row, col) >= 0;
  tile.dataset.pct = String(pct);
  tile.style.setProperty("--pct", String(pct));
  tile.dataset.faded = String(pct <= 50);
  if (pct < 100) tile.dataset.label = `${pct}%`;
  else delete tile.dataset.label;
  tile.dataset.locked = String(locked);
  if (locked) tile.setAttribute("aria-disabled", "true");
  else tile.removeAttribute("aria-disabled");
  if (tile.dataset.state !== "rejected") tile.dataset.state = on ? "selected" : "default";
  tile.setAttribute("aria-pressed", String(on));
  tile.setAttribute("aria-label", `${tile.textContent}, ${pct} percent${locked ? ", used by you" : ""}`);
}

function wordList(words) {
  return words.map((word) => {
    const li = document.createElement("li");
    li.textContent = word;
    return li;
  });
}

function render() {
  if (!match || !match.word1) return;
  for (const tile of grid.querySelectorAll(".tile")) paintTile(tile);
  const word = selection.map(pairOf).join("");
  const preview = $("m-preview");
  preview.dataset.empty = String(!word);
  const touch = window.matchMedia("(hover: none) and (pointer: coarse)").matches;
  // Phones give the preview a row of its own; on desktop it shares the bar with three buttons.
  setText(preview, word ? `→ ${word}` : touch ? "Tap tiles to build a word" : "Click tiles");

  const open = phase === "running";
  byTestId("m-submit").disabled = busy || !open || selection.length < MIN_TILES;
  byTestId("m-showcase-button").disabled = busy || !open || selection.length < MIN_TILES;
  byTestId("m-clear").disabled = busy || selection.length === 0;

  $("m-found-list").replaceChildren(...wordList(match.found));
  byTestId("m-showcase-box").hidden = !match.showcase;
  setText($("m-showcase"), match.showcase || "");
  const strip = match.found.length ? `Your words: ${match.found.join(" · ")}` : "Your words appear here";
  setText($("m-words"), match.showcase ? `${strip} · Showcase: ${match.showcase}` : strip);
}

function renderTimer() {
  const left = msLeft(endsAt);
  setText($("m-timer"), clockText(endsAt));
  if (left <= WARNING_MS && !warned) warn();
  if (left === 0 && phase === "running") setText($("m-preview"), "Time’s up. Counting the words…");
}

function warn() {
  if (warned || phase !== "running") return;
  warned = true;
  $("m-timer").dataset.warning = "true";
  popup("warning", "10 seconds left");
}

function applyChanges(changes) {
  if (!match || !match.pct) return;
  for (const { row, col, pct } of changes) {
    match.pct[row][col] = pct;
    const tile = grid.querySelector(`.tile[data-row="${row}"][data-col="${col}"]`);
    if (tile) paintTile(tile);
  }
}

function lock(tiles) {
  for (const [row, col] of tiles) match.locked.add(key(row, col));
}

grid.addEventListener("click", (event) => {
  const tile = event.target.closest(".tile");
  if (!tile || phase !== "running" || busy) return;
  const row = Number(tile.dataset.row);
  const col = Number(tile.dataset.col);
  if (match.locked.has(key(row, col))) return popup("note", `You already used ${tile.textContent}`, "Each tile once per player; the others still can.");
  const i = selectedIndex(row, col);
  selection = i >= 0 ? selection.filter((_, j) => j !== i) : [...selection, [row, col]];
  return render();
});

byTestId("m-clear").addEventListener("click", () => {
  selection = [];
  render();
});
byTestId("m-submit").addEventListener("click", () => send("word"));
byTestId("m-showcase-button").addEventListener("click", () => send("showcase"));

async function send(kind) {
  if (busy || phase !== "running" || selection.length < MIN_TILES) return;
  const tiles = selection;
  busy = true;
  render();
  try {
    if (kind === "showcase") {
      const result = await api.matchShowcase(match.id, tiles);
      lock(tiles);
      match.showcase = result.word;
      popup("note", `Showcase word: ${result.word}`, "No points now. After play the others vote on it.");
    } else {
      const result = await api.matchSubmit(match.id, tiles);
      if (result.status === "accepted") {
        lock(tiles);
        match.found = [...match.found, result.word];
        applyChanges(result.changes);
        announce("status", `${result.word} accepted.`);
      } else if (result.status === "duplicate") {
        popup("note", `${result.word} is already in your list`, "No points this time.");
      } else {
        popup("rejected", `${result.word} isn’t in the dictionary`, "No penalty. Try another word.");
        await shake(tiles);
      }
    }
  } catch (err) {
    const code = err instanceof ApiError ? err.code : "";
    if (code === "rate_limited") popup("note", "Not so fast", "Wait half a second between words.");
    else if (code === "tile_locked") popup("note", "You already used one of these tiles");
    else if (code === "match_not_running") popup("note", "Play is over", "That word came too late.");
    else popup("rejected", "Couldn’t send", errorText(err));
  } finally {
    selection = [];
    busy = false;
    render();
  }
}

function shake(tiles) {
  for (const [row, col] of tiles) {
    grid.querySelector(`.tile[data-row="${row}"][data-col="${col}"]`).dataset.state = "rejected";
  }
  return new Promise((resolve) => setTimeout(() => {
    for (const tile of grid.querySelectorAll('.tile[data-state="rejected"]')) tile.dataset.state = "default";
    resolve();
  }, REJECT_MS));
}

// The popup stack: others' finds, notes and rejects. Docked beside the grid on desktop and over the
// action area on phones, never over the grid (§8).
function popup(kind, title, body = "") {
  const list = $("m-popups");
  const item = document.createElement("li");
  item.className = "popup";
  item.dataset.kind = kind;
  const head = document.createElement("strong");
  head.textContent = title;
  item.append(head);
  if (body) {
    const text = document.createElement("span");
    text.textContent = body;
    item.append(text);
  }
  list.append(item);
  while (list.childElementCount > MAX_POPUPS) list.firstElementChild.remove();
  announce(kind === "rejected" ? "alert" : "status", body ? `${title}. ${body}` : title);
  setTimeout(() => item.remove(), POPUP_MS);
}

// --- the creativity vote (§3.7) ---

function showVote(candidates, endsInMs, voted) {
  phase = "voting";
  if (!vote || vote.matchId !== match.id) {
    vote = { matchId: match.id, candidates, ranking: [], sent: voted, endsAt: null, progress: null };
  } else {
    vote.candidates = candidates;
    vote.sent = vote.sent || voted;
  }
  vote.endsAt = at(endsInMs);
  delete $("m-timer").dataset.warning; // the timer now counts the vote
  renderVote();
  show("vote");
  renderVoteTimer();
}

const slotsFor = () => Math.min(3, vote.candidates.length);

function renderVote() {
  if (!vote || phase !== "voting") return;
  const slots = slotsFor();
  setText($("vote-sub"), vote.sent
    ? "Your vote is in."
    : `Rank up to ${slots} of the others’ showcase words. Tap them in order: 1st gets 5 points, 2nd 3, 3rd 1. Names stay hidden until the results.`);
  $("vote-cands").replaceChildren(...vote.candidates.map((c) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cand";
    button.dataset.candidate = c.id;
    const rank = vote.ranking.indexOf(c.id);
    button.setAttribute("aria-pressed", String(rank >= 0));
    button.disabled = vote.sent;
    const word = document.createElement("span");
    word.textContent = c.word;
    button.append(word);
    if (rank >= 0) {
      const badge = document.createElement("span");
      badge.className = "cand-rank";
      badge.textContent = RANK_NAMES[rank];
      button.append(badge);
    }
    li.append(button);
    return li;
  }));
  $("vote-slots").replaceChildren(...RANK_POINTS.slice(0, slots).map((points, i) => {
    const li = document.createElement("li");
    li.className = "slot";
    const id = vote.ranking[i];
    li.dataset.filled = String(Boolean(id));
    const label = document.createElement("span");
    label.className = "slot-label";
    label.textContent = `${RANK_NAMES[i]} · ${points} points`;
    const word = document.createElement("strong");
    word.textContent = id ? vote.candidates.find((c) => c.id === id).word : "–";
    li.append(label, word);
    return li;
  }));
  byTestId("vote-send").disabled = busy || vote.sent || vote.ranking.length === 0;
  byTestId("vote-reset").disabled = busy || vote.sent || vote.ranking.length === 0;
  const progress = vote.progress ? ` (${vote.progress.voted} of ${vote.progress.of} voted)` : "";
  setText($("vote-status"), vote.error
    || (vote.sent ? `Waiting for the others${progress}…` : progress ? `Voted so far${progress}` : ""));
}

function renderVoteTimer() {
  if (vote && phase === "voting") setText($("m-timer"), clockText(vote.endsAt));
}

$("vote-cands").addEventListener("click", (event) => {
  const button = event.target.closest(".cand");
  if (!button || !vote || vote.sent || busy) return;
  const id = button.dataset.candidate;
  if (vote.ranking.includes(id)) vote.ranking = vote.ranking.filter((r) => r !== id);
  else if (vote.ranking.length < slotsFor()) vote.ranking = [...vote.ranking, id];
  renderVote();
});

byTestId("vote-reset").addEventListener("click", () => {
  if (!vote || vote.sent) return;
  vote.ranking = [];
  renderVote();
});

byTestId("vote-send").addEventListener("click", async () => {
  if (!vote || vote.sent || busy || vote.ranking.length === 0) return;
  busy = true;
  vote.error = "";
  renderVote();
  try {
    await api.matchVote(match.id, vote.ranking);
    vote.sent = true;
    announce("status", "Vote sent.");
  } catch (err) {
    if (err instanceof ApiError && err.code === "already_voted") vote.sent = true;
    else {
      vote.error = errorText(err);
      announce("alert", vote.error);
    }
  } finally {
    busy = false;
    renderVote();
  }
});

// --- results (§3.1 step 8): also opened from the #match/<id> link ---

function showResults(res) {
  closeStream();
  phase = "results";
  history.replaceState(null, "", `#match/${res.match_id}`);
  const total = `${res.total_words} ${res.total_words === 1 ? "word" : "words"}`;
  setText($("res-sub"), `${res.word1} × ${res.word2} · ${total} on the board`);
  $("res-players").replaceChildren(...res.players.map(playerItem));
  const missed = res.missed_words || [];
  setText($("res-missed-head"), `Words nobody found · ${missed.length}`);
  $("res-missed").replaceChildren(...wordList(missed));
  $("res-missed").closest("section").hidden = missed.length === 0;
  show("results");
  const winner = res.players.find((p) => p.place === 1);
  if (winner) announce("status", `Results. ${winner.nickname} wins with ${winner.score} points.`);
}

function playerItem(p) {
  const li = document.createElement("li");
  li.className = "res-player";
  li.dataset.place = String(p.place);
  const row = document.createElement("div");
  row.className = "res-row";
  for (const [cls, text] of [["res-place", String(p.place)], ["res-name", p.nickname], ["res-score", String(p.score)]]) {
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    row.append(span);
  }
  const bonus = p.showcase ? p.showcase.bonus : 0;
  const detail = document.createElement("p");
  detail.className = "res-detail";
  detail.textContent = `${p.word_score} from words · +${bonus} vote bonus`;
  const showcase = document.createElement("p");
  showcase.className = "res-detail";
  showcase.dataset.testid = "res-showcase";
  showcase.textContent = p.showcase
    ? `Showcase ${p.showcase.word} · ${p.showcase.votes} vote points → +${p.showcase.bonus}`
    : "No showcase word";
  const words = document.createElement("ul");
  words.className = "res-words";
  words.append(...p.words.map((w) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    const pts = document.createElement("span");
    name.textContent = w.word;
    pts.className = "pts";
    pts.textContent = String(w.points);
    item.append(name, pts);
    return item;
  }));
  li.append(row, detail, showcase, words);
  return li;
}

byTestId("res-again").addEventListener("click", () => {
  forgetLink();
  openMatch();
});
byTestId("res-solo").addEventListener("click", async () => {
  await leaveMatch();
  try {
    await startGame("free");
  } catch (err) {
    showMessage("Couldn’t start a game", errorText(err));
  }
});

// --- one clock for the lobby, the match and the vote ---

setInterval(() => {
  if (phase === "queued" || phase === "countdown") renderLobby();
  else if (phase === "running") renderTimer();
  else if (phase === "voting") renderVoteTimer();
}, 250);
