"""Local multiplayer sandbox: one shared board, hot-seat players, no clock (dev only).

    python -m scripts.match_sandbox [--players 4] [--words GARDEN,SUMMER] [--port 8765]

Then open http://127.0.0.1:8765/ and switch players with the tabs, or open one window per
player with ?p=1 … ?p=4. Every action runs through trur/match.py, the real rules engine; time
is frozen inside the play window, so nothing ends until "End play" is pressed. Stdlib only, and
it binds to 127.0.0.1.
"""

import argparse
import json
import random
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trur import match
from trur.solver import Lexicon, load_lexicon, pick_pair

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).with_name("match_sandbox.html")
NAMES = ("Ana", "Ben", "Cleo", "Dan", "Eve", "Finn")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
PLAY_AT = T0 + timedelta(seconds=1)  # frozen: always inside the play window
CLOSE_AT = T0 + timedelta(seconds=match.DURATION_S) + match.GRACE + timedelta(milliseconds=1)
VOTE_AT = CLOSE_AT + timedelta(seconds=1)


class Sandbox:
    def __init__(self, lex: Lexicon, players: int, min_words: int, seed: int | None = None) -> None:
        if not 2 <= players <= len(NAMES):
            raise ValueError(f"2 to {len(NAMES)} players")
        self.lex = lex
        self.players = [(str(i), NAMES[i - 1], NAMES[i - 1].lower()) for i in range(1, players + 1)]
        self.min_words = min_words
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.new_board()

    def new_board(self, word1: str | None = None, word2: str | None = None) -> None:
        if word1 and word2:
            word1, word2 = word1.upper(), word2.upper()
            if not (word1.isascii() and word1.isalpha() and word2.isascii() and word2.isalpha()):
                raise ValueError("words are letters A–Z only")
            answers = self.lex.solve(word1, word2)
        else:
            word1, word2, answers = pick_pair(self.lex, self.rng, lambda a, b, ans: len(ans) >= self.min_words)
        self.match = match.new_match("sandbox", word1, word2, answers, self.players, T0, self.lex.max_tiles)
        self.phase = "play"
        self.ballots: dict[str, list[match.Candidate]] = {}
        self.result: dict | None = None

    def _uid(self, uid: object) -> str:
        if uid not in self.match.players:
            raise KeyError("unknown player")
        return str(uid)

    def state(self, uid: str) -> dict:
        uid = self._uid(uid)
        m, me = self.match, self.match.players[uid]
        feed = [
            {"n": i, "nickname": m.players[e.user_id].nickname, "letters": len(e.word)}
            for i, e in enumerate(m.log)
            if e.user_id != uid and e.status in ("accepted", "displayed")
        ]
        voted = sum(p.ballot is not None for p in m.players.values())
        return {
            "phase": self.phase,
            "me": uid,
            "players": [{"id": u, "nickname": p.nickname} for u, p in m.players.items()],
            "word1": m.word1,
            "word2": m.word2,
            "pct": match.pct_grid(m),
            "locked": sorted(me.locked),
            "found": list(me.found),
            "display": me.display.word if me.display else None,
            "feed": feed,
            "vote": {
                "candidates": [c._asdict() for c in self.ballots[uid]],
                "voted": me.ballot is not None,
                "count": voted,
                "of": len(m.players),
            } if self.phase == "vote" else None,
            "results": self.result,
        }

    def submit(self, uid: str, tiles: list) -> dict:
        out = match.submit(self.match, self._uid(uid), [tuple(t) for t in tiles], PLAY_AT)
        return {"status": out.status, "word": out.word}

    def display(self, uid: str, tiles: list) -> dict:
        out = match.display(self.match, self._uid(uid), [tuple(t) for t in tiles], PLAY_AT)
        return {"status": "displayed", "word": out.word}

    def end_play(self) -> None:
        if self.phase != "play":
            raise match.NotRunning()
        if match.close_play(self.match, CLOSE_AT, self.rng):
            self.phase = "vote"
            self.ballots = {uid: match.ballot_for(self.match, uid, self.rng) for uid in self.match.players}
        else:
            self._finish()

    def vote(self, uid: str, ranking: list) -> None:
        match.vote(self.match, self._uid(uid), [str(c) for c in ranking], VOTE_AT)
        if all(p.ballot is not None for p in self.match.players.values()):
            self._finish()

    def end_vote(self) -> None:
        if self.phase != "vote":
            raise match.NotVoting()
        self._finish()

    def _finish(self) -> None:
        self.result = match.results(match.finish(self.match, VOTE_AT))
        for row in self.result["players"]:
            del row["user_id"]
        self.phase = "results"


ERRORS = {
    match.NotRunning: (409, "match_not_running"),
    match.TileLocked: (409, "tile_locked"),
    match.NotVoting: (409, "match_not_voting"),
    match.AlreadyVoted: (409, "already_voted"),
    ValueError: (422, "invalid_input"),
    KeyError: (404, "not_found"),
    TypeError: (422, "invalid_input"),
}


def make_handler(box: Sandbox) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # quiet
            pass

        def _send(self, status: int, body: bytes = b"", ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: object) -> None:
            self._send(status, json.dumps(data).encode())

        def do_GET(self) -> None:
            path, _, query = self.path.partition("?")
            if path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                params = dict(p.partition("=")[::2] for p in query.split("&") if p)
                return self._call(lambda: box.state(params.get("p", "")))
            self._send(404)

        def do_POST(self) -> None:
            if self.headers.get("Content-Type") != "application/json":
                return self._send(415)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            actions = {
                "/api/submit": lambda: box.submit(body["p"], body["tiles"]),
                "/api/display": lambda: box.display(body["p"], body["tiles"]),
                "/api/end": box.end_play,
                "/api/vote": lambda: box.vote(body["p"], body["ranking"]),
                "/api/end-vote": box.end_vote,
                "/api/new": lambda: box.new_board(body.get("word1"), body.get("word2")),
            }
            if self.path not in actions:
                return self._send(404)
            self._call(actions[self.path])

        def _call(self, action) -> None:
            try:
                with box.lock:
                    data = action()
            except tuple(ERRORS) as e:
                status, code = next(v for k, v in ERRORS.items() if isinstance(e, k))
                return self._json(status, {"error": code, "detail": str(e)})
            self._json(200, data or {})

    return Handler


def serve(box: Sandbox, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(box))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--words", help="WORD1,WORD2 (default: a random pair)")
    ap.add_argument("--min-words", type=int, default=20)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    args = ap.parse_args(argv)
    box = Sandbox(load_lexicon(args.data), args.players, args.min_words, args.seed)
    if args.words:
        box.new_board(*args.words.split(",", 1))
    server = serve(box, args.port)
    print(f"Match sandbox: http://127.0.0.1:{server.server_port}/  ({box.match.word1} × {box.match.word2}; Ctrl+C stops)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
