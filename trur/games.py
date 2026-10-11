"""The game service: create, resume and end games (docs/architecture.md §2). Stdlib only.

The server is authoritative: it picks the grid, rebuilds words from tile coordinates and keeps
the score. Answers never leave the server for an open game (S12); `missed_words` is filled in
only once a free game is over.

Seams for later tasks, each marked where it goes:
- T17: the 1-second submit gap (S4), checked in memory before the submit transaction
- T18: the server-side event log (game_started, word_*, game_ended)
- T21: daily games, and keeping today's daily words out of free play
- T25: the free-games-per-day limit
- T26: the plausibility flag and the ranking recompute in _end
"""

import random
import sqlite3
import uuid
from datetime import datetime
from typing import Literal, TypedDict

from trur import db
from trur.config import Settings
from trur.errors import ApiError
from trur.solver import MAX_TRIES, Lexicon, Tile, is_win_word, pick_pair, points, word_from_tiles

Mode = Literal["free", "daily"]
EndReason = Literal["player", "tries", "all_found", "replaced", "day_closed"]


class FoundWord(TypedDict):
    word: str
    points: int


class GameState(TypedDict):
    game_id: str
    mode: Mode
    daily_date: str | None
    word1: str  # columns
    word2: str  # rows
    tries_left: int
    total_words: int
    found: list[FoundWord]  # submission order
    found_count: int
    score: int
    won: bool
    game_over: bool
    missed_words: list[str] | None  # free play only, once the game is over


class SubmitResult(TypedDict):
    status: Literal["accepted", "duplicate", "rejected"]
    word: str
    points: int  # 0 unless accepted
    score: int
    tries_left: int
    found_count: int
    total_words: int
    won: bool
    game_over: bool
    missed_words: list[str] | None


def create_game(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, rng: random.Random, user_id: str, mode: Mode, now: datetime
) -> tuple[GameState, bool]:
    """Start a new free game, ending the player's open one as `replaced`. The bool is True when a game was created."""
    if mode != "free":
        raise NotImplementedError("daily games come with T21")
    sweep_abandoned(conn, lex, s, now, user_id)
    # T21: ensure_daily_pair(today), and the accept rule also refuses today's daily words.
    try:  # the picker runs outside the transaction: it can take a while and needs no lock
        word1, word2, answers = pick_pair(lex, rng, lambda w1, w2, found: len(found) >= s.min_free_words)
    except LookupError:
        raise ApiError(503, "no_pair_available", "No word pair is available right now. Please try again.") from None
    game_id = str(uuid.uuid4())
    with db.tx(conn):
        # T25: the free-games-per-day limit is checked here, before the open game is touched.
        open_game = conn.execute(
            "SELECT * FROM games WHERE user_id = ? AND mode = 'free' AND ended_at IS NULL", (user_id,)
        ).fetchone()
        if open_game is not None:
            _end(conn, lex, s, open_game, "replaced", now)
        conn.execute(
            "INSERT INTO games (id, user_id, mode, word1, word2, total_words, started_at) "
            "VALUES (?, ?, 'free', ?, ?, ?, ?)",
            (game_id, user_id, word1, word2, len(answers), db.iso(now)),
        )
        # T18: log game_started.
        game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return _state(conn, lex, game), True


def current_game(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, user_id: str, mode: Mode, now: datetime
) -> GameState | None:
    """The player's open game in this mode, so a reload can resume it."""
    sweep_abandoned(conn, lex, s, now, user_id)  # a game from a past day is closed, not resumed
    game = conn.execute(
        "SELECT * FROM games WHERE user_id = ? AND mode = ? AND ended_at IS NULL", (user_id, mode)
    ).fetchone()
    return None if game is None else _state(conn, lex, game)


def submit(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, user_id: str, game_id: str, tiles: list[Tile], now: datetime
) -> SubmitResult:
    """Check one word, all in one IMMEDIATE transaction, so parallel submits can't score twice or beat the 3 tries (S5).

    `day_closed` and `game_over` are raised after COMMIT, so a game closed by the day check stays closed.
    """
    closed = None
    with db.tx(conn):
        game = _load_owned(conn, user_id, game_id)
        if _day_over(game, now):
            _end(conn, lex, s, game, "day_closed", now)
            closed = ApiError(409, "day_closed", "This game's day is over, so the game has been closed.")
        elif game["ended_at"] is not None:
            closed = ApiError(409, "game_over", "This game is over.")
        else:
            result = _check_word(conn, lex, s, game, tiles, now)
    if closed is not None:
        raise closed
    return result


def end_by_player(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, user_id: str, game_id: str, now: datetime
) -> GameState:
    """The player presses End game. Same order of checks as submit: ownership, the day, already over."""
    closed = None
    with db.tx(conn):
        game = _load_owned(conn, user_id, game_id)
        if _day_over(game, now):
            _end(conn, lex, s, game, "day_closed", now)
            closed = ApiError(409, "day_closed", "This game's day is over, so the game has been closed.")
        elif game["ended_at"] is not None:
            closed = ApiError(409, "game_over", "This game is over.")
        else:
            _end(conn, lex, s, game, "player", now)
            state = _state(conn, lex, conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())
    if closed is not None:
        raise closed
    return state


def sweep_abandoned(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, now: datetime, user_id: str | None = None
) -> int:
    """Close open games whose UTC day is over, with their current score (reason day_closed).

    There's no timer, because the machine stops when idle: this runs at startup, before a board
    is served, and whenever a request touches a player's games. Returns how many were closed.
    """
    today = now.date().isoformat()
    sql = (
        "SELECT * FROM games WHERE ended_at IS NULL"
        " AND ((mode = 'daily' AND daily_date < ?) OR (mode = 'free' AND substr(started_at, 1, 10) < ?))"
    )
    params: tuple = (today, today)
    if user_id is not None:
        sql += " AND user_id = ?"
        params += (user_id,)
    with db.tx(conn):
        stale = conn.execute(sql, params).fetchall()
        for game in stale:
            _end(conn, lex, s, game, "day_closed", now)
    return len(stale)


# --- private helpers ---

def _load_owned(conn: sqlite3.Connection, user_id: str, game_id: str) -> sqlite3.Row:
    """The game, if it belongs to this player. Someone else's game looks exactly like a missing one (S2)."""
    game = conn.execute("SELECT * FROM games WHERE id = ? AND user_id = ?", (game_id, user_id)).fetchone()
    if game is None:
        raise ApiError(404, "not_found", "There is no such game.")
    return game


def _day_over(game: sqlite3.Row, now: datetime) -> bool:
    """A game belongs to its UTC day (daily: daily_date; free: the started_at date) and closes at midnight."""
    return (game["daily_date"] or game["started_at"][:10]) < now.date().isoformat()


def _check_word(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, game: sqlite3.Row, tiles: list[Tile], now: datetime
) -> SubmitResult:
    try:
        word = word_from_tiles(game["word1"], game["word2"], tiles, lex.max_tiles)
    except ValueError:
        raise ApiError(422, "invalid_tiles", "Those tiles aren't a valid selection on this grid.") from None
    month = (game["daily_date"] or game["started_at"])[:7]  # the game's month decides its board
    found_before = conn.execute(
        "SELECT 1 FROM submissions WHERE game_id = ? AND word = ? AND is_valid = 1", (game["id"], word)
    ).fetchone()
    gained = 0
    if found_before is not None:
        status = "duplicate"  # no row, no try used
    elif word in lex.words:
        status, gained = "accepted", points(word)
        conn.execute(
            "INSERT INTO submissions (user_id, game_id, game_mode, word, is_valid, points, submitted_at, month) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (game["user_id"], game["id"], game["mode"], word, gained, db.iso(now), month),
        )
        won = game["mode"] == "daily" and is_win_word(word)
        conn.execute("UPDATE games SET score = score + ?, won = MAX(won, ?) WHERE id = ?", (gained, int(won), game["id"]))
        found = conn.execute("SELECT COUNT(*) FROM submissions WHERE game_id = ? AND is_valid = 1", (game["id"],)).fetchone()[0]
        if found >= game["total_words"]:
            _end(conn, lex, s, game, "all_found", now)
    else:
        status = "rejected"
        conn.execute(
            "INSERT INTO submissions (user_id, game_id, game_mode, word, is_valid, points, submitted_at, month) "
            "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
            (game["user_id"], game["id"], game["mode"], word, db.iso(now), month),
        )
        conn.execute("UPDATE games SET wrong_count = wrong_count + 1 WHERE id = ?", (game["id"],))
        if game["wrong_count"] + 1 >= MAX_TRIES:
            _end(conn, lex, s, game, "tries", now)
    # T18: log word_accepted / word_duplicate / word_rejected.
    state = _state(conn, lex, conn.execute("SELECT * FROM games WHERE id = ?", (game["id"],)).fetchone())
    return {
        "status": status,
        "word": word,
        "points": gained,
        "score": state["score"],
        "tries_left": state["tries_left"],
        "found_count": state["found_count"],
        "total_words": state["total_words"],
        "won": state["won"],
        "game_over": state["game_over"],
        "missed_words": state["missed_words"],
    }


def _end(conn: sqlite3.Connection, lex: Lexicon, s: Settings, game: sqlite3.Row, reason: EndReason, now: datetime) -> None:
    """Close a game with its current score. Runs inside the caller's transaction."""
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ? AND ended_at IS NULL", (db.iso(now), game["id"]))
    # T26: set `flagged` with is_implausible, then recompute_ranking for each of the game's periods.
    # T18: log game_ended with {reason, score, flagged}.


def _state(conn: sqlite3.Connection, lex: Lexicon, game: sqlite3.Row) -> GameState:
    found: list[FoundWord] = [
        {"word": r["word"], "points": r["points"]}
        for r in conn.execute(
            "SELECT word, points FROM submissions WHERE game_id = ? AND is_valid = 1 ORDER BY id", (game["id"],)
        )
    ]
    over = game["ended_at"] is not None
    return {
        "game_id": game["id"],
        "mode": game["mode"],
        "daily_date": game["daily_date"],
        "word1": game["word1"],
        "word2": game["word2"],
        "tries_left": MAX_TRIES - game["wrong_count"],
        "total_words": game["total_words"],  # stored at creation, so a dictionary change can't shift it
        "found": found,
        "found_count": len(found),
        "score": game["score"],
        "won": bool(game["won"]),
        "game_over": over,
        "missed_words": _missed(lex, game, found) if over and game["mode"] == "free" else None,
    }


def _missed(lex: Lexicon, game: sqlite3.Row, found: list[FoundWord]) -> list[str]:
    """The answers not found, by points then A–Z."""
    got = {f["word"] for f in found}
    return sorted((w for w in lex.solve(game["word1"], game["word2"]) if w not in got), key=lambda w: (-points(w), w))
