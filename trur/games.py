"""The game service: create, resume and end games (docs/architecture.md §2). Stdlib only.

The server is authoritative: it picks the grid, rebuilds words from tile coordinates and keeps
the score. Answers never leave the server for an open game (S12); `missed_words` is filled in
only once a free game is over.

Seams for later tasks, each marked where it goes:
- T14: sweep_abandoned before create/current, end_by_player
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
from trur.solver import MAX_TRIES, Lexicon, pick_pair, points

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


def create_game(
    conn: sqlite3.Connection, lex: Lexicon, s: Settings, rng: random.Random, user_id: str, mode: Mode, now: datetime
) -> tuple[GameState, bool]:
    """Start a new free game, ending the player's open one as `replaced`. The bool is True when a game was created."""
    if mode != "free":
        raise NotImplementedError("daily games come with T21")
    # T14: sweep_abandoned(conn, lex, s, now, user_id) runs first.
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
    # T14: sweep_abandoned(conn, lex, s, now, user_id) first, so a game from a past day is closed, not resumed.
    game = conn.execute(
        "SELECT * FROM games WHERE user_id = ? AND mode = ? AND ended_at IS NULL", (user_id, mode)
    ).fetchone()
    return None if game is None else _state(conn, lex, game)


# --- private helpers ---

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
