"""Step M4: the lobby (docs/test-strategy.md §6, docs/multiplayer.md §4 and §6.3).

The lobby is driven synchronously: tests call tick(), pair_picked() and saved() with a FakeClock,
with no event loop and no threads. Only the run() cases start the real ticker, and they wait for
events, never for a fixed time.
"""

import asyncio
import json
import logging
import random
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.conftest import FakeClock, make_settings
from tests.test_match import GARDEN_SUMMER, tiles
from trur import daily, db, games, match
from trur.config import Settings
from trur.errors import ApiError
from trur.lobby import KEEP_FAILED, TERMINAL, Hub, Item, Lobby, PickJob, SaveJob, public_results, run
from trur.solver import Lexicon, Tile

SETTINGS = Settings(match_max_active=2, match_max_queue=8)  # small caps, cheap to reach
NICKS = dict(zip("ABCDEFGHIJ", ("Ann", "Ben", "Cy", "Dee", "Eve", "Fay", "Gus", "Hal", "Ivy", "Jo")))
GARDEN = ("GARDEN", "SUMMER", GARDEN_SUMMER)
END = match.DURATION_S + 0.301  # from starts_at: the first tick that closes play


class RecordingHub(Hub):
    """A real Hub that also records every publish (docs/test-strategy.md §6)."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[list[str], str, dict]] = []

    def publish(self, user_ids, event, data) -> None:
        user_ids = list(user_ids)
        self.sent.append((user_ids, event, data))
        super().publish(user_ids, event, data)

    def events(self, user_id: str) -> list[str]:
        return [event for uids, event, _ in self.sent if user_id in uids]

    def last(self, user_id: str, event: str) -> dict:
        return [data for uids, e, data in self.sent if user_id in uids and e == event][-1]


@pytest.fixture
def hub() -> RecordingHub:
    return RecordingHub()


@pytest.fixture
def lobby(lex: Lexicon, hub: RecordingHub) -> Lobby:
    return Lobby(lex, SETTINGS, hub, random.Random(0))


def join(lobby: Lobby, clock: FakeClock, *uids: str, stream: bool = True) -> dict[str, asyncio.Queue[Item]]:
    """Queue these players, each with an open stream unless stream=False."""
    streams = {}
    for uid in uids:
        lobby.join(uid, NICKS[uid], NICKS[uid].lower(), clock.now())
        if stream:
            streams[uid] = lobby.connect(uid, clock.now())
    return streams


def drive(lobby: Lobby, clock: FakeClock, pick=GARDEN, saves: bool | None = None) -> list[PickJob | SaveJob]:
    """tick() at the clock's time and resolve its jobs at once (docs/test-strategy.md §6): a PickJob
    with `pick` (a pair, or LookupError), a SaveJob with `saves` (True, False, or None: left pending)."""
    now = clock.now()
    jobs = lobby.tick(now)
    for job in jobs:
        if isinstance(job, PickJob):
            if pick is LookupError:
                lobby.pick_failed(job.match_id, now)
            else:
                lobby.pair_picked(job.match_id, pick, now)
        elif saves is not None:
            lobby.saved(job.record.id, saves, now)
    return jobs


def start(lobby: Lobby, clock: FakeClock, players: str = "AB") -> str:
    """Queue, form at the end of the fill window, pick GARDEN × SUMMER, tick to GO. The clock ends at starts_at."""
    join(lobby, clock, *players)
    clock.advance(SETTINGS.match_fill_s)
    [job] = drive(lobby, clock)
    clock.advance(match.COUNTDOWN_S)
    drive(lobby, clock)
    return job.match_id


def board(lobby: Lobby, match_id: str, word: str) -> list[Tile]:
    return tiles(lobby.rooms[match_id].m, word)


def play(lobby: Lobby, clock: FakeClock, uid: str, match_id: str, word: str) -> match.SubmitOutcome:
    return lobby.submit(uid, match_id, board(lobby, match_id, word), clock.now())


def voting(lobby: Lobby, clock: FakeClock, players: str = "ABC", displays: str = "AB") -> str:
    """A match whose `displays` set the display words GEAR, DEER, RUNS (in that order), ticked into
    the vote. Ballots count only after the close, so the clock ends 1 s into the vote."""
    match_id = start(lobby, clock, players)
    for uid, word in zip(displays, ("GEAR", "DEER", "RUNS")):
        lobby.display(uid, match_id, board(lobby, match_id, word), clock.now())
    clock.advance(END)
    drive(lobby, clock)
    assert lobby.rooms[match_id].state == "voting"
    clock.advance(1)
    return match_id


def closed_at(lobby: Lobby, match_id: str):
    return lobby.rooms[match_id].m.closed_at


def candidate(lobby: Lobby, match_id: str, author: str) -> str:
    return next(c for c, uid in lobby.rooms[match_id].m.candidates.items() if uid == author)


def close_stream(lobby: Lobby, uid: str, now) -> None:
    """The player's tab goes away: a fresh stream (replacing any) is opened and closed."""
    lobby.hub.unsubscribe(uid, lobby.connect(uid, now), now)


def drain(q: asyncio.Queue[Item]) -> list[Item]:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def refusal(call: Callable[[], object]) -> tuple[int, str]:
    with pytest.raises(ApiError) as exc:
        call()
    return exc.value.status, exc.value.code


def state(lobby: Lobby, clock: FakeClock, uid: str) -> str:
    return lobby.view(uid, clock.now())["state"]


# --- the queue ---

def test_one_player_alone_never_forms_a_match(lobby: Lobby, clock: FakeClock) -> None:
    join(lobby, clock, "A")
    assert lobby.view("A", clock.now())["queue"] == {"waiting": 1, "fill_ends_in_ms": None, "timeout_in_ms": 120_000}
    for _ in range(11):
        clock.advance(10)
        assert drive(lobby, clock) == []
    assert state(lobby, clock, "A") == "queued"


def test_the_second_player_starts_the_fill_window(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    join(lobby, clock, "A")
    clock.advance(2)
    join(lobby, clock, "B")
    clock.advance(3)
    join(lobby, clock, "C")
    assert lobby.view("A", clock.now())["queue"] == {"waiting": 3, "fill_ends_in_ms": 7000, "timeout_in_ms": 115_000}
    assert hub.last("A", "queue") == {"waiting": 3, "fill_ends_in_ms": 7000}
    clock.advance(6.999)
    assert drive(lobby, clock) == []
    clock.advance(0.001)
    [job] = lobby.tick(clock.now())
    assert list(lobby.rooms[job.match_id].players) == ["A", "B", "C"]
    assert {state(lobby, clock, u) for u in "ABC"} == {"forming"}


def test_the_sixth_player_forms_at_once_and_the_rest_start_a_new_queue(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    join(lobby, clock, *"ABCDEFGH")
    [job] = lobby.tick(clock.now())  # the window still has 10 s to go
    assert list(lobby.rooms[job.match_id].players) == list("ABCDEF")
    assert lobby.view("G", clock.now())["queue"] == {"waiting": 2, "fill_ends_in_ms": 10_000, "timeout_in_ms": 120_000}
    assert hub.last("H", "queue") == {"waiting": 2, "fill_ends_in_ms": 10_000}
    lobby.leave("H", clock.now())
    join(lobby, clock, "I")  # a 7th, alone in a new queue after the match formed
    assert lobby.view("I", clock.now())["queue"]["waiting"] == 2


def test_the_queue_timeout_fires_at_120_s(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    streams = join(lobby, clock, "A")
    clock.advance(119.999)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "queued"
    clock.advance(0.001)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "idle"
    assert drain(streams["A"])[-1] == ("queue_timeout", {}) and "queue_timeout" in TERMINAL


def test_the_timeout_waits_while_a_fill_window_runs(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    join(lobby, clock, "A")
    clock.advance(115)
    join(lobby, clock, "B")
    clock.advance(5)  # A has waited 120 s, inside B's window
    assert drive(lobby, clock) == []
    assert state(lobby, clock, "A") == "queued" and "queue_timeout" not in hub.events("A")
    clock.advance(5)
    [job] = drive(lobby, clock)
    assert list(lobby.rooms[job.match_id].players) == ["A", "B"]


def test_a_leave_that_drops_the_window_to_one_player_cancels_it(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    streams = join(lobby, clock, "A", "B")
    clock.advance(5)
    lobby.leave("B", clock.now())
    lobby.leave("B", clock.now())  # idempotent
    assert drain(streams["B"])[-1] is None  # B's stream closes, without an event
    assert state(lobby, clock, "B") == "idle"
    assert hub.last("A", "queue") == {"waiting": 1, "fill_ends_in_ms": None}
    clock.advance(5)
    assert drive(lobby, clock) == []
    clock.advance(110)  # A's own timeout still counts from A's join
    drive(lobby, clock)
    assert hub.events("A")[-1] == "queue_timeout"


def test_a_timeout_tells_the_others_who_still_wait(lex: Lexicon, clock: FakeClock, hub: RecordingHub) -> None:
    lobby = Lobby(lex, replace(SETTINGS, match_min_players=3), hub, random.Random(0))
    join(lobby, clock, "A")
    clock.advance(60)
    join(lobby, clock, "B")  # two wait, but a match needs three: no window
    clock.advance(60)
    drive(lobby, clock)
    assert hub.events("A")[-1] == "queue_timeout"
    assert hub.last("B", "queue") == {"waiting": 1, "fill_ends_in_ms": None}


def test_a_closed_stream_drops_the_player_after_10_s(lobby: Lobby, clock: FakeClock) -> None:
    join(lobby, clock, "A")
    clock.advance(30)
    close_stream(lobby, "A", clock.now())
    clock.advance(10)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "queued"
    clock.advance(0.001)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "idle"


def test_joining_without_a_stream_drops_the_player_after_10_s(lobby: Lobby, clock: FakeClock) -> None:
    lobby.join("A", "Ann", "ann", clock.now())
    close_stream(lobby, "A", clock.now())  # an old stream, closed long before the join below
    lobby.leave("A", clock.now())
    clock.advance(60)
    join(lobby, clock, "A", stream=False)
    clock.advance(10)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "queued"
    clock.advance(0.001)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "idle"


def test_a_full_queue_refuses_with_server_busy(lobby: Lobby, clock: FakeClock) -> None:
    join(lobby, clock, *"ABCDEFGH")  # MATCH_MAX_QUEUE = 8
    assert refusal(lambda: join(lobby, clock, "I")) == (503, "server_busy")
    assert state(lobby, clock, "I") == "idle"
    assert lobby.join("A", "Ann", "ann", clock.now())["state"] == "queued"  # already queued: the same view


def test_no_free_match_slot_sends_server_busy(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    first = start(lobby, clock, "AB")
    start(lobby, clock, "CD")  # MATCH_MAX_ACTIVE = 2
    streams = join(lobby, clock, "E", "F")
    clock.advance(SETTINGS.match_fill_s)
    assert drive(lobby, clock) == []
    assert drain(streams["E"])[-1] == ("server_busy", {}) and "server_busy" in TERMINAL
    assert state(lobby, clock, "E") == state(lobby, clock, "F") == "idle"
    clock.set(lobby.rooms[first].m.starts_at + timedelta(seconds=END))
    drive(lobby, clock)  # the first match ends; its save is still pending, but it no longer counts
    join(lobby, clock, "E", "F")
    clock.advance(SETTINGS.match_fill_s)
    assert len(drive(lobby, clock)) == 1


def test_a_player_in_a_match_cannot_queue_until_it_ends(lobby: Lobby, clock: FakeClock) -> None:
    match_id = start(lobby, clock, "AB")
    assert refusal(lambda: join(lobby, clock, "A")) == (409, "in_match")
    lobby.leave("A", clock.now())  # does nothing in a match
    assert state(lobby, clock, "A") == "running"
    clock.advance(END)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "idle"
    assert join(lobby, clock, "A")["A"] is not None
    assert state(lobby, clock, "A") == "queued" and match_id in lobby.rooms


# --- match states ---

def test_forming_publishes_nothing_until_the_board_is_picked(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    join(lobby, clock, "A", "B")
    clock.advance(SETTINGS.match_fill_s)
    [job] = lobby.tick(clock.now())
    view = lobby.view("A", clock.now())
    assert view["state"] == "forming" and view["match"]["players"] == ["Ann", "Ben"]
    assert view["match"]["starts_in_ms"] is None and view["match"]["word1"] is None
    assert "match_found" not in hub.events("A")
    clock.advance(0.4)  # the pick takes a while; T is when it's done
    lobby.pair_picked(job.match_id, GARDEN, clock.now())
    assert hub.sent[-1] == (["A", "B"], "match_found", {"match_id": job.match_id, "players": ["Ann", "Ben"], "starts_in_ms": 3000})
    clock.advance(1)
    view = lobby.view("B", clock.now())
    assert (view["state"], view["match"]["starts_in_ms"], view["match"]["word1"], view["match"]["pct"]) == ("countdown", 2000, None, None)
    clock.advance(1.999)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "countdown"
    clock.advance(0.001)
    drive(lobby, clock)
    assert hub.sent[-1] == (["A", "B"], "start", {"word1": "GARDEN", "word2": "SUMMER", "ends_in_ms": 60_000})
    assert state(lobby, clock, "A") == "running"


def test_no_board_cancels_the_match(lobby: Lobby, clock: FakeClock) -> None:
    streams = join(lobby, clock, "A", "B")
    clock.advance(SETTINGS.match_fill_s)
    drive(lobby, clock, pick=LookupError)
    assert drain(streams["A"])[-1] == ("match_cancelled", {}) and "match_cancelled" in TERMINAL
    assert state(lobby, clock, "A") == state(lobby, clock, "B") == "idle"
    assert lobby.rooms == {}


def test_a_player_who_leaves_during_the_countdown_is_still_in_the_results(lobby: Lobby, clock: FakeClock) -> None:
    join(lobby, clock, "A", "B")
    clock.advance(SETTINGS.match_fill_s)
    [job] = drive(lobby, clock)
    lobby.leave("A", clock.now())
    close_stream(lobby, "A", clock.now())
    clock.advance(match.COUNTDOWN_S)
    drive(lobby, clock)
    play(lobby, clock, "B", job.match_id, "GEAR")
    clock.advance(END)
    [save] = drive(lobby, clock)
    assert [p.user_id for p in save.record.players] == ["A", "B"]
    assert [p["nickname"] for p in lobby.results(job.match_id, "A")["players"]] == ["Ben", "Ann"]


def test_the_warning_is_published_once(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    start(lobby, clock, "AB")
    clock.advance(49.999)
    drive(lobby, clock)
    assert "warning" not in hub.events("A")
    clock.advance(0.001)
    drive(lobby, clock)
    drive(lobby, clock)
    assert hub.events("A").count("warning") == 1
    assert hub.last("A", "warning") == {"ends_in_ms": 10_000}


def test_one_tick_catches_up_from_the_countdown_to_the_end(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    join(lobby, clock, "A", "B")
    clock.advance(SETTINGS.match_fill_s)
    drive(lobby, clock)
    hub.sent.clear()
    clock.advance(match.COUNTDOWN_S + END)
    jobs = drive(lobby, clock)
    assert hub.events("A") == hub.events("B") == ["start", "warning", "end"]
    assert [type(j) for j in jobs] == [SaveJob]
    assert drive(lobby, clock) == []


def test_catching_up_with_two_display_words_opens_the_vote_instead(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    join(lobby, clock, "A", "B")
    clock.advance(SETTINGS.match_fill_s)
    [job] = drive(lobby, clock)
    clock.advance(match.COUNTDOWN_S)  # play has started by the clock, before any tick says so
    lobby.display("A", job.match_id, board(lobby, job.match_id, "GEAR"), clock.now())
    lobby.display("B", job.match_id, board(lobby, job.match_id, "DEER"), clock.now())
    hub.sent.clear()
    clock.advance(END)
    assert drive(lobby, clock) == []
    assert hub.events("A") == hub.events("B") == ["start", "warning", "voting"]


# --- the vote ---

def test_without_ballots_the_vote_ends_after_its_60_s(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = voting(lobby, clock, "AB", "AB")
    clock.set(closed_at(lobby, match_id) + timedelta(seconds=match.VOTE_S + 0.3))
    assert drive(lobby, clock) == []
    assert state(lobby, clock, "A") == "voting"
    clock.advance(0.001)
    [save] = drive(lobby, clock)
    assert [p["display"]["bonus"] for p in hub.last("A", "end")["players"]] == [0, 0]
    assert save.record.id == match_id


def test_fewer_than_two_display_words_skip_the_vote(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = start(lobby, clock, "AB")
    lobby.display("A", match_id, board(lobby, match_id, "GEAR"), clock.now())
    clock.advance(END)
    drive(lobby, clock)
    assert hub.events("A")[-1] == "end" and "voting" not in hub.events("A")
    assert state(lobby, clock, "A") == "idle"


def test_each_voter_gets_their_own_anonymous_ballot(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    voting(lobby, clock, "ABC", "AB")
    ballots = {uids[0]: data for uids, event, data in hub.sent if event == "voting"}
    assert set(ballots) == {"A", "B", "C"}
    assert all(set(b) == {"ends_in_ms", "candidates"} and b["ends_in_ms"] == 60_000 for b in ballots.values())
    assert all(set(c) == {"id", "word"} for b in ballots.values() for c in b["candidates"])
    words = {uid: sorted(c["word"] for c in b["candidates"]) for uid, b in ballots.items()}
    assert words == {"A": ["DEER"], "B": ["GEAR"], "C": ["DEER", "GEAR"]}
    assert lobby.view("C", clock.now())["match"]["vote"] == {"ends_in_ms": 60_000, "candidates": ballots["C"]["candidates"], "voted": False} | {"ends_in_ms": 59_000}


def test_the_vote_ends_on_the_ballot_that_leaves_everyone_done(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = voting(lobby, clock, "ABC", "AB")
    a, b = candidate(lobby, match_id, "A"), candidate(lobby, match_id, "B")
    lobby.vote("A", match_id, [b], clock.now())
    assert hub.sent[-1] == (["A", "B", "C"], "voted", {"voted": 1, "of": 3})
    lobby.vote("B", match_id, [a], clock.now())
    assert hub.sent[-1][1:] == ("voted", {"voted": 2, "of": 3})
    assert lobby.view("A", clock.now())["match"]["vote"]["voted"] is True
    lobby.vote("C", match_id, [a, b], clock.now())
    assert hub.events("C")[-2:] == ["voted", "end"]
    assert state(lobby, clock, "C") == "idle"
    [save] = lobby.tick(clock.now())  # the save follows in the same step
    assert isinstance(save, SaveJob) and save.record.id == match_id
    displays = {p["nickname"]: p["display"] for p in hub.last("A", "end")["players"]}
    assert displays["Ann"] == {"word": "GEAR", "votes": 10, "bonus": 5}  # 5 + 5 from two ballots
    assert displays["Ben"] == {"word": "DEER", "votes": 8, "bonus": 4}  # 5 + 3


def test_an_absent_voter_counts_as_done_after_10_s(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = voting(lobby, clock, "ABC", "AB")
    closed = closed_at(lobby, match_id)
    lobby.vote("A", match_id, [candidate(lobby, match_id, "B")], clock.now())
    lobby.vote("B", match_id, [candidate(lobby, match_id, "A")], clock.now())
    clock.set(closed + timedelta(seconds=5))
    close_stream(lobby, "C", clock.now())
    clock.set(closed + timedelta(seconds=15))
    drive(lobby, clock)
    assert state(lobby, clock, "C") == "voting"
    clock.advance(0.001)
    [save] = drive(lobby, clock)
    assert hub.events("A")[-1] == "end" and save.record.id == match_id
    assert lobby.connect("C", clock.now()) is None  # back after the end: idle, the results link shows the rest
    assert refusal(lambda: lobby.vote("C", match_id, [candidate(lobby, match_id, "A")], clock.now())) == (409, "match_not_voting")


def test_a_voter_who_reconnects_within_10_s_keeps_the_vote_open(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = voting(lobby, clock, "ABC", "AB")
    lobby.vote("A", match_id, [candidate(lobby, match_id, "B")], clock.now())
    lobby.vote("B", match_id, [candidate(lobby, match_id, "A")], clock.now())
    close_stream(lobby, "C", clock.now())
    clock.advance(9)
    snapshot = lobby.connect("C", clock.now()).get_nowait()
    assert snapshot[0] == "snapshot" and snapshot[1]["state"] == "voting"
    clock.advance(30)
    drive(lobby, clock)
    assert state(lobby, clock, "C") == "voting"
    lobby.vote("C", match_id, [candidate(lobby, match_id, "A")], clock.now())
    assert hub.events("C")[-1] == "end"


def test_a_voter_without_a_stream_counts_as_gone_from_the_close(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = start(lobby, clock, "ABC")
    close_stream(lobby, "C", clock.now())  # gone since GO, 60 s before the vote opens
    for uid, word in zip("AB", ("GEAR", "DEER")):
        lobby.display(uid, match_id, board(lobby, match_id, word), clock.now())
    clock.advance(END)
    drive(lobby, clock)
    clock.advance(1)
    lobby.vote("A", match_id, [candidate(lobby, match_id, "B")], clock.now())
    lobby.vote("B", match_id, [candidate(lobby, match_id, "A")], clock.now())
    assert state(lobby, clock, "A") == "voting"  # C isn't done at once
    clock.set(closed_at(lobby, match_id) + timedelta(seconds=10))
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "voting"
    clock.advance(0.001)
    drive(lobby, clock)
    assert hub.events("A")[-1] == "end"


def test_with_every_stream_gone_the_vote_ends_after_10_s(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    voting(lobby, clock, "ABC", "AB")
    for uid in "ABC":
        close_stream(lobby, uid, clock.now())
    clock.advance(10)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "voting"
    clock.advance(0.001)
    drive(lobby, clock)
    assert hub.events("A")[-1] == "end"


def test_a_join_while_voting_is_refused(lobby: Lobby, clock: FakeClock) -> None:
    voting(lobby, clock, "AB", "AB")
    assert refusal(lambda: join(lobby, clock, "A")) == (409, "in_match")


def test_ballot_refusals(lobby: Lobby, clock: FakeClock) -> None:
    match_id = voting(lobby, clock, "ABC", "AB")
    a, b = candidate(lobby, match_id, "A"), candidate(lobby, match_id, "B")
    assert refusal(lambda: lobby.vote("A", match_id, [a], clock.now())) == (422, "invalid_ballot")  # own word
    assert refusal(lambda: lobby.vote("A", match_id, ["c9"], clock.now())) == (422, "invalid_ballot")
    assert refusal(lambda: lobby.vote("C", match_id, [a, a], clock.now())) == (422, "invalid_ballot")
    assert refusal(lambda: lobby.vote("D", match_id, [a], clock.now())) == (404, "not_found")
    lobby.vote("A", match_id, [b], clock.now())
    assert refusal(lambda: lobby.vote("A", match_id, [b], clock.now())) == (409, "already_voted")


# --- play ---

def test_play_refusals(lobby: Lobby, clock: FakeClock) -> None:
    join(lobby, clock, "A", "B")
    clock.advance(SETTINGS.match_fill_s)
    [job] = lobby.tick(clock.now())
    match_id, two = job.match_id, [(0, 0), (1, 1)]
    assert refusal(lambda: lobby.submit("A", match_id, two, clock.now())) == (409, "match_not_running")  # forming
    assert refusal(lambda: lobby.submit("C", match_id, two, clock.now())) == (404, "not_found")
    assert refusal(lambda: lobby.display("A", "no-such-match", two, clock.now())) == (404, "not_found")
    assert refusal(lambda: lobby.vote("A", match_id, ["c1"], clock.now())) == (409, "match_not_voting")
    lobby.pair_picked(match_id, GARDEN, clock.now())
    assert refusal(lambda: lobby.display("A", match_id, two, clock.now())) == (409, "match_not_running")  # countdown
    clock.advance(match.COUNTDOWN_S)
    drive(lobby, clock)
    play(lobby, clock, "A", match_id, "GEAR")
    assert refusal(lambda: play(lobby, clock, "A", match_id, "GENRES")) == (409, "tile_locked")  # GE is A's
    assert refusal(lambda: lobby.submit("A", match_id, [(0, 0)], clock.now())) == (422, "invalid_tiles")
    assert refusal(lambda: lobby.display("A", match_id, [(1, 1), (1, 1)], clock.now())) == (422, "invalid_tiles")
    assert refusal(lambda: lobby.vote("A", match_id, ["c1"], clock.now())) == (409, "match_not_voting")
    clock.advance(END)
    drive(lobby, clock)
    assert refusal(lambda: play(lobby, clock, "B", match_id, "GEAR")) == (409, "match_not_running")  # over, not yet saved


def test_a_display_word_publishes_exactly_what_a_found_word_does(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = start(lobby, clock, "ABC")
    assert play(lobby, clock, "A", match_id, "GEAR").status == "accepted"
    tiles_word, found_word = hub.sent[-2:]
    assert tiles_word == (["A", "B", "C"], "tiles", {"changes": [{"row": 4, "col": 0, "pct": 50}, {"row": 5, "col": 1, "pct": 50}]})
    assert found_word == (["B", "C"], "found", {"nickname": "Ann", "letters": 4})  # everyone but the finder
    lobby.display("A", match_id, board(lobby, match_id, "DEER"), clock.now())
    tiles_display, found_display = hub.sent[-2:]
    assert tiles_display == (["A", "B", "C"], "tiles", {"changes": [{"row": 4, "col": 3, "pct": 50}, {"row": 5, "col": 4, "pct": 50}]})
    assert found_display == found_word


def test_rejected_and_duplicate_words_publish_nothing(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = start(lobby, clock, "AB")
    play(lobby, clock, "A", match_id, "GEAR")
    sent = len(hub.sent)
    assert play(lobby, clock, "A", match_id, "GEAR").status == "duplicate"
    assert play(lobby, clock, "B", match_id, "RUNS").status == "rejected"
    assert len(hub.sent) == sent


def test_the_view_shows_only_the_callers_own_play(lobby: Lobby, clock: FakeClock) -> None:
    match_id = start(lobby, clock, "AB")
    play(lobby, clock, "A", match_id, "GEAR")
    lobby.display("A", match_id, board(lobby, match_id, "DEER"), clock.now())
    clock.advance(20)
    a = lobby.view("A", clock.now())
    assert a["state"] == "running" and a["queue"] is None
    assert a["match"] == {
        "match_id": match_id, "players": ["Ann", "Ben"], "starts_in_ms": None, "ends_in_ms": 40_000,
        "word1": "GARDEN", "word2": "SUMMER", "pct": match.pct_grid(lobby.rooms[match_id].m),
        "locked": [[4, 0], [4, 3], [5, 1], [5, 4]], "found": ["GEAR"], "display": "DEER", "vote": None,
    }
    b = lobby.view("B", clock.now())["match"]
    assert (b["locked"], b["found"], b["display"], b["pct"]) == ([], [], None, a["match"]["pct"])
    assert b["pct"][4][0] == 50


def test_a_last_moment_word_is_in_the_results(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = start(lobby, clock, "AB")
    clock.advance(match.DURATION_S + 0.001)
    drive(lobby, clock)
    assert "end" not in hub.events("A")
    clock.advance(0.298)
    assert play(lobby, clock, "B", match_id, "NEARER").status == "accepted"  # at ends_at + 299 ms
    clock.advance(0.002)
    [save] = drive(lobby, clock)  # ends_at + 301 ms
    ben = next(p for p in hub.last("A", "end")["players"] if p["nickname"] == "Ben")
    assert ben["words"] == [{"word": "NEARER", "points": 9}]
    assert [w.word for p in save.record.players if p.user_id == "B" for w in p.words] == ["NEARER"]


# --- the Hub ---

def test_a_second_stream_replaces_the_first(lobby: Lobby, clock: FakeClock) -> None:
    join(lobby, clock, "A", stream=False)
    first = lobby.connect("A", clock.now())
    second = lobby.connect("A", clock.now())
    assert [item[0] for item in drain(first)] == ["snapshot", "replaced"] and "replaced" in TERMINAL
    lobby.hub.unsubscribe("A", first, clock.now())  # the first stream's cleanup leaves the second alone
    clock.advance(11)
    drive(lobby, clock)
    assert state(lobby, clock, "A") == "queued"
    assert drain(second) == [("snapshot", lobby.view("A", clock.now() - timedelta(seconds=11)))]


def test_the_snapshot_comes_first_and_an_idle_player_gets_no_stream(lobby: Lobby, clock: FakeClock) -> None:
    assert lobby.connect("A", clock.now()) is None  # → 204
    join(lobby, clock, "A", "B", stream=False)
    q = lobby.connect("A", clock.now())
    assert q.get_nowait() == ("snapshot", lobby.view("A", clock.now()))
    assert q.empty()


# --- results and saving ---

def test_results_stay_in_memory_until_saved(lobby: Lobby, clock: FakeClock, hub: RecordingHub) -> None:
    match_id = start(lobby, clock, "AB")
    play(lobby, clock, "A", match_id, "GEAR")
    assert lobby.results(match_id, "A") is None  # not over yet
    clock.advance(END)
    [save] = lobby.tick(clock.now())
    res = lobby.results(match_id, "A")
    assert res == match.results(save.record) and lobby.results(match_id, "B") == res
    assert lobby.results(match_id, "C") is None and lobby.results("no-such-match", "A") is None
    assert hub.last("A", "end") == public_results(res)
    assert all("user_id" not in p for p in hub.last("A", "end")["players"])
    lobby.saved(match_id, True, clock.now())
    assert lobby.results(match_id, "A") is None and match_id not in lobby.rooms  # → the DB


def test_a_failed_save_keeps_the_results_for_10_minutes(lobby: Lobby, clock: FakeClock) -> None:
    match_id = start(lobby, clock, "AB")
    clock.advance(END)
    drive(lobby, clock, saves=False)
    failed_at = clock.now()
    assert start(lobby, clock, "CD") in lobby.rooms  # the lobby keeps forming matches
    clock.set(failed_at + KEEP_FAILED - timedelta(milliseconds=1))
    drive(lobby, clock)
    assert lobby.results(match_id, "A") is not None
    clock.set(failed_at + KEEP_FAILED)
    drive(lobby, clock)
    assert lobby.results(match_id, "A") is None and match_id not in lobby.rooms


# --- run(): the real ticker ---

def ticker(lobby: Lobby, clock: FakeClock, scenario: Callable[[], Awaitable[None]], pick=lambda: GARDEN, save=lambda rec: None) -> None:
    """Run `scenario` alongside run() with a 1-ms tick, then stop it as the lifespan would."""
    async def main() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(run(lobby, clock, pick, save, stop, 0.001))
        try:
            await scenario()
        finally:
            stop.set()
            await task

    asyncio.run(main())


async def until(q: asyncio.Queue[Item], event: str) -> dict:
    """The data of the stream's next `event`; fails after 2 s without it."""
    while True:
        item = await asyncio.wait_for(q.get(), 2)
        if item is not None and item[0] == event:
            return item[1]


def test_run_plays_a_match_and_saves_it(lobby: Lobby, clock: FakeClock) -> None:
    saved: list[match.MatchRecord] = []
    streams = join(lobby, clock, "A", "B")

    async def scenario() -> None:
        clock.advance(SETTINGS.match_fill_s)
        found = await until(streams["A"], "match_found")
        clock.advance(match.COUNTDOWN_S)
        await until(streams["A"], "start")
        play(lobby, clock, "A", found["match_id"], "GEAR")
        clock.advance(END)
        await until(streams["B"], "end")

    ticker(lobby, clock, scenario, save=saved.append)
    assert [[w.word for w in p.words] for p in saved[0].players] == [["GEAR"], []]
    assert lobby.rooms == {}  # saved before run() returned, so dropped from memory


def test_run_saves_a_vote_that_ended_just_before_stop(lobby: Lobby, clock: FakeClock) -> None:
    saved: list[match.MatchRecord] = []
    streams = join(lobby, clock, "A", "B")

    async def scenario() -> None:
        clock.advance(SETTINGS.match_fill_s)
        found = await until(streams["A"], "match_found")
        match_id = found["match_id"]
        clock.advance(match.COUNTDOWN_S)
        await until(streams["A"], "start")
        lobby.display("A", match_id, board(lobby, match_id, "GEAR"), clock.now())
        lobby.display("B", match_id, board(lobby, match_id, "DEER"), clock.now())
        clock.advance(END)
        await until(streams["A"], "voting")
        clock.advance(1)
        lobby.vote("A", match_id, [candidate(lobby, match_id, "B")], clock.now())
        lobby.vote("B", match_id, [candidate(lobby, match_id, "A")], clock.now())  # ends the vote; stop follows at once

    ticker(lobby, clock, scenario, save=saved.append)
    assert len(saved) == 1 and lobby.rooms == {}


def test_run_cancels_a_match_without_a_board(lobby: Lobby, clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    errors = iter([LookupError, RuntimeError("database is locked")])

    def pick():
        raise next(errors)

    async def scenario() -> None:
        for _ in range(2):
            streams = join(lobby, clock, "A", "B")
            clock.advance(SETTINGS.match_fill_s)
            await until(streams["B"], "match_cancelled")

    with caplog.at_level(logging.ERROR, logger="trur"):
        ticker(lobby, clock, scenario, pick=pick)
    assert lobby.rooms == {} and state(lobby, clock, "A") == "idle"
    [record] = caplog.records  # the RuntimeError only: no board is an expected outcome
    assert record.getMessage().startswith("picking a board for match") and "database is locked" in caplog.text


def test_run_logs_a_failed_save_and_keeps_the_results(lobby: Lobby, clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    streams = join(lobby, clock, "A", "B")

    def save(rec: match.MatchRecord) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    async def scenario() -> None:
        clock.advance(SETTINGS.match_fill_s)
        await until(streams["A"], "match_found")
        clock.advance(match.COUNTDOWN_S + END)
        await until(streams["A"], "end")

    with caplog.at_level(logging.ERROR, logger="trur"):
        ticker(lobby, clock, scenario, save=save)
    [room] = lobby.rooms.values()
    assert room.keep_until == clock.now() + KEEP_FAILED and lobby.results(room.id, "A") is not None
    assert "failed; its results stay in memory for 10 min" in caplog.text and "disk I/O error" in caplog.text


def test_run_survives_a_failing_tick(lobby: Lobby, clock: FakeClock, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    real_tick, calls = lobby.tick, []

    def flaky_tick(now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("bug")
        return real_tick(now)

    monkeypatch.setattr(lobby, "tick", flaky_tick)
    streams = join(lobby, clock, "A", "B")

    async def scenario() -> None:
        clock.advance(SETTINGS.match_fill_s)
        await until(streams["A"], "match_found")

    with caplog.at_level(logging.ERROR, logger="trur"):
        ticker(lobby, clock, scenario)
    assert "lobby tick failed" in caplog.text


# --- board picking (docs/multiplayer.md §3.5) ---

TODAY = date(2026, 10, 11)  # the FakeClock's day


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(db_path)
    yield c
    c.close()


def daily_settings(tmp_path: Path, *pairs: tuple[str, str, str]) -> Settings:
    """Settings whose daily JSON holds these (date, word1, word2) entries."""
    path = tmp_path / "daily.json"
    path.write_text(json.dumps([{"date": d, "word1": w1, "word2": w2} for d, w1, w2 in pairs]), encoding="utf-8")
    return make_settings(tmp_path, daily_json_source=str(path))


def picked_words(conn: sqlite3.Connection, lex: Lexicon, s: Settings) -> set[str]:
    return {w for seed in range(50) for w in games.pick_match_pair(conn, lex, s, random.Random(seed), TODAY)[:2]}


def test_match_boards_skip_todays_and_tomorrows_daily_words(conn: sqlite3.Connection, lex: Lexicon, tmp_path: Path) -> None:
    s = daily_settings(tmp_path, ("2026-10-11", "MEDICAL", "CORRECT"), ("2026-10-12", "STRONG", "MEANING"))
    assert picked_words(conn, lex, s) == {"GARDEN", "SUMMER"}
    assert daily.get_daily_pair(conn, TODAY + timedelta(days=1)) is not None  # imported for the check


def test_tomorrow_not_picked_yet_excludes_only_today(conn: sqlite3.Connection, lex: Lexicon, tmp_path: Path) -> None:
    words = picked_words(conn, lex, daily_settings(tmp_path, ("2026-10-11", "MEDICAL", "CORRECT")))
    assert words & {"STRONG", "MEANING"} and not words & {"MEDICAL", "CORRECT"}


def test_no_daily_pair_to_protect(conn: sqlite3.Connection, lex: Lexicon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_pair(*args: object) -> None:
        raise ApiError(503, "no_pair_available", "No daily pair is available right now.")

    monkeypatch.setattr(daily, "ensure_daily_pair", no_pair)
    assert picked_words(conn, lex, daily_settings(tmp_path)) >= {"MEDICAL", "CORRECT"}


def test_a_board_needs_exactly_min_match_words(conn: sqlite3.Connection, lex: Lexicon, tmp_path: Path) -> None:
    s = daily_settings(tmp_path, ("2026-10-11", "MEDICAL", "CORRECT"), ("2026-10-12", "STRONG", "MEANING"))
    most = max(len(lex.solve("GARDEN", "SUMMER")), len(lex.solve("SUMMER", "GARDEN")))
    assert len(games.pick_match_pair(conn, lex, replace(s, min_match_words=most), random.Random(0), TODAY)[2]) == most
    with pytest.raises(LookupError):
        games.pick_match_pair(conn, lex, replace(s, min_match_words=most + 1), random.Random(0), TODAY)
