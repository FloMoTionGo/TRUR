// Gameplay log: batches tile events, at most 50 per request and at least 1 s apart (docs/architecture.md §3).
// Best effort: a lost batch never gets in the way of play.

import { api } from "./api.js";

const MAX_BATCH = 50;
const MAX_QUEUE = 500; // if the server keeps refusing, drop the oldest rather than grow without end
const GAP_MS = 1100; // a little over the server's 1-per-second limit

let queue = [];
let timer = null;

export function logEvent(event) {
  queue.push({ ...event, client_ms: Math.round(performance.now()) });
  if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE);
  schedule();
}

function schedule() {
  if (timer !== null || queue.length === 0) return;
  timer = setTimeout(flush, GAP_MS); // collects the clicks of the next second, and keeps batches apart
}

async function flush() {
  const batch = queue.splice(0, MAX_BATCH);
  try {
    await api.events(batch);
  } catch {
    // dropped: the log is for analysis, not for scoring
  }
  timer = null;
  schedule();
}
