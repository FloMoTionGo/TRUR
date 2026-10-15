"""SQLite connections, the authorizer, migrations and bookkeeping (docs/architecture.md §2, §4).

Every query uses `?` placeholders (S1). Request connections run with an authorizer that refuses
schema changes, so even a slipped-in statement can't drop a table. The schema itself is applied
once at startup by `migrate`, on its own connection without the authorizer.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from trur import match
from trur.solver import Lexicon

# Entry i takes the database to user_version i+1. Never edit an applied entry; append a new one.
MIGRATIONS: tuple[str, ...] = (
    """
CREATE TABLE users (
  id           TEXT PRIMARY KEY,
  token_hash   TEXT NOT NULL UNIQUE,
  nickname     TEXT NOT NULL,
  nickname_key TEXT NOT NULL UNIQUE,
  hidden       INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0,1)),
  created_at   TEXT NOT NULL
) STRICT;
CREATE TABLE daily_challenges (
  date      TEXT PRIMARY KEY,
  word1     TEXT NOT NULL,
  word2     TEXT NOT NULL,
  picked_by TEXT NOT NULL CHECK (picked_by IN ('workflow','fallback')),
  CHECK (word1 <> word2)
) STRICT;
CREATE TABLE games (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode        TEXT NOT NULL CHECK (mode IN ('free','daily')),
  daily_date  TEXT REFERENCES daily_challenges(date),
  word1       TEXT NOT NULL,
  word2       TEXT NOT NULL,
  total_words INTEGER NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  wrong_count INTEGER NOT NULL DEFAULT 0 CHECK (wrong_count BETWEEN 0 AND 3),
  score       INTEGER NOT NULL DEFAULT 0,
  won         INTEGER NOT NULL DEFAULT 0 CHECK (won IN (0,1)),
  flagged     INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0,1)),
  CHECK ((mode = 'daily') = (daily_date IS NOT NULL)),
  UNIQUE (user_id, daily_date)
) STRICT;
CREATE UNIQUE INDEX games_one_open_free ON games(user_id) WHERE mode = 'free' AND ended_at IS NULL;
CREATE INDEX games_user_mode_started ON games(user_id, mode, started_at);
CREATE INDEX games_open ON games(mode, started_at) WHERE ended_at IS NULL;
CREATE TABLE submissions (
  id           INTEGER PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  game_mode    TEXT NOT NULL CHECK (game_mode IN ('free','daily')),
  word         TEXT NOT NULL,
  is_valid     INTEGER NOT NULL CHECK (is_valid IN (0,1)),
  points       INTEGER NOT NULL DEFAULT 0,
  submitted_at TEXT NOT NULL,
  month        TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX submissions_valid_once ON submissions(game_id, word) WHERE is_valid = 1;
CREATE INDEX submissions_game ON submissions(game_id, submitted_at);
CREATE TABLE rankings (
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game_mode  TEXT NOT NULL CHECK (game_mode IN ('free','daily')),
  period     TEXT NOT NULL,
  best_score INTEGER NOT NULL,
  PRIMARY KEY (user_id, game_mode, period)
) STRICT;
CREATE INDEX rankings_board ON rankings(game_mode, period, best_score DESC);
CREATE TABLE events (
  id         INTEGER PRIMARY KEY,
  user_id    TEXT REFERENCES users(id) ON DELETE CASCADE,
  game_id    TEXT REFERENCES games(id) ON DELETE CASCADE,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX events_created ON events(created_at);
CREATE INDEX events_game ON events(game_id);
CREATE TABLE words (
  id        INTEGER PRIMARY KEY,
  word      TEXT NOT NULL UNIQUE,
  length    INTEGER NOT NULL,
  is_source INTEGER NOT NULL DEFAULT 0 CHECK (is_source IN (0,1)),
  freq_rank INTEGER
) STRICT;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
""",
    # 2: multiplayer matches (docs/multiplayer.md §6.6)
    """
CREATE TABLE matches (
  id           TEXT PRIMARY KEY,
  word1        TEXT NOT NULL,
  word2        TEXT NOT NULL,
  total_words  INTEGER NOT NULL,
  started_at   TEXT NOT NULL,
  ended_at     TEXT NOT NULL,
  month        TEXT NOT NULL,
  player_count INTEGER NOT NULL CHECK (player_count BETWEEN 2 AND 6),
  results      TEXT NOT NULL
) STRICT;
CREATE INDEX matches_started ON matches(started_at);
CREATE TABLE match_players (
  match_id      TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  score         INTEGER NOT NULL,
  word_score    INTEGER NOT NULL,
  vote_bonus    INTEGER NOT NULL DEFAULT 0,
  showcase_word TEXT,
  place         INTEGER NOT NULL,
  words_found   INTEGER NOT NULL,
  PRIMARY KEY (match_id, user_id)
) STRICT;
CREATE TABLE match_votes (
  match_id  TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  voter_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  points    INTEGER NOT NULL CHECK (points IN (5,3,1)),
  PRIMARY KEY (match_id, voter_id, target_id),
  CHECK (voter_id <> target_id)
) STRICT;
CREATE INDEX match_players_user ON match_players(user_id);
CREATE TABLE match_submissions (
  id           INTEGER PRIMARY KEY,
  match_id     TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  word         TEXT NOT NULL,
  tiles        TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'word' CHECK (kind IN ('word','showcase')),
  is_valid     INTEGER NOT NULL CHECK (is_valid IN (0,1)),
  points       INTEGER NOT NULL DEFAULT 0,
  submitted_at TEXT NOT NULL,
  month        TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX match_submissions_valid_once ON match_submissions(match_id, user_id, word) WHERE is_valid = 1;
CREATE INDEX match_submissions_match ON match_submissions(match_id, submitted_at);
ALTER TABLE events ADD COLUMN match_id TEXT REFERENCES matches(id) ON DELETE CASCADE;
""",
)

# --- the authorizer (S1, second line of defence) ---

_DENIED_ACTIONS = frozenset(
    getattr(sqlite3, name)
    for name in dir(sqlite3)
    if name.startswith(("SQLITE_CREATE_", "SQLITE_DROP_"))
) | {sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, source: str | None) -> int:
    """Refuse schema changes, ATTACH/DETACH, PRAGMA writes and load_extension; allow the rest."""
    if action in _DENIED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and arg2 is not None:  # a PRAGMA with an argument writes
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg2 == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    # Pragmas first: once the authorizer is installed they would be refused.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.set_authorizer(_authorizer)
    return conn


def connect(db_path: Path) -> sqlite3.Connection:
    """A request connection: autocommit (transactions via tx()), any thread, authorizer on.

    check_same_thread=False because FastAPI may run a sync dependency and its route on different
    threadpool threads; each connection still serves one request at a time (§8 item 21).
    """
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    return _configure(conn)


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """A read-only connection for the boards (S1): any write fails in SQLite itself."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
    return _configure(conn)


def migrate(db_path: Path) -> None:
    """Apply pending MIGRATIONS on an unrestricted connection. The SQL is always a constant."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(MIGRATIONS)):
            conn.executescript(f"BEGIN IMMEDIATE;{MIGRATIONS[i]}PRAGMA user_version = {i + 1}; COMMIT;")
    finally:
        conn.close()


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[None]:
    """One write transaction. IMMEDIATE takes the write lock up front, so check-then-write is atomic (S5)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def iso(dt: datetime) -> str:
    """UTC timestamp as stored: 'YYYY-MM-DDTHH:MM:SS.ffffffZ'. Fixed width, so text order is time order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- bookkeeping ---

def files_sha256(*paths: Path) -> str:
    """Fingerprint of the word-list files, so `sync_words` can skip an unchanged dictionary."""
    h = hashlib.sha256()
    for path in paths:
        h.update(path.read_bytes())
    return h.hexdigest()


def sync_words(conn: sqlite3.Connection, lex: Lexicon, files_hash: str) -> None:
    """Refill the `words` table from the lexicon, but only when the word-list files changed."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'words_sha256'").fetchone()
    if row is not None and row["value"] == files_hash:
        return
    rank = {w: i + 1 for i, w in enumerate(lex.source)}
    with tx(conn):
        conn.execute("DELETE FROM words")
        conn.executemany(
            "INSERT INTO words (word, length, is_source, freq_rank) VALUES (?, ?, ?, ?)",
            ((w, len(w), int(w in rank), rank.get(w)) for w in sorted(lex.words)),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('words_sha256', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (files_hash,),
        )


def purge_events(conn: sqlite3.Connection, older_than: datetime) -> int:
    """Delete gameplay events at or before the cutoff (S7/S13 retention). Returns the number deleted."""
    return conn.execute("DELETE FROM events WHERE created_at <= ?", (iso(older_than),)).rowcount


# --- matches (docs/multiplayer.md §6.6) ---

DELETED_PLAYER = "deleted player"


def _month(dt: datetime) -> str:
    return iso(dt)[:7]


def _existing_users(conn: sqlite3.Connection, user_ids: list[str]) -> set[str]:
    rows = conn.execute("SELECT id FROM users WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(user_ids),))
    return {r["id"] for r in rows}


def save_match(conn: sqlite3.Connection, rec: match.MatchRecord) -> None:
    """Write a finished match in one transaction: the snapshot, players, submissions, votes and events.

    A player deleted during the match (scripts.admin runs in another process) gets no rows; the
    snapshot keeps their words and score under their user_id, never a nickname. Duplicates store
    no submission row, as in solo.
    """
    res = match.results(rec)
    snapshot = {**res, "players": [{k: v for k, v in p.items() if k != "nickname"} for p in res["players"]]}
    month = _month(rec.started_at)

    def event(user_id: str, type_: str, payload: dict, at: datetime) -> None:
        conn.execute(
            "INSERT INTO events (user_id, match_id, type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, rec.id, type_, json.dumps(payload, separators=(",", ":")), iso(at)),
        )

    with tx(conn):
        alive = _existing_users(conn, [p.user_id for p in rec.players])  # the write lock is held from here
        conn.execute(
            "INSERT INTO matches (id, word1, word2, total_words, started_at, ended_at, month, player_count, results) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec.id, rec.word1, rec.word2, len(rec.answers), iso(rec.started_at), iso(rec.ended_at), month,
             len(rec.players), json.dumps(snapshot, separators=(",", ":"))),
        )
        for p in res["players"]:
            if p["user_id"] not in alive:
                continue
            disp = p["showcase"]
            conn.execute(
                "INSERT INTO match_players "
                "(match_id, user_id, score, word_score, vote_bonus, showcase_word, place, words_found) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rec.id, p["user_id"], p["score"], p["word_score"], disp["bonus"] if disp else 0,
                 disp["word"] if disp else None, p["place"], len(p["words"])),
            )
        for uid in (p.user_id for p in rec.players if p.user_id in alive):  # join order
            event(uid, "match_started", {"player_count": len(rec.players)}, rec.started_at)
        for e in rec.log:
            if e.user_id not in alive:
                continue
            if e.kind == "vote":
                targets = [t for t in e.targets if t in alive]
                conn.executemany(
                    "INSERT INTO match_votes (match_id, voter_id, target_id, points) VALUES (?, ?, ?, ?)",
                    ((rec.id, e.user_id, t, match.VOTE_POINTS[e.targets.index(t)]) for t in targets),
                )
                event(e.user_id, "match_vote_cast", {"ranked": len(e.targets)}, e.at)
                continue
            if e.status != "duplicate":
                conn.execute(
                    "INSERT INTO match_submissions "
                    "(match_id, user_id, word, tiles, kind, is_valid, points, submitted_at, month) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rec.id, e.user_id, e.word, json.dumps([list(t) for t in e.tiles], separators=(",", ":")),
                     e.kind, int(e.status == "accepted"), e.points, iso(e.at), month),
                )
            if e.kind == "showcase":
                event(e.user_id, "match_showcase_word", {"word": e.word}, e.at)
            else:
                event(e.user_id, f"match_word_{e.status}", {"word": e.word, "points": e.points}, e.at)
        for p in res["players"]:
            if p["user_id"] in alive:
                event(p["user_id"], "match_ended", {"place": p["place"], "score": p["score"]}, rec.ended_at)


def match_results(conn: sqlite3.Connection, match_id: str, user_id: str) -> dict | None:
    """The saved results with today's nicknames; None (→ 404) unless user_id played in that match (M1)."""
    row = conn.execute(
        "SELECT m.results FROM matches m JOIN match_players p ON p.match_id = m.id "
        "WHERE m.id = ? AND p.user_id = ?",
        (match_id, user_id),
    ).fetchone()
    if row is None:
        return None
    res = json.loads(row["results"])
    ids = [p["user_id"] for p in res["players"]]
    rows = conn.execute("SELECT id, nickname FROM users WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))
    names = {r["id"]: r["nickname"] for r in rows}
    for p in res["players"]:
        p["nickname"] = names.get(p["user_id"], DELETED_PLAYER)
    return res
