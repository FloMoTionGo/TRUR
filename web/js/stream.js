// The match's live stream: an EventSource with its terminal events handled (docs/multiplayer.md §6.5).

const EVENTS = [
  "snapshot", "queue", "queue_timeout", "server_busy", "match_found", "match_cancelled", "start", "tiles",
  "found", "showcase", "warning", "voting", "voted", "end", "replaced",
];
// The server closes the stream after these, so the browser must not reconnect to it.
const TERMINAL = new Set(["end", "queue_timeout", "server_busy", "match_cancelled", "replaced"]);

// onEvent(name, data) for every event; onLost() when the stream ended without a terminal event
// (a restart, a deploy, a 204 on reconnect), which is when the caller asks /api/match/current what happened.
export function openStream({ onEvent, onLost }) {
  const source = new EventSource("/api/match/stream");
  let finished = false;
  const close = () => {
    finished = true;
    source.close();
  };
  for (const name of EVENTS) {
    source.addEventListener(name, (event) => {
      let data = {};
      try {
        data = JSON.parse(event.data);
      } catch {
        // a malformed event carries nothing we could use
      }
      if (TERMINAL.has(name)) close();
      onEvent(name, data);
    });
  }
  // A dropped connection reconnects by itself (CONNECTING) and starts again with a snapshot. Only a CLOSED
  // stream is final: the server answered 204 (idle) or an error status.
  source.onerror = () => {
    if (finished || source.readyState !== EventSource.CLOSED) return;
    finished = true;
    onLost();
  };
  return { close, isOpen: () => !finished };
}
