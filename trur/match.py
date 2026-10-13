"""Multiplayer rules engine (docs/multiplayer.md §3, §6.3).

Stdlib only, no I/O and no clock reads: every time and every random source is passed in, so the
lobby, the tests and the sandbox drive it the same way. A match is one shared grid; each accepted
word (or display word) halves its tiles for everyone and locks them for its player.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, NamedTuple

from trur.solver import Tile, points, word_from_tiles

# Decided rules, not settings (§6.7).
COUNTDOWN_S = 3
DURATION_S = 60
WARNING_S = 10
VOTE_S = 60
GRACE = timedelta(milliseconds=300)
VOTE_POINTS = (5, 3, 1)
BONUS_SCALE = 1


class MatchError(Exception):
    """Base for the rule refusals the API maps to 409s."""


class NotRunning(MatchError):
    pass


class TileLocked(MatchError):
    pass


class NotVoting(MatchError):
    pass


class AlreadyVoted(MatchError):
    pass


@dataclass(frozen=True)
class FoundWord:
    word: str
    tiles: tuple[Tile, ...]
    points: int
    at: datetime


@dataclass(frozen=True)
class DisplayWord:
    word: str
    tiles: tuple[Tile, ...]
    at: datetime


@dataclass(frozen=True)
class MatchEvent:
    """One submit, display word or ballot, in order, for saving (§6.6)."""
    kind: Literal["word", "display", "vote"]
    user_id: str
    at: datetime
    status: Literal["accepted", "duplicate", "rejected", "displayed", "voted"]
    word: str = ""
    tiles: tuple[Tile, ...] = ()
    points: int = 0
    targets: tuple[str, ...] = ()  # vote only: user_ids, best first


class Change(NamedTuple):
    tile: Tile
    pct: int


class SubmitOutcome(NamedTuple):
    status: Literal["accepted", "duplicate", "rejected"]
    word: str
    points: int  # 0 unless accepted; never sent to the browser during play
    changes: tuple[Change, ...]  # empty unless accepted


class DisplayOutcome(NamedTuple):
    word: str
    changes: tuple[Change, ...]


class Candidate(NamedTuple):
    id: str
    word: str


@dataclass
class PlayerState:
    nickname: str
    sort_key: str  # nickname_key; orders tied places
    locked: set[Tile] = field(default_factory=set)
    found: dict[str, FoundWord] = field(default_factory=dict)
    display: DisplayWord | None = None
    ballot: tuple[str, ...] | None = None  # candidate ids, best first; None = not voted


@dataclass
class Match:
    id: str
    word1: str
    word2: str
    answers: frozenset[str]
    players: dict[str, PlayerState]  # user_id → state; fixed when the match forms
    starts_at: datetime
    max_tiles: int | None = None
    uses: dict[Tile, int] = field(default_factory=dict)  # missing = 0
    closed_at: datetime | None = None
    voting: bool = False
    candidates: dict[str, str] = field(default_factory=dict)  # candidate id → user_id
    log: list[MatchEvent] = field(default_factory=list)

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(seconds=DURATION_S)

    @property
    def vote_ends_at(self) -> datetime | None:
        return None if self.closed_at is None else self.closed_at + timedelta(seconds=VOTE_S)


def new_match(
    match_id: str,
    word1: str,
    word2: str,
    answers: Sequence[str],
    players: Sequence[tuple[str, str, str]],
    starts_at: datetime,
    max_tiles: int | None = None,
) -> Match:
    """players: (user_id, nickname, nickname_key) in join order."""
    return Match(
        id=match_id,
        word1=word1,
        word2=word2,
        answers=frozenset(answers),
        players={uid: PlayerState(nick, key) for uid, nick, key in players},
        starts_at=starts_at,
        max_tiles=max_tiles,
    )


# --- scoring (§3.3): whole-number maths, rounding half up ---

def pct(uses: int) -> int:
    """max(1, round_half_up(100 / 2**uses)): 100, 50, 25, 13, 6, 3, 2, 1, 1, …"""
    if uses >= 7:
        return 1
    d = 1 << uses
    return max(1, (200 + d) // (2 * d))


def word_points(tile_uses: Sequence[int]) -> int:
    """tiles × Σpct ÷ 100, rounded half up, at least 1; taken before the halving."""
    return max(1, (len(tile_uses) * sum(pct(u) for u in tile_uses) + 50) // 100)


def bonus(received: int, ballots: int) -> int:
    """round_half_up(BONUS_SCALE × received ÷ ballots); 0 without ballots (§3.7)."""
    if ballots == 0:
        return 0
    return (2 * BONUS_SCALE * received + ballots) // (2 * ballots)


def ms_until(when: datetime, now: datetime) -> int:
    """Times sent to the browser are relative and never negative."""
    return max(0, int((when - now) / timedelta(milliseconds=1)))


def pct_grid(m: Match) -> list[list[int]]:
    """[row][col] of the current percentages."""
    return [[pct(m.uses.get((r, c), 0)) for c in range(len(m.word1))] for r in range(len(m.word2))]


# --- play ---

def is_running(m: Match, now: datetime) -> bool:
    return m.closed_at is None and m.starts_at <= now <= m.ends_at + GRACE


def _player(m: Match, user_id: str) -> PlayerState:
    return m.players[user_id]  # KeyError: the lobby checks membership first (→ 404)


def _use(m: Match, p: PlayerState, tiles: tuple[Tile, ...]) -> tuple[Change, ...]:
    p.locked.update(tiles)
    for t in tiles:
        m.uses[t] = m.uses.get(t, 0) + 1
    return tuple(Change(t, pct(m.uses[t])) for t in tiles)


def _check_play(m: Match, user_id: str, tiles: Sequence[Tile], now: datetime) -> tuple[PlayerState, str, tuple[Tile, ...]]:
    p = _player(m, user_id)
    if not is_running(m, now):
        raise NotRunning()
    word = word_from_tiles(m.word1, m.word2, tiles, m.max_tiles)  # ValueError: invalid tiles
    return p, word, tuple((r, c) for r, c in tiles)


def submit(m: Match, user_id: str, tiles: Sequence[Tile], now: datetime) -> SubmitOutcome:
    """Order: running · tiles · already found (→ duplicate) · locked · in answers."""
    p, word, tiles = _check_play(m, user_id, tiles, now)
    if word in p.found:
        m.log.append(MatchEvent("word", user_id, now, "duplicate", word, tiles))
        return SubmitOutcome("duplicate", word, 0, ())
    if any(t in p.locked for t in tiles):
        raise TileLocked()
    if word not in m.answers:
        m.log.append(MatchEvent("word", user_id, now, "rejected", word, tiles))
        return SubmitOutcome("rejected", word, 0, ())
    score = word_points([m.uses.get(t, 0) for t in tiles])
    p.found[word] = FoundWord(word, tiles, score, now)
    m.log.append(MatchEvent("word", user_id, now, "accepted", word, tiles, score))
    return SubmitOutcome("accepted", word, score, _use(m, p, tiles))


def display(m: Match, user_id: str, tiles: Sequence[Tile], now: datetime) -> DisplayOutcome:
    """No dictionary or duplicate check and no points; otherwise like an accepted word (§3.6)."""
    p, word, tiles = _check_play(m, user_id, tiles, now)
    if any(t in p.locked for t in tiles):
        raise TileLocked()
    p.display = DisplayWord(word, tiles, now)
    m.log.append(MatchEvent("display", user_id, now, "displayed", word, tiles))
    return DisplayOutcome(word, _use(m, p, tiles))


# --- the creativity vote (§3.7) ---

def close_play(m: Match, now: datetime, rng: random.Random) -> bool:
    """Close play and draw candidate ids. False = fewer than two display words: no vote."""
    m.closed_at = now
    authors = [uid for uid, p in m.players.items() if p.display is not None]
    ids = [f"c{i}" for i in range(1, len(authors) + 1)]
    rng.shuffle(ids)
    m.candidates = dict(zip(ids, authors))
    m.voting = len(authors) >= 2
    return m.voting


def ballot_for(m: Match, user_id: str, rng: random.Random) -> list[Candidate]:
    """The other players' display words, without names, shuffled for this voter."""
    ballot = [
        Candidate(cid, m.players[uid].display.word)  # type: ignore[union-attr]  # candidates all have one
        for cid, uid in sorted(m.candidates.items())
        if uid != user_id
    ]
    rng.shuffle(ballot)
    return ballot


def is_voting(m: Match, now: datetime) -> bool:
    return m.voting and m.closed_at is not None and m.closed_at < now <= m.vote_ends_at + GRACE  # type: ignore[operator]


def vote(m: Match, user_id: str, ranking: Sequence[str], now: datetime) -> None:
    p = _player(m, user_id)
    if not is_voting(m, now):
        raise NotVoting()
    if p.ballot is not None:
        raise AlreadyVoted()
    if not 1 <= len(ranking) <= len(VOTE_POINTS):
        raise ValueError("a ballot ranks 1 to 3 words")
    if len(set(ranking)) != len(ranking):
        raise ValueError("a word is ranked twice")
    for cid in ranking:
        if m.candidates.get(cid, user_id) == user_id:
            raise ValueError("unknown candidate, or your own word")
    p.ballot = tuple(ranking)
    m.log.append(MatchEvent("vote", user_id, now, "voted", targets=tuple(m.candidates[c] for c in ranking)))


# --- the end ---

@dataclass(frozen=True)
class PlayerRecord:
    user_id: str
    nickname: str
    sort_key: str
    words: tuple[FoundWord, ...]  # in the order found
    display: DisplayWord | None  # the last one
    votes_cast: tuple[str, ...]  # target user_ids, best first; empty if not voted


@dataclass(frozen=True)
class MatchRecord:
    """A frozen copy of a finished match, for saving and for the results (§6.6)."""
    id: str
    word1: str
    word2: str
    answers: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    players: tuple[PlayerRecord, ...]
    uses: tuple[tuple[Tile, int], ...]
    log: tuple[MatchEvent, ...]


def finish(m: Match, now: datetime) -> MatchRecord:
    return MatchRecord(
        id=m.id,
        word1=m.word1,
        word2=m.word2,
        answers=tuple(sorted(m.answers)),
        started_at=m.starts_at,
        ended_at=now,
        players=tuple(
            PlayerRecord(
                uid,
                p.nickname,
                p.sort_key,
                tuple(p.found.values()),
                p.display,
                tuple(m.candidates[c] for c in p.ballot or ()),
            )
            for uid, p in m.players.items()
        ),
        uses=tuple(sorted(m.uses.items())),
        log=tuple(m.log),
    )


def results(rec: MatchRecord) -> dict:
    """The results as JSON-ready data, with user_ids (the API drops them; the snapshot keeps them)."""
    received: dict[str, int] = {}
    for p in rec.players:
        for i, target in enumerate(p.votes_cast):
            received[target] = received.get(target, 0) + VOTE_POINTS[i]
    ballots = [p.user_id for p in rec.players if p.votes_cast]
    rows = []
    for p in rec.players:
        word_score = sum(w.points for w in p.words)
        disp = None
        vote_bonus = 0
        if p.display is not None:
            got = received.get(p.user_id, 0)
            vote_bonus = bonus(got, sum(1 for v in ballots if v != p.user_id))
            disp = {"word": p.display.word, "votes": got, "bonus": vote_bonus}
        rows.append({
            "user_id": p.user_id,
            "nickname": p.nickname,
            "sort_key": p.sort_key,
            "score": word_score + vote_bonus,
            "word_score": word_score,
            "display": disp,
            "words": [{"word": w.word, "points": w.points} for w in p.words],
        })
    rows.sort(key=lambda r: (-r["score"], r["sort_key"]))
    for i, r in enumerate(rows):
        r["place"] = rows[i - 1]["place"] if i and rows[i - 1]["score"] == r["score"] else i + 1  # RANK(): 1, 1, 3
        del r["sort_key"]
    found = {w.word for p in rec.players for w in p.words}
    uses = dict(rec.uses)
    return {
        "match_id": rec.id,
        "word1": rec.word1,
        "word2": rec.word2,
        "total_words": len(rec.answers),
        "players": rows,
        "missed_words": sorted((w for w in rec.answers if w not in found), key=lambda w: (-points(w), w)),
        "pct": [[pct(uses.get((r, c), 0)) for c in range(len(rec.word1))] for r in range(len(rec.word2))],
    }
