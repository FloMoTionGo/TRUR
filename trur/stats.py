"""A player's own daily record: played, won, streaks, best score, longest word (tasks/todo.md B2).

Read straight from `games` and `submissions`, which are kept for good; only `events` is purged, so no
summary table is needed. One daily game per player and day (the UNIQUE (user_id, daily_date) index, which
also serves the query). Days are UTC dates, as the daily challenge's are. The caller passes today's date:
this module never reads a clock. Stdlib only.

A flagged game still counts as played, won and for the streak: this is the player's own record, not a
board. Its score and words are left out of the best score and the longest word, as the boards leave
them out. Hidden players see their own numbers like anyone else.
"""

import sqlite3
from collections.abc import Collection
from datetime import date, timedelta
from typing import TypedDict

RECENT_DAYS = 30


class Day(TypedDict):
    date: str
    played: bool
    won: bool


class Stats(TypedDict):
    played: int
    won: int
    current_streak: int
    best_streak: int
    best_score: int
    longest_word: str | None
    days: list[Day]  # the last RECENT_DAYS days, oldest first, today last


_GAMES = (
    "SELECT g.daily_date, g.score, g.won, g.flagged,"
    " (SELECT COUNT(*) FROM submissions s WHERE s.game_id = g.id AND s.is_valid = 1) AS found"
    " FROM games g WHERE g.user_id = ? AND g.mode = 'daily' ORDER BY g.daily_date"
)

# the longest word, the earliest in the alphabet among equals, so the answer never flips between reads
_LONGEST = (
    "SELECT s.word FROM submissions s JOIN games g ON g.id = s.game_id"
    " WHERE g.user_id = ? AND g.mode = 'daily' AND g.flagged = 0 AND s.is_valid = 1"
    " ORDER BY length(s.word) DESC, s.word LIMIT 1"
)


def counts_as_played(found: int) -> bool:
    """The streak rule (owner, 2026-11-28): a day counts once at least one word is found.

    Starting the daily isn't enough, and a win isn't needed: a win-only streak would break on hard pairs.
    """
    return found >= 1


def current_streak(played: Collection[date], today: date) -> int:
    """Days in a row up to today. A today not played yet leaves yesterday's streak standing."""
    day = today if today in played else today - timedelta(days=1)
    run = 0
    while day in played:
        run += 1
        day -= timedelta(days=1)
    return run


def best_streak(played: Collection[date]) -> int:
    best = run = 0
    previous: date | None = None
    for day in sorted(played):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def daily_stats(conn_ro: sqlite3.Connection, user_id: str, today: date) -> Stats:
    played: set[date] = set()
    won: set[date] = set()
    best_score = 0
    for row in conn_ro.execute(_GAMES, (user_id,)):
        if not counts_as_played(row["found"]):
            continue
        day = date.fromisoformat(row["daily_date"])
        played.add(day)
        if row["won"]:
            won.add(day)
        if not row["flagged"]:
            best_score = max(best_score, row["score"])
    longest = conn_ro.execute(_LONGEST, (user_id,)).fetchone()
    recent = [today - timedelta(days=back) for back in range(RECENT_DAYS - 1, -1, -1)]
    return {
        "played": len(played),
        "won": len(won),
        "current_streak": current_streak(played, today),
        "best_streak": best_streak(played),
        "best_score": best_score,
        "longest_word": longest["word"] if longest else None,
        "days": [{"date": d.isoformat(), "played": d in played, "won": d in won} for d in recent],
    }
