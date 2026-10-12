"""Admin tasks on the live database. There is no admin endpoint on the internet (S4, S11).

    python -m scripts.admin [--db PATH] hide|unhide|delete-player <nickname>
    python -m scripts.admin [--db PATH] flag|unflag <game_id>

On Fly: fly ssh console -C "python -m scripts.admin unflag <game_id>". The database defaults to
DATA_DIR/trur.db from the environment, as in the app. Nicknames are matched like registration
(folded), so "ALICE" finds Alice.

- hide / unhide: the player disappears from, or comes back to, every board on the next request.
- flag / unflag: an ended game leaves, or rejoins, the boards; its rankings are rebuilt at once.
- delete-player: the player and all their games, submissions, events and rankings (S13).

Stdlib only. Exit 1, with nothing changed, when the player or game doesn't exist.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from trur import db, rankings
from trur.auth import fold_nickname
from trur.config import load_settings


class AdminError(Exception):
    pass


def _player(conn: sqlite3.Connection, nickname: str) -> sqlite3.Row:
    user = conn.execute(
        "SELECT id, nickname FROM users WHERE nickname_key = ?", (fold_nickname(nickname),)
    ).fetchone()
    if user is None:
        raise AdminError(f"no such player: {nickname!r}")
    return user


def set_hidden(conn: sqlite3.Connection, nickname: str, hidden: bool) -> str:
    with db.tx(conn):
        user = _player(conn, nickname)
        conn.execute("UPDATE users SET hidden = ? WHERE id = ?", (int(hidden), user["id"]))
    return f"{'hid' if hidden else 'unhid'} {user['nickname']}"


def set_flagged(conn: sqlite3.Connection, game_id: str, flagged: bool) -> str:
    """Only ended games: _end sets the flag from the plausibility check and would overwrite it."""
    with db.tx(conn):
        game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if game is None:
            raise AdminError(f"no such game: {game_id!r}")
        if game["ended_at"] is None:
            raise AdminError(f"game {game_id} is still open; flag it once it has ended")
        conn.execute("UPDATE games SET flagged = ? WHERE id = ?", (int(flagged), game_id))
        for period in rankings.periods(game):
            rankings.recompute_ranking(conn, game["user_id"], game["mode"], period)
    return f"{'flagged' if flagged else 'unflagged'} game {game_id} (score {game['score']})"


def delete_player(conn: sqlite3.Connection, nickname: str) -> str:
    """ON DELETE CASCADE removes their games, submissions, events and rankings with the user row."""
    with db.tx(conn):
        user = _player(conn, nickname)
        (count,) = conn.execute("SELECT COUNT(*) FROM games WHERE user_id = ?", (user["id"],)).fetchone()
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    return f"deleted {user['nickname']} and {count} game(s)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--db", type=Path, help="database file (default: DATA_DIR/trur.db)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("hide", "unhide", "delete-player"):
        sub.add_parser(name).add_argument("nickname")
    for name in ("flag", "unflag"):
        sub.add_parser(name).add_argument("game_id")
    args = parser.parse_args(argv)

    path = args.db or load_settings().db_path
    if not path.is_file():  # sqlite3 would quietly create an empty one
        print(f"no database at {path}", file=sys.stderr)
        return 1
    conn = db.connect(path)
    try:
        if args.command in ("hide", "unhide"):
            message = set_hidden(conn, args.nickname, args.command == "hide")
        elif args.command in ("flag", "unflag"):
            message = set_flagged(conn, args.game_id, args.command == "flag")
        else:
            message = delete_player(conn, args.nickname)
    except AdminError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
