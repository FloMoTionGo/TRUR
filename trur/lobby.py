"""Multiplayer lobby: the quick-match queue, running matches, live streams and the ticker
(docs/multiplayer.md §4, §6.3).

Stdlib plus asyncio. All state lives on the event loop, and every method here is synchronous, so
submits and ballots are handled one at a time with no `await` between check and update (§7 M3).
Time comes in as `now`. `tick()` changes state, publishes, and returns the jobs that need a
thread (picking a board, saving a match); `run()` runs them and feeds the results back.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, NamedTuple

from trur import match
from trur.clock import Clock
from trur.config import Settings
from trur.errors import ApiError
from trur.match import ms_until
from trur.solver import Lexicon, Tile

log = logging.getLogger("trur")

KEEP_FAILED = timedelta(minutes=10)  # the results of a failed save stay in memory this long
SAVE_WAIT_S = 2.0  # on stop; with the 2-s stream cut that stays under Fly's 5-s kill_timeout (§6.8)
TERMINAL = frozenset({"end", "queue_timeout", "server_busy", "match_cancelled", "replaced"})  # the stream closes after these

Item = tuple[str, dict] | None  # (event, data); None ends the stream without an event
Pair = tuple[str, str, tuple[str, ...]]  # word1, word2, answers
State = Literal["forming", "countdown", "running", "voting", "ended"]


class Hub:
    """One asyncio.Queue per open stream, keyed by user_id (§6.5).

    Only the event loop publishes, so every stream gets its events in order.
    """

    def __init__(self) -> None:
        self._streams: dict[str, asyncio.Queue[Item]] = {}
        self._gone: dict[str, datetime] = {}

    def subscribe(self, user_id: str) -> asyncio.Queue[Item]:
        """A new stream for this player. An older one gets `replaced`, and then closes."""
        old = self._streams.get(user_id)
        if old is not None:
            old.put_nowait(("replaced", {}))
        q: asyncio.Queue[Item] = asyncio.Queue()
        self._streams[user_id] = q
        self._gone.pop(user_id, None)
        return q

    def unsubscribe(self, user_id: str, q: asyncio.Queue[Item], now: datetime) -> None:
        """Only removes q if it is still the player's current stream."""
        if self._streams.get(user_id) is q:
            del self._streams[user_id]
            self._gone[user_id] = now

    def connected(self, user_id: str) -> bool:
        return user_id in self._streams

    def gone_since(self, user_id: str) -> datetime | None:
        """When the player's last stream closed; None while connected, or if they never had one."""
        return self._gone.get(user_id)

    def publish(self, user_ids: Iterable[str], event: str, data: dict) -> None:
        for uid in user_ids:
            q = self._streams.get(uid)
            if q is not None:
                q.put_nowait((event, data))

    def close(self, user_ids: Iterable[str]) -> None:
        """End these players' streams without an event."""
        for uid in user_ids:
            q = self._streams.get(uid)
            if q is not None:
                q.put_nowait(None)


class PickJob(NamedTuple):
    match_id: str


class SaveJob(NamedTuple):
    record: match.MatchRecord


@dataclass
class Waiting:
    nickname: str
    sort_key: str  # nickname_key; orders tied places
    joined_at: datetime


@dataclass
class Room:
    """One match, from forming until it is saved (or its kept results expire)."""
    id: str
    players: dict[str, Waiting]  # user_id → player, in join order; fixed when the match forms
    state: State = "forming"
    m: match.Match | None = None  # from pair_picked on
    warned: bool = False
    ballots: dict[str, list[match.Candidate]] = field(default_factory=dict)  # per voter, drawn when the vote opens
    results: dict | None = None  # from the end on, with user_ids
    keep_until: datetime | None = None  # set by a failed save


def _started(room: Room) -> match.Match:
    """The room's match; NotRunning while its board is still being picked."""
    if room.m is None:
        raise match.NotRunning()
    return room.m


def _vote_ends_at(m: match.Match) -> datetime:
    return m.vote_ends_at  # type: ignore[return-value]  # only asked once play has closed


def public_results(res: dict) -> dict:
    """Results as the browser gets them: never a user_id (§6.4)."""
    return {**res, "players": [{k: v for k, v in p.items() if k != "user_id"} for p in res["players"]]}


_REFUSALS: dict[type[match.MatchError], tuple[int, str, str]] = {
    match.NotRunning: (409, "match_not_running", "The match isn't running."),
    match.TileLocked: (409, "tile_locked", "You've already used one of those tiles."),
    match.NotVoting: (409, "match_not_voting", "The vote isn't open."),
    match.AlreadyVoted: (409, "already_voted", "You've already voted."),
}
_INVALID_TILES = (422, "invalid_tiles", "Those tiles aren't a valid selection on this grid.")
_INVALID_BALLOT = (422, "invalid_ballot", "That ballot isn't valid.")


@contextmanager
def _refusals(invalid: tuple[int, str, str]) -> Iterator[None]:
    """The rules engine's refusals as API errors (§6.4)."""
    try:
        yield
    except match.MatchError as exc:
        raise ApiError(*_REFUSALS[type(exc)]) from None
    except ValueError:
        raise ApiError(*invalid) from None


class Lobby:
    def __init__(self, lex: Lexicon, settings: Settings, hub: Hub, rng: random.Random) -> None:
        self.lex = lex
        self.s = settings
        self.hub = hub
        self.rng = rng  # candidate ids and ballot order
        self.queue: dict[str, Waiting] = {}  # in join order
        self.fill_ends_at: datetime | None = None  # set exactly while 2 or more wait
        self.rooms: dict[str, Room] = {}
        self.playing: dict[str, Room] = {}  # user_id → room, from forming until the end
        self._jobs: list[PickJob | SaveJob] = []  # returned by the next tick

    # --- the queue (§4) ---

    def join(self, user_id: str, nickname: str, sort_key: str, now: datetime) -> dict:
        """Queue the player. Already queued: the same view. 409 in_match, 503 server_busy."""
        if user_id in self.playing:
            raise ApiError(409, "in_match", "You're already in a match.")
        if user_id not in self.queue:
            if len(self.queue) >= self.s.match_max_queue:
                raise ApiError(503, "server_busy", "Server busy, try again later.")
            self.queue[user_id] = Waiting(nickname, sort_key, now)
            self._queue_changed(now)
        return self.view(user_id, now)

    def leave(self, user_id: str, now: datetime) -> None:
        """Idempotent; does nothing outside the queue, so a match is never left."""
        if self.queue.pop(user_id, None) is not None:
            self.hub.close([user_id])
            self._queue_changed(now)

    def _queue_changed(self, now: datetime) -> None:
        if len(self.queue) < self.s.match_min_players:
            self.fill_ends_at = None
        elif self.fill_ends_at is None:
            self.fill_ends_at = now + timedelta(seconds=self.s.match_fill_s)
        self.hub.publish(self.queue, "queue", {"waiting": len(self.queue), "fill_ends_in_ms": self._fill_ms(now)})

    def _fill_ms(self, now: datetime) -> int | None:
        return None if self.fill_ends_at is None else ms_until(self.fill_ends_at, now)

    def _gone(self, user_id: str, since: datetime, now: datetime) -> bool:
        """No open stream for MATCH_STREAM_GONE_S, counted from `since` at the earliest."""
        if self.hub.connected(user_id):
            return False
        left = max(since, self.hub.gone_since(user_id) or since)
        return now - left > timedelta(seconds=self.s.match_stream_gone_s)

    def _tick_queue(self, now: datetime) -> None:
        gone = [uid for uid, w in self.queue.items() if self._gone(uid, w.joined_at, now)]
        changed = bool(gone)
        for uid in gone:
            del self.queue[uid]
        if changed:
            self._queue_changed(now)
        while len(self.queue) >= self.s.match_max_players or (self.fill_ends_at is not None and now >= self.fill_ends_at):
            group = list(self.queue)[: self.s.match_max_players]
            self._form({uid: self.queue.pop(uid) for uid in group})
            self.fill_ends_at = None  # whoever is left starts a new window
            self._queue_changed(now)
        if self.fill_ends_at is None:  # the timeout isn't checked while a window runs
            limit = timedelta(seconds=self.s.match_queue_timeout_s)
            timed_out = [uid for uid, w in self.queue.items() if now - w.joined_at >= limit]
            if timed_out:
                for uid in timed_out:
                    del self.queue[uid]
                self.hub.publish(timed_out, "queue_timeout", {})
                self._queue_changed(now)

    def _form(self, group: dict[str, Waiting]) -> None:
        if sum(r.state != "ended" for r in self.rooms.values()) >= self.s.match_max_active:
            self.hub.publish(group, "server_busy", {})
            return
        room = Room(str(uuid.uuid4()), group)
        self.rooms[room.id] = room
        for uid in group:
            self.playing[uid] = room
        self._jobs.append(PickJob(room.id))

    # --- what a player sees (§6.4) ---

    def view(self, user_id: str, now: datetime) -> dict:
        """The MatchView: only the caller's own words, display word and ballot; never a score."""
        w = self.queue.get(user_id)
        if w is not None:
            timeout = w.joined_at + timedelta(seconds=self.s.match_queue_timeout_s)
            queue = {"waiting": len(self.queue), "fill_ends_in_ms": self._fill_ms(now), "timeout_in_ms": ms_until(timeout, now)}
            return {"state": "queued", "queue": queue, "match": None}
        room = self.playing.get(user_id)
        if room is None:
            return {"state": "idle", "queue": None, "match": None}
        return {"state": room.state, "queue": None, "match": self._match_view(room, user_id, now)}

    def _match_view(self, room: Room, user_id: str, now: datetime) -> dict:
        view: dict = {
            "match_id": room.id, "players": [w.nickname for w in room.players.values()],
            "starts_in_ms": None, "ends_in_ms": None, "word1": None, "word2": None, "pct": None,
            "locked": [], "found": [], "display": None, "vote": None,
        }
        if room.state == "forming":
            return view
        m = _started(room)
        if room.state == "countdown":
            view["starts_in_ms"] = ms_until(m.starts_at, now)
            return view
        p = m.players[user_id]  # the board, from `start` on
        view.update(
            ends_in_ms=ms_until(m.ends_at, now), word1=m.word1, word2=m.word2, pct=match.pct_grid(m),
            locked=sorted([r, c] for r, c in p.locked), found=list(p.found),
            display=p.display.word if p.display else None,
        )
        if room.state == "voting":
            view["vote"] = {
                "ends_in_ms": ms_until(_vote_ends_at(m), now),
                "candidates": [{"id": c.id, "word": c.word} for c in room.ballots[user_id]],
                "voted": p.ballot is not None,
            }
        return view

    def connect(self, user_id: str, now: datetime) -> asyncio.Queue[Item] | None:
        """Open a stream: None when idle (→ 204), else a queue with the snapshot first (§6.2)."""
        view = self.view(user_id, now)
        if view["state"] == "idle":
            return None
        q = self.hub.subscribe(user_id)
        q.put_nowait(("snapshot", view))  # no await in between, so no event is lost or doubled
        return q

    # --- play (§3.2, §3.6, §3.7) ---

    def _room(self, user_id: str, match_id: str) -> Room:
        """The player's match. Someone else's looks exactly like a missing one (§7 M1)."""
        room = self.rooms.get(match_id)
        if room is None or user_id not in room.players:
            raise ApiError(404, "not_found", "There is no such match.")
        return room

    def submit(self, user_id: str, match_id: str, tiles: Sequence[Tile], now: datetime) -> match.SubmitOutcome:
        room = self._room(user_id, match_id)
        with _refusals(_INVALID_TILES):
            out = match.submit(_started(room), user_id, tiles, now)
        if out.status == "accepted":  # rejected and duplicate words publish nothing
            self._played(room, user_id, out.word, out.changes)
        return out

    def display(self, user_id: str, match_id: str, tiles: Sequence[Tile], now: datetime) -> match.DisplayOutcome:
        room = self._room(user_id, match_id)
        with _refusals(_INVALID_TILES):
            out = match.display(_started(room), user_id, tiles, now)
        self._played(room, user_id, out.word, out.changes)
        return out

    def _played(self, room: Room, user_id: str, word: str, changes: tuple[match.Change, ...]) -> None:
        """A display word publishes exactly what a real word does, so the two look the same."""
        self.hub.publish(room.players, "tiles", {"changes": [{"row": r, "col": c, "pct": p} for (r, c), p in changes]})
        others = [uid for uid in room.players if uid != user_id]
        self.hub.publish(others, "found", {"nickname": room.players[user_id].nickname, "letters": len(word)})

    def vote(self, user_id: str, match_id: str, ranking: Sequence[str], now: datetime) -> None:
        """One ballot; ends the vote at once if every player is now done."""
        room = self._room(user_id, match_id)
        with _refusals(_INVALID_BALLOT):
            if room.state != "voting":  # also once the vote ended early, inside its time window
                raise match.NotVoting()
            match.vote(_started(room), user_id, ranking, now)
        voted = sum(p.ballot is not None for p in _started(room).players.values())
        self.hub.publish(room.players, "voted", {"voted": voted, "of": len(room.players)})
        if self._all_done(room, now):
            self._end(room, now)

    def _all_done(self, room: Room, now: datetime) -> bool:
        """Every player has voted, or has had no stream for MATCH_STREAM_GONE_S since play closed."""
        m = _started(room)
        closed_at = m.closed_at or now  # always set while voting
        return all(p.ballot is not None or self._gone(uid, closed_at, now) for uid, p in m.players.items())

    # --- the ticker's side ---

    def tick(self, now: datetime) -> list[PickJob | SaveJob]:
        """Advance the queue and every match to `now`. One tick can catch up several steps."""
        self._tick_queue(now)
        for room in list(self.rooms.values()):
            self._tick_room(room, now)
        jobs, self._jobs = self._jobs, []
        return jobs

    def _tick_room(self, room: Room, now: datetime) -> None:
        m = room.m
        if room.state == "ended":
            if room.keep_until is not None and now >= room.keep_until:
                del self.rooms[room.id]
            return
        if m is None:  # forming: waiting for the pick
            return
        if room.state == "countdown" and now >= m.starts_at:
            room.state = "running"
            self.hub.publish(room.players, "start", {"word1": m.word1, "word2": m.word2, "ends_in_ms": ms_until(m.ends_at, now)})
        if room.state == "running" and not room.warned and now >= m.ends_at - timedelta(seconds=match.WARNING_S):
            room.warned = True
            self.hub.publish(room.players, "warning", {"ends_in_ms": ms_until(m.ends_at, now)})
        if room.state == "running" and now > m.ends_at + match.GRACE:
            if not match.close_play(m, now, self.rng):
                self._end(room, now)
                return
            room.state = "voting"
            for uid in room.players:
                room.ballots[uid] = match.ballot_for(m, uid, self.rng)
                candidates = [{"id": c.id, "word": c.word} for c in room.ballots[uid]]
                self.hub.publish([uid], "voting", {"ends_in_ms": ms_until(_vote_ends_at(m), now), "candidates": candidates})
        if room.state == "voting" and (now > _vote_ends_at(m) + match.GRACE or self._all_done(room, now)):
            self._end(room, now)

    def _end(self, room: Room, now: datetime) -> None:
        """Results, then everyone is idle at once; the save follows through the next tick's jobs."""
        rec = match.finish(_started(room), now)
        room.results = match.results(rec)
        room.state = "ended"
        for uid in room.players:
            del self.playing[uid]
        self.hub.publish(room.players, "end", public_results(room.results))
        self._jobs.append(SaveJob(rec))

    def pair_picked(self, match_id: str, pair: Pair, now: datetime) -> None:
        """forming → countdown. T = now; play starts COUNTDOWN_S later (§3.1)."""
        room = self.rooms[match_id]
        word1, word2, answers = pair
        players = [(uid, w.nickname, w.sort_key) for uid, w in room.players.items()]
        starts_at = now + timedelta(seconds=match.COUNTDOWN_S)
        room.m = match.new_match(match_id, word1, word2, answers, players, starts_at, self.lex.max_tiles)
        room.state = "countdown"
        found = {"match_id": match_id, "players": [w.nickname for w in room.players.values()], "starts_in_ms": ms_until(starts_at, now)}
        self.hub.publish(room.players, "match_found", found)

    def pick_failed(self, match_id: str, now: datetime) -> None:
        """forming → idle: no board qualifies."""
        room = self.rooms.pop(match_id)
        for uid in room.players:
            del self.playing[uid]
        self.hub.publish(room.players, "match_cancelled", {})

    def saved(self, match_id: str, ok: bool, now: datetime) -> None:
        """Saved: drop it, so results come from the DB. Failed: keep the results in memory for 10 min."""
        if ok:
            del self.rooms[match_id]
        else:
            self.rooms[match_id].keep_until = now + KEEP_FAILED

    def results(self, match_id: str, user_id: str) -> dict | None:
        """In memory from the end until saved, with user_ids. None (→ the DB, else 404) otherwise,
        and for anyone who wasn't a player."""
        room = self.rooms.get(match_id)
        if room is None or room.results is None or user_id not in room.players:
            return None
        return room.results


async def run(
    lobby: Lobby,
    clock: Clock,
    pick: Callable[[], Pair],
    save: Callable[[match.MatchRecord], None],
    stop: asyncio.Event,
    tick_s: float,
) -> None:
    """The ticker (§6.3): tick every tick_s, run each job in a thread, feed its result back.

    A failing tick or job is logged and never stops the loop. On stop, one last tick collects the
    saves of votes that ended since the previous one, and pending saves get up to SAVE_WAIT_S; a
    match that hasn't ended is lost, as on any restart (§6.6).
    """
    tasks: set[asyncio.Task] = set()  # strong references, so the loop can't drop a running job
    saves: set[asyncio.Task] = set()

    async def picking(job: PickJob) -> None:
        try:
            pair = await asyncio.to_thread(pick)
        except Exception as exc:
            if not isinstance(exc, LookupError):  # LookupError: no board qualifies, which is expected
                log.exception("picking a board for match %s failed", job.match_id)
            lobby.pick_failed(job.match_id, clock.now())
        else:
            lobby.pair_picked(job.match_id, pair, clock.now())

    async def saving(job: SaveJob) -> None:
        try:
            await asyncio.to_thread(save, job.record)
        except Exception:
            log.exception("saving match %s failed; its results stay in memory for 10 min", job.record.id)
            lobby.saved(job.record.id, False, clock.now())
        else:
            lobby.saved(job.record.id, True, clock.now())

    while True:
        try:
            jobs = lobby.tick(clock.now())
        except Exception:
            log.exception("lobby tick failed")
            jobs = []
        for job in jobs:
            if isinstance(job, PickJob):
                task = asyncio.create_task(picking(job))
            else:
                task = asyncio.create_task(saving(job))
                saves.add(task)
                task.add_done_callback(saves.discard)
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), tick_s)
        except TimeoutError:
            pass
    if saves:
        await asyncio.wait(saves, timeout=SAVE_WAIT_S)
