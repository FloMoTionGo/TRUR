"""Step M5: the match routes and the live stream (docs/test-strategy.md §6, docs/multiplayer.md §6.4–§6.5).

In-process tests (TestClient) run with the ticker off: they move the FakeClock and tick the lobby
themselves, on the app's own event loop. The stream, concurrency and shutdown tests run on
live_match_server with the real ticker; they wait for stream events with a 2-s timeout, never for
a fixed time.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FakeClock, LiveServer
from tests.test_games import live_player, rows
from tests.test_lobby import END, GARDEN, drain, drive
from tests.test_match import make_match, tiles
from tests.test_security import assert_envelope
from trur import db, match
from trur.lobby import Lobby, PickJob, SaveJob

M = "/api/match"
NO_MATCH = "7b0c5a0e-3d2f-4c1e-9a77-1f6e2b9d8c40"
FILL_S = 10  # MATCH_FILL_S in the test settings
Player = Callable[[str], TestClient]


def coords(word: str) -> list[list[int]]:
    """The tiles of `word` on GARDEN × SUMMER, as the browser sends them."""
    return [list(t) for t in tiles(make_match(), word)]


def queue(c: TestClient) -> dict:
    r = c.post(f"{M}/queue", json={})
    assert r.status_code == 200, r.text
    return r.json()


class Host:
    """The app under test with its ticker off (MATCH_TICK_MS = 0): the test moves the clock and ticks
    the lobby on the app's own event loop, with every board pick resolved at once (test_lobby.drive)."""

    def __init__(self, app: FastAPI, client: TestClient, clock: FakeClock) -> None:
        self.app, self.client, self.clock = app, client, clock
        self.lobby: Lobby = app.state.lobby

    def call(self, fn: Callable, *args: object):
        return self.client.portal.call(fn, *args)

    def tick(self) -> list[PickJob | SaveJob]:
        return self.call(drive, self.lobby, self.clock, GARDEN)

    def start(self, *players: TestClient, go: bool = True) -> str:
        """Queue these players and end the fill window; with go, tick on to GO. The match id."""
        for c in players:
            queue(c)
        self.clock.advance(FILL_S)
        [job] = self.tick()
        if go:
            self.go()
        return job.match_id

    def go(self) -> None:
        self.clock.advance(match.COUNTDOWN_S)
        self.tick()

    def configure(self, **changes: object) -> None:
        """New settings for the routes and the lobby alike."""
        self.app.state.settings = replace(self.app.state.settings, **changes)
        self.lobby.s = self.app.state.settings


@pytest.fixture
def host(app: FastAPI, client: TestClient, clock: FakeClock) -> Host:
    return Host(app, client, clock)  # `client` has run the lifespan, which made the lobby


def user_ids(db_path: Path) -> dict[str, str]:
    return dict(rows(db_path, "SELECT nickname, id FROM users"))


# --- who may call what (§7 M1) ---

ROUTES = [
    ("POST", f"{M}/queue", {}),
    ("POST", f"{M}/queue/leave", {}),
    ("GET", f"{M}/current", None),
    ("GET", f"{M}/stream", None),
    ("POST", f"{M}/{NO_MATCH}/submit", {"tiles": coords("GEAR")}),
    ("POST", f"{M}/{NO_MATCH}/display", {"tiles": coords("GEAR")}),
    ("POST", f"{M}/{NO_MATCH}/vote", {"ranking": ["c1"]}),
    ("GET", f"{M}/{NO_MATCH}/results", None),
]


def test_every_match_route_needs_a_player(client: TestClient) -> None:
    for method, path, body in ROUTES:
        assert_envelope(client.request(method, path, json=body), 401, "no_player")


def in_match(c: TestClient, match_id: str) -> list[tuple[int, dict]]:
    """Every route that names a match, called by c."""
    return [(r.status_code, r.json()) for r in (
        c.post(f"{M}/{match_id}/submit", json={"tiles": coords("GEAR")}),
        c.post(f"{M}/{match_id}/display", json={"tiles": coords("GEAR")}),
        c.post(f"{M}/{match_id}/vote", json={"ranking": ["c1"]}),
        c.get(f"{M}/{match_id}/results"),
    )]


def test_someone_elses_match_looks_like_a_missing_one(host: Host, player: Player) -> None:
    alice, bob, cy = player("Alice"), player("Bob"), player("Cyd")
    match_id = host.start(alice, bob)
    answers = in_match(cy, match_id)
    assert answers == in_match(cy, NO_MATCH)
    assert {(status, body["error"]["code"]) for status, body in answers} == {(404, "not_found")}
    assert cy.get(f"{M}/stream").status_code == 204  # the stream names no match: Cy is simply idle
    assert host.lobby.rooms[match_id].m.uses == {}


@pytest.mark.parametrize("variant", [str.upper, lambda m: "{" + m + "}", lambda m: "urn:uuid:" + m,
                                     lambda m: m.replace("-", ""), lambda m: "nope"])
def test_a_match_id_must_be_a_canonical_uuid(host: Host, player: Player, variant: Callable[[str], str]) -> None:
    alice, bob = player("Alice"), player("Bob")
    match_id = host.start(alice, bob)
    assert {(s, b["error"]["code"]) for s, b in in_match(alice, variant(match_id))} == {(404, "not_found")}
    assert host.lobby.rooms[match_id].m.uses == {}


# --- the contract (§6.4) ---

def test_queue_current_leave_and_the_idle_stream(host: Host, player: Player) -> None:
    alice = player("Alice")
    idle = {"state": "idle", "queue": None, "match": None}
    assert alice.get(f"{M}/current").json() == idle
    assert alice.get(f"{M}/stream").status_code == 204  # EventSource won't reconnect
    view = queue(alice)
    assert view == {"state": "queued", "queue": {"waiting": 1, "fill_ends_in_ms": None, "timeout_in_ms": 120_000}, "match": None}
    assert queue(alice) == view  # already queued: the same view
    assert alice.get(f"{M}/current").json() == view
    for _ in range(2):  # leaving is idempotent
        r = alice.post(f"{M}/queue/leave", json={})
        assert (r.status_code, r.content) == (204, b"")
    assert alice.get(f"{M}/current").json() == idle


def test_joining_from_a_match_or_into_a_full_queue(host: Host, player: Player) -> None:
    alice, bob, cy, dee = player("Alice"), player("Bob"), player("Cyd"), player("Dee")
    host.start(alice, bob)
    assert_envelope(alice.post(f"{M}/queue", json={}), 409, "in_match")
    host.configure(match_max_queue=1)
    queue(cy)
    assert_envelope(dee.post(f"{M}/queue", json={}), 503, "server_busy")
    assert dee.get(f"{M}/current").json()["state"] == "idle"


def test_submit_and_display_answer_without_points(host: Host, player: Player) -> None:
    alice, bob = player("Alice"), player("Bob")
    match_id = host.start(alice, bob, go=False)
    submit, display = f"{M}/{match_id}/submit", f"{M}/{match_id}/display"
    assert_envelope(alice.post(submit, json={"tiles": coords("GEAR")}), 409, "match_not_running")  # 3-2-1
    host.go()
    accepted = alice.post(submit, json={"tiles": coords("GEAR")})
    assert accepted.status_code == 200
    assert accepted.json() == {"status": "accepted", "word": "GEAR",
                               "changes": [{"row": 4, "col": 0, "pct": 50}, {"row": 5, "col": 1, "pct": 50}]}
    assert alice.post(submit, json={"tiles": coords("GEAR")}).json() == {"status": "duplicate", "word": "GEAR", "changes": []}
    assert alice.post(submit, json={"tiles": coords("GSAS")}).json() == {"status": "rejected", "word": "GSAS", "changes": []}
    assert_envelope(alice.post(submit, json={"tiles": coords("NEAR")}), 409, "tile_locked")  # AR is Alice's
    assert_envelope(alice.post(submit, json={"tiles": [[9, 9], [0, 0]]}), 422, "invalid_tiles")  # off the 6×6 grid
    assert_envelope(alice.post(submit, json={"tiles": coords("DEER"), "score": 99}), 422, "invalid_request")
    assert bob.post(display, json={"tiles": coords("DEER")}).json() == {
        "status": "displayed", "word": "DEER", "changes": [{"row": 4, "col": 3, "pct": 50}, {"row": 5, "col": 4, "pct": 50}]}
    host.clock.advance(END)
    host.tick()  # one display word: no vote, the match is over but not yet saved
    assert_envelope(bob.post(submit, json={"tiles": coords("RUNE")}), 409, "match_not_running")
    assert_envelope(bob.post(display, json={"tiles": coords("RUNE")}), 409, "match_not_running")


def test_the_submit_gap_is_shared_with_display_words(host: Host, player: Player) -> None:
    host.configure(match_submit_min_gap_s=0.5)
    alice, bob = player("Alice"), player("Bob")
    match_id = host.start(alice, bob)
    submit, display = f"{M}/{match_id}/submit", f"{M}/{match_id}/display"
    m = host.lobby.rooms[match_id].m
    assert alice.post(submit, json={"tiles": coords("GEAR")}).json()["status"] == "accepted"
    uses = dict(m.uses)
    host.clock.advance(0.4)
    for path in (submit, display):
        r = alice.post(path, json={"tiles": coords("DEER")})
        assert_envelope(r, 429, "rate_limited")
        assert r.headers["retry-after"] == "1"
    assert m.uses == uses and list(m.players[user_ids_of(m)["Alice"]].found) == ["GEAR"]  # nothing changed
    host.clock.advance(0.1)  # 0.5 s after the submit
    assert alice.post(display, json={"tiles": coords("DEER")}).json()["status"] == "displayed"
    host.clock.advance(0.4)
    assert_envelope(alice.post(submit, json={"tiles": coords("RUNE")}), 429, "rate_limited")
    assert bob.post(submit, json={"tiles": coords("RUNE")}).json()["status"] == "accepted"  # the gap is per player


def user_ids_of(m: match.Match) -> dict[str, str]:
    return {p.nickname: uid for uid, p in m.players.items()}


def test_queue_joins_are_rate_limited(host: Host, player: Player) -> None:
    host.configure(rl_queue_joins_per_player_hour=2)
    alice = player("Alice")
    for _ in range(2):
        queue(alice)
        alice.post(f"{M}/queue/leave", json={})
    r = alice.post(f"{M}/queue", json={})
    assert_envelope(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "3600"
    assert alice.get(f"{M}/current").json()["state"] == "idle"
    host.clock.advance(3600)
    assert queue(alice)["state"] == "queued"


def candidates(c: TestClient) -> dict[str, str]:
    """The caller's ballot: word → candidate id."""
    return {cand["word"]: cand["id"] for cand in c.get(f"{M}/current").json()["match"]["vote"]["candidates"]}


def test_the_vote_and_when_results_open(host: Host, player: Player) -> None:
    alice, bob, cy = player("Alice"), player("Bob"), player("Cyd")
    match_id = host.start(alice, bob, cy)

    def vote(c: TestClient, ranking: list[str]) -> httpx.Response:
        return c.post(f"{M}/{match_id}/vote", json={"ranking": ranking})

    assert_envelope(vote(alice, ["c1"]), 409, "match_not_voting")  # still playing
    alice.post(f"{M}/{match_id}/display", json={"tiles": coords("RUNS")})
    bob.post(f"{M}/{match_id}/display", json={"tiles": coords("DEER")})
    host.clock.advance(END)
    host.tick()
    host.clock.advance(1)  # ballots count only after the close
    for c in (alice, bob, cy):
        assert_envelope(c.get(f"{M}/{match_id}/results"), 404, "not_found")  # not over during the vote
    ballots = {name: candidates(c) for name, c in (("Alice", alice), ("Bob", bob), ("Cyd", cy))}
    assert set(ballots["Alice"]) == {"DEER"} and set(ballots["Cyd"]) == {"RUNS", "DEER"}
    own = ballots["Cyd"]["RUNS"]
    for ranking in ([own], ["c9"], [], [ballots["Alice"]["DEER"]] * 2, ["c1", "c2", "c3", "c4"]):
        assert_envelope(vote(alice, ranking), 422, "invalid_ballot")
    assert vote(alice, [ballots["Alice"]["DEER"]]).status_code == 204
    assert_envelope(vote(alice, [ballots["Alice"]["DEER"]]), 409, "already_voted")
    assert vote(bob, [ballots["Bob"]["RUNS"]]).status_code == 204
    assert_envelope(cy.get(f"{M}/{match_id}/results"), 404, "not_found")
    assert vote(cy, [ballots["Cyd"]["DEER"], ballots["Cyd"]["RUNS"]]).status_code == 204  # everyone done: the end
    results = [c.get(f"{M}/{match_id}/results") for c in (alice, bob, cy)]
    assert [r.status_code for r in results] == [200] * 3 and results[0].json() == results[1].json() == results[2].json()
    assert [(p["nickname"], p["display"]) for p in results[0].json()["players"]] == [
        ("Bob", {"word": "DEER", "votes": 10, "bonus": 5}),  # 5 + 5 from two ballots
        ("Alice", {"word": "RUNS", "votes": 8, "bonus": 4}),  # 5 + 3
        ("Cyd", None),
    ]
    assert_envelope(vote(cy, [ballots["Cyd"]["DEER"]]), 409, "match_not_voting")


def test_results_come_from_the_database_once_saved(host: Host, player: Player, db_path: Path) -> None:
    alice, bob, cy = player("Alice"), player("Bob"), player("Cyd")
    match_id = host.start(alice, bob)
    alice.post(f"{M}/{match_id}/submit", json={"tiles": coords("GEAR")})
    host.clock.advance(END)
    [save] = host.tick()  # the end; the save is left to the test
    in_memory = alice.get(f"{M}/{match_id}/results").json()
    conn = db.connect(db_path)
    try:
        db.save_match(conn, save.record)
    finally:
        conn.close()
    host.call(host.lobby.saved, match_id, True, host.clock.now())
    assert match_id not in host.lobby.rooms
    assert alice.get(f"{M}/{match_id}/results").json() == bob.get(f"{M}/{match_id}/results").json() == in_memory
    assert all("user_id" not in p for p in in_memory["players"])
    assert_envelope(cy.get(f"{M}/{match_id}/results"), 404, "not_found")
    assert_envelope(alice.post(f"{M}/{match_id}/submit", json={"tiles": coords("RUNE")}), 404, "not_found")


# --- payload leaks (§7 M2) ---

def paths(value: object, at: str = "") -> set[str]:
    """Every key path in a JSON value: {"a": [{"b": 1}]} → {"a", "a[].b"}."""
    if isinstance(value, dict):
        out: set[str] = set()
        for key, v in value.items():
            path = f"{at}.{key}" if at else key
            out |= {path} | paths(v, path)
        return out
    if isinstance(value, list):
        return set().union(*(paths(v, f"{at}[]") for v in value))
    return set()


VIEW = {
    "state", "queue", "queue.waiting", "queue.fill_ends_in_ms", "queue.timeout_in_ms", "match",
    *(f"match.{k}" for k in ("match_id", "players", "starts_in_ms", "ends_in_ms", "word1", "word2", "pct",
                             "locked", "found", "display", "vote")),
    "match.vote.ends_in_ms", "match.vote.candidates", "match.vote.candidates[].id", "match.vote.candidates[].word",
    "match.vote.voted",
}
RESULTS = {
    "match_id", "word1", "word2", "total_words", "players", "missed_words", "pct",
    *(f"players[].{k}" for k in ("place", "nickname", "score", "word_score", "display", "words")),
    "players[].display.word", "players[].display.votes", "players[].display.bonus",
    "players[].words[].word", "players[].words[].points",
}
REPLY = {"status", "word", "changes", "changes[].row", "changes[].col", "changes[].pct"}
EVENTS = {  # docs/multiplayer.md §6.5
    "snapshot": VIEW,
    "queue": {"waiting", "fill_ends_in_ms"},
    "match_found": {"match_id", "players", "starts_in_ms"},
    "start": {"word1", "word2", "ends_in_ms"},
    "tiles": {"changes", "changes[].row", "changes[].col", "changes[].pct"},
    "found": {"nickname", "letters"},
    "warning": {"ends_in_ms"},
    "voting": {"ends_in_ms", "candidates", "candidates[].id", "candidates[].word"},
    "voted": {"voted", "of"},
    "end": RESULTS,
}


def test_payloads_hold_only_what_the_player_may_see(host: Host, player: Player, db_path: Path) -> None:
    """Every MatchView, stream event, reply and Results in every state, against allow-lists, and the
    values the allow-lists can't see: the board, other players' words, nicknames and user ids."""
    names = ("Alice", "Bob", "Cyd")
    clients = {n: player(n) for n in names}
    ids = user_ids(db_path)
    views: list[tuple[str, str, dict]] = []  # (nickname, state, MatchView)

    def look() -> None:
        for n, c in clients.items():
            view = c.get(f"{M}/current").json()
            views.append((n, view["state"], view))

    streams = {}
    for n, c in clients.items():
        views.append((n, "queued", queue(c)))
        streams[n] = host.call(host.lobby.connect, ids[n], host.clock.now())  # what /stream relays
    look()
    host.clock.advance(FILL_S)
    [pick] = host.call(host.lobby.tick, host.clock.now())
    look()  # forming
    host.call(host.lobby.pair_picked, pick.match_id, GARDEN, host.clock.now())
    look()  # countdown
    host.go()
    look()  # running
    match_id = pick.match_id

    def play(n: str, action: str, word: str) -> httpx.Response:
        return clients[n].post(f"{M}/{match_id}/{action}", json={"tiles": coords(word)})

    replies = [play("Alice", "submit", "GEAR"), play("Cyd", "submit", "NEAR"), play("Cyd", "submit", "GSAS"),
               play("Alice", "submit", "GEAR"), play("Bob", "display", "DEER"), play("Alice", "display", "RUNS")]
    host.clock.advance(match.DURATION_S - match.WARNING_S)
    host.tick()
    look()  # running, after the warning
    host.clock.advance(END - (match.DURATION_S - match.WARNING_S))
    host.tick()
    look()  # voting
    host.clock.advance(1)
    for n, c in clients.items():
        assert c.post(f"{M}/{match_id}/vote", json={"ranking": list(candidates(c).values())}).status_code == 204
    look()  # idle again
    results = [c.get(f"{M}/{match_id}/results").json() for c in clients.values()]
    events = {n: drain(q) for n, q in streams.items()}

    assert [state for n, state, _ in views if n == "Alice"] == [
        "queued", "queued", "forming", "countdown", "running", "running", "voting", "idle"]
    for n, state, view in views:
        assert paths(view) <= VIEW, (n, state)
    for r in replies:
        assert paths(r.json()) <= REPLY
    for res in results:
        assert paths(res) <= RESULTS and res == results[0]
    for n, items in events.items():
        assert [e for e, _ in items][0::len(items) - 1] == ["snapshot", "end"]
        for event, data in items:
            assert paths(data) <= EVENTS[event], (n, event)
    assert {e for items in events.values() for e, _ in items} == set(EVENTS)

    everything = json.dumps([views, [r.json() for r in replies], results, events])
    assert not [n for n, uid in ids.items() if uid in everything]  # no user id, anywhere

    found = {"Alice": {"GEAR"}, "Bob": set(), "Cyd": {"NEAR"}}
    shown = {"Alice": {"RUNS"}, "Bob": {"DEER"}, "Cyd": set()}
    for n in names:
        theirs = set().union(*(found[o] for o in names if o != n))
        their_display = set().union(*(shown[o] for o in names if o != n))
        stream = [(e, json.dumps(d)) for e, d in events[n]]
        before = {e: [t for _, t in stream[:[x for x, _ in stream].index(e)]] for e in ("start", "voting", "end")}
        mine = [(state, json.dumps(v)) for o, state, v in views if o == n]
        assert not [t for t in before["start"] + [t for s, t in mine if s in ("queued", "forming", "countdown")]
                    if "GARDEN" in t or "SUMMER" in t], n  # the board only from GO
        assert not [t for t in before["end"] + [t for s, t in mine if s != "idle"] if any(w in t for w in theirs)], n
        assert not [t for t in before["voting"] + [t for s, t in mine if s in ("queued", "forming", "countdown", "running")]
                    if any(w in t for w in their_display)], n
        voting = next(t for e, t in stream if e == "voting")
        assert not [o for o in names if o in voting], n  # display words without names


# --- live: the stream, concurrency and shutdown (live_match_server) ---

class Stream:
    """An open /api/match/stream, read as (event, data) pairs; `: ping` comments are skipped."""

    def __init__(self, response: httpx.Response) -> None:
        self.lines = response.aiter_lines()

    async def _read(self) -> tuple[str, dict] | None:
        event, data = None, None
        async for line in self.lines:
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
            elif not line and event is not None:
                return event, data
        return None

    async def next(self) -> tuple[str, dict] | None:
        """The next event within 2 s; None once the server has closed the stream."""
        return await asyncio.wait_for(self._read(), 2)

    async def until(self, event: str) -> dict:
        while (item := await self.next()) is not None:
            if item[0] == event:
                return item[1]
        raise AssertionError(f"the stream closed before {event!r}")

    async def ping(self) -> None:
        """A keepalive comment within 2 s."""
        async def read() -> None:
            async for line in self.lines:
                if line.startswith(":"):
                    assert line == ": ping"
                    return
            raise AssertionError("the stream closed without a ping")

        await asyncio.wait_for(read(), 2)


@asynccontextmanager
async def open_stream(http: httpx.AsyncClient, headers: dict[str, str]) -> AsyncIterator[Stream]:
    async with http.stream("GET", f"{M}/stream", headers=headers) as r:
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert (r.headers["cache-control"], r.headers["x-accel-buffering"]) == ("no-store", "no")
        yield Stream(r)


def live_players(server: LiveServer, *names: str) -> dict[str, dict[str, str]]:
    return {n: live_player(server.base_url, n) for n in names}


@dataclass
class Table:
    """Players on the live server, each with an open stream, in a match on GARDEN × SUMMER."""
    http: httpx.AsyncClient
    clock: FakeClock
    headers: dict[str, dict[str, str]]
    streams: dict[str, Stream]
    match_id: str = ""

    async def post(self, n: str, action: str, body: dict) -> httpx.Response:
        return await self.http.post(f"{M}/{self.match_id}/{action}", json=body, headers=self.headers[n])

    async def until(self, event: str) -> dict[str, dict]:
        """`event` on every player's stream: its data by nickname."""
        return {n: await s.until(event) for n, s in self.streams.items()}


@asynccontextmanager
async def table(server: LiveServer, headers: dict[str, dict[str, str]]) -> AsyncIterator[Table]:
    """The players queue and open their streams; the fill window and the countdown pass, and every
    stream has seen `start`."""
    async with httpx.AsyncClient(base_url=server.base_url, timeout=10) as http, AsyncExitStack() as stack:
        t = Table(http, server.clock, headers, {})
        for n, h in headers.items():
            assert (await http.post(f"{M}/queue", json={}, headers=h)).json()["state"] == "queued"
            t.streams[n] = await stack.enter_async_context(open_stream(http, h))
            assert (await t.streams[n].next())[0] == "snapshot"
        server.clock.advance(FILL_S)
        t.match_id = next(iter((await t.until("match_found")).values()))["match_id"]
        server.clock.advance(match.COUNTDOWN_S)
        assert {(s["word1"], s["word2"]) for s in (await t.until("start")).values()} == {("GARDEN", "SUMMER")}
        yield t


def test_two_clients_play_a_full_match(live_match_server: LiveServer, db_path: Path) -> None:
    server = live_match_server
    headers = live_players(server, "Alice", "Bob")

    async def scenario() -> list[dict]:
        async with table(server, headers) as t:
            assert (await t.post("Alice", "submit", {"tiles": coords("GEAR")})).json()["status"] == "accepted"
            assert await t.streams["Bob"].until("found") == {"nickname": "Alice", "letters": 4}
            assert (await t.post("Alice", "display", {"tiles": coords("RUNS")})).status_code == 200
            assert (await t.post("Bob", "display", {"tiles": coords("DEER")})).status_code == 200
            t.clock.advance(END)
            ballots = await t.until("voting")
            t.clock.advance(1)
            for n, ballot in ballots.items():
                [only] = ballot["candidates"]
                assert (await t.post(n, "vote", {"ranking": [only["id"]]})).status_code == 204
            ends = await t.until("end")  # at once: everyone has voted
            for s in t.streams.values():
                assert await s.next() is None  # the server closes the stream after `end`
            for h in headers.values():
                assert (await t.http.get(f"{M}/stream", headers=h)).status_code == 204
            fetched = [(await t.http.get(f"{M}/{t.match_id}/results", headers=h)).json() for h in headers.values()]
            return [*ends.values(), *fetched]

    results = asyncio.run(scenario())
    assert all(r == results[0] for r in results)
    assert [(p["nickname"], p["score"], p["word_score"], p["display"]) for p in results[0]["players"]] == [
        ("Alice", 9, 4, {"word": "RUNS", "votes": 5, "bonus": 5}),
        ("Bob", 5, 0, {"word": "DEER", "votes": 5, "bonus": 5}),
    ]
    server.stop()  # the lifespan waits for a save still running
    assert rows(db_path, "SELECT word, kind FROM match_submissions ORDER BY id") == [
        ("GEAR", "word"), ("RUNS", "display"), ("DEER", "display")]
    assert rows(db_path, "SELECT COUNT(*) FROM match_votes") == [(2,)]


def test_parallel_identical_submits_score_once(live_match_server: LiveServer, db_path: Path) -> None:
    server = live_match_server
    headers = live_players(server, "Alice", "Bob")

    async def scenario() -> dict:
        async with table(server, headers) as t:
            responses = await asyncio.gather(*(t.post("Alice", "submit", {"tiles": coords("GEAR")}) for _ in range(20)))
            assert [r.status_code for r in responses] == [200] * 20
            assert sorted(r.json()["status"] for r in responses) == ["accepted"] + ["duplicate"] * 19
            t.clock.advance(END)
            return (await t.until("end"))["Alice"]

    res = asyncio.run(scenario())
    assert [w for p in res["players"] for w in p["words"]] == [{"word": "GEAR", "points": 4}]
    server.stop()
    assert rows(db_path, "SELECT word, is_valid FROM match_submissions") == [("GEAR", 1)]


def test_two_players_racing_for_the_same_word(live_match_server: LiveServer) -> None:
    server = live_match_server
    headers = live_players(server, "Alice", "Bob")

    async def scenario() -> dict:
        async with table(server, headers) as t:
            replies = await asyncio.gather(*(t.post(n, "submit", {"tiles": coords("GEAR")}) for n in headers))
            assert [r.json()["status"] for r in replies] == ["accepted", "accepted"]
            t.clock.advance(END)
            return (await t.until("end"))["Alice"]

    res = asyncio.run(scenario())
    assert sorted(w["points"] for p in res["players"] for w in p["words"]) == [2, 4]  # the second finder pays
    assert (res["pct"][4][0], res["pct"][5][1]) == (25, 25)  # GE and AR, halved twice


def test_parallel_identical_ballots_count_once(live_match_server: LiveServer, db_path: Path) -> None:
    server = live_match_server
    headers = live_players(server, "Alice", "Bob")

    async def scenario() -> None:
        async with table(server, headers) as t:
            await t.post("Alice", "display", {"tiles": coords("RUNS")})
            await t.post("Bob", "display", {"tiles": coords("DEER")})
            t.clock.advance(END)
            ballots = await t.until("voting")
            t.clock.advance(1)
            [deer] = ballots["Alice"]["candidates"]
            replies = await asyncio.gather(*(t.post("Alice", "vote", {"ranking": [deer["id"]]}) for _ in range(10)))
            assert sorted(r.status_code for r in replies) == [204] + [409] * 9
            assert {r.json()["error"]["code"] for r in replies if r.status_code == 409} == {"already_voted"}
            [runs] = ballots["Bob"]["candidates"]
            assert (await t.post("Bob", "vote", {"ranking": [runs["id"]]})).status_code == 204
            await t.until("end")

    asyncio.run(scenario())
    server.stop()
    assert rows(db_path, "SELECT COUNT(*), COUNT(DISTINCT voter_id) FROM match_votes") == [(2, 2)]


async def eventually(check: Callable[[], bool]) -> None:
    """Up to 2 s for check() to hold, for what no stream event announces."""
    deadline = time.monotonic() + 2
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


def test_the_stream_starts_with_a_snapshot_is_replaced_and_keeps_alive(live_match_server: LiveServer) -> None:
    server = live_match_server
    lobby: Lobby = server.app.state.lobby
    alice = live_players(server, "Alice")["Alice"]

    async def scenario() -> None:
        async with httpx.AsyncClient(base_url=server.base_url, timeout=10) as http:
            current = lambda: http.get(f"{M}/current", headers=alice)  # noqa: E731
            assert (await http.post(f"{M}/queue", json={}, headers=alice)).status_code == 200
            [uid] = lobby.queue
            async with open_stream(http, alice) as first:
                event, view = await first.next()
                assert event == "snapshot" and view["state"] == "queued"
                await first.ping()  # SSE_KEEPALIVE_S is 0.2 s in tests
                async with open_stream(http, alice) as second:
                    assert await first.next() == ("replaced", {})
                    assert await first.next() is None  # and closed
                    assert (await second.next())[0] == "snapshot"
            # The browser closed it: the server counts the stream as gone, and drops Alice after 10 s.
            await eventually(lambda: not lobby.hub.connected(uid))
            server.clock.advance(10.001)
            await eventually(lambda: uid not in lobby.queue)
            assert (await current()).json()["state"] == "idle"
            assert (await http.post(f"{M}/queue", json={}, headers=alice)).status_code == 200
            async with open_stream(http, alice) as again:  # a reconnect gets a fresh snapshot
                assert (await again.next())[0] == "snapshot"
                assert (await http.post(f"{M}/queue/leave", json={}, headers=alice)).status_code == 204
                assert await again.next() is None  # leaving ends the stream without an event

    asyncio.run(scenario())


def test_shutdown_cuts_open_streams_and_finishes_a_pending_save(
    live_match_server: LiveServer, db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = live_match_server
    real_save = db.save_match

    def slow_save(conn, record) -> None:
        time.sleep(2.5)  # a slow disk: the save is still running when the lifespan shutdown starts
        real_save(conn, record)

    monkeypatch.setattr(db, "save_match", slow_save)
    headers = live_players(server, "Alice", "Bob", "Cyd")

    async def scenario() -> float:
        async with table(server, {n: headers[n] for n in ("Alice", "Bob")}) as t:
            await t.post("Alice", "submit", {"tiles": coords("GEAR")})
            assert (await t.http.post(f"{M}/queue", json={}, headers=headers["Cyd"])).status_code == 200
            async with open_stream(t.http, headers["Cyd"]) as cy:  # still open at shutdown: uvicorn cuts it
                assert (await cy.next())[0] == "snapshot"
                t.clock.advance(END)
                await t.until("end")  # the save starts at the next tick
                began = time.monotonic()
                await asyncio.to_thread(server.stop)
                return time.monotonic() - began

    assert asyncio.run(scenario()) < 4  # 2 s for the streams, then up to 2 s for pending saves
    assert rows(db_path, "SELECT word1, word2 FROM matches") == [("GARDEN", "SUMMER")]
    assert server.app.state.lobby.rooms == {}  # the ticker saw the save through, so the lifespan waited for it
