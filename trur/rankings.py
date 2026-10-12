"""Rankings: the best-score cache and the board queries (docs/architecture.md §2, §4; plan: Rankings).

`rankings` holds each player's best ended, unflagged score per board period. It's a cache rebuilt
from `games` whenever a game ends or an admin flags or unflags one, so a cleared flag shows at once.
Rank is worked out when a board is read, never stored, and hidden players are filtered out first,
so they don't take up a place. Stdlib only.
"""

import sqlite3
from collections.abc import Mapping
from datetime import date
from typing import TypedDict


class BoardRow(TypedDict):
    rank: int
    nickname: str
    score: int


def periods(game: Mapping) -> list[str]:
    """The boards a game counts on: free play its started_at month; daily its date's month and the date."""
    if game["mode"] == "daily":
        return [game["daily_date"][:7], game["daily_date"]]
    return [game["started_at"][:7]]


# A period is 'YYYY-MM' (monthly board) or 'YYYY-MM-DD' (daily board); neither form can match the other.
_BEST = (
    "SELECT MAX(score) FROM games"
    " WHERE user_id = ? AND mode = ? AND ended_at IS NOT NULL AND flagged = 0"
    " AND (daily_date = ? OR substr(COALESCE(daily_date, started_at), 1, 7) = ?)"
)

_BOARD = (
    "SELECT RANK() OVER (ORDER BY r.best_score DESC) AS rank, u.nickname, r.best_score AS score"
    " FROM rankings r JOIN users u ON u.id = r.user_id"
    " WHERE r.game_mode = ? AND r.period = ? AND u.hidden = 0"
    " ORDER BY score DESC, u.nickname_key LIMIT ?"
)


def recompute_ranking(conn: sqlite3.Connection, user_id: str, mode: str, period: str) -> None:
    """Rebuild one player's row for one board from `games`. Runs inside the caller's transaction."""
    (best,) = conn.execute(_BEST, (user_id, mode, period, period)).fetchone()
    if best is None:
        conn.execute("DELETE FROM rankings WHERE user_id = ? AND game_mode = ? AND period = ?", (user_id, mode, period))
    else:
        conn.execute(
            "INSERT INTO rankings (user_id, game_mode, period, best_score) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (user_id, game_mode, period) DO UPDATE SET best_score = excluded.best_score",
            (user_id, mode, period, best),
        )


def monthly_board(conn_ro: sqlite3.Connection, mode: str, month: str, limit: int) -> list[BoardRow]:
    return _board(conn_ro, mode, month, limit)


def daily_board(conn_ro: sqlite3.Connection, day: date, limit: int) -> list[BoardRow]:
    return _board(conn_ro, "daily", day.isoformat(), limit)


def _board(conn_ro: sqlite3.Connection, mode: str, period: str, limit: int) -> list[BoardRow]:
    """Ties share a rank (1, 1, 3) and are listed by nickname (§8 item 12)."""
    return [
        {"rank": r["rank"], "nickname": r["nickname"], "score": r["score"]}
        for r in conn_ro.execute(_BOARD, (mode, period, limit))
    ]
