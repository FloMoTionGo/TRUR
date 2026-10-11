"""Daily pairs: import the workflow's JSON, make sure a day has a pair, past answers (docs/architecture.md §2).

Stdlib only. The JSON is untrusted input (S10): it's read from one fixed source with a timeout and
a size cap, and every entry is validated. Nothing here raises on a bad source: the fallback picker
fills the gap, so the game never stops for lack of a daily pair.
"""

import logging
import random
import sqlite3
import threading
import urllib.request
from datetime import date
from pathlib import Path

from trur.config import Settings
from trur.errors import ApiError
from trur.solver import (
    DailyEntry, Lexicon, is_win_word, pair_key, parse_daily_json, pick_pair, points, validate_daily_entry,
)

log = logging.getLogger("trur")

_lock = threading.Lock()  # one picker at a time; the app runs as a single process (--workers 1)


def _read_source(s: Settings) -> bytes:
    """The raw JSON, at most daily_fetch_max_bytes. OSError or ValueError on any failure."""
    cap = s.daily_fetch_max_bytes
    if s.daily_json_source.lower().startswith("https://"):
        with urllib.request.urlopen(s.daily_json_source, timeout=s.daily_fetch_timeout_s) as response:
            raw = response.read(cap + 1)
    else:
        with Path(s.daily_json_source).open("rb") as f:
            raw = f.read(cap + 1)
    if len(raw) > cap:
        raise ValueError(f"daily JSON is over {cap} bytes")
    return raw


def read_daily_entries(lex: Lexicon, s: Settings) -> list[DailyEntry]:
    """Every valid entry in the JSON. Logs and skips bad entries; logs and returns [] if the source fails."""
    try:
        items = parse_daily_json(_read_source(s))
    except (OSError, ValueError) as exc:  # includes timeouts, HTTP errors and bad JSON
        log.error("daily JSON could not be read: %s", type(exc).__name__)
        return []
    entries = []
    for i, item in enumerate(items):
        entry = validate_daily_entry(lex, item)
        if entry is None:
            log.warning("daily JSON entry %d skipped: not a valid pair", i)  # the content itself isn't logged
        else:
            entries.append(entry)
    return entries


def import_daily(conn: sqlite3.Connection, lex: Lexicon, s: Settings) -> list[DailyEntry]:
    """Store the JSON's valid entries as 'workflow'. A stored date is never overwritten. Returns the entries."""
    entries = read_daily_entries(lex, s)
    conn.executemany(
        "INSERT OR IGNORE INTO daily_challenges (date, word1, word2, picked_by) VALUES (?, ?, ?, 'workflow')",
        [(e.date.isoformat(), e.word1, e.word2) for e in entries],
    )
    return entries


def get_daily_pair(conn: sqlite3.Connection, day: date) -> DailyEntry | None:
    row = conn.execute("SELECT word1, word2 FROM daily_challenges WHERE date = ?", (day.isoformat(),)).fetchone()
    return None if row is None else DailyEntry(day, row["word1"], row["word2"])


def ensure_daily_pair(conn: sqlite3.Connection, lex: Lexicon, s: Settings, rng: random.Random, day: date) -> DailyEntry:
    """The day's pair: stored, else imported from the JSON, else a fallback pick that has never been used.

    503 no_pair_available if no unused qualifying pair is left.
    """
    found = get_daily_pair(conn, day)
    if found is not None:
        return found
    with _lock:
        found = get_daily_pair(conn, day)  # another request may have filled it while we waited
        if found is not None:
            return found
        planned = import_daily(conn, lex, s)
        found = get_daily_pair(conn, day)
        if found is not None:
            return found
        used = {pair_key(r["word1"], r["word2"]) for r in conn.execute("SELECT word1, word2 FROM daily_challenges")}
        used |= {pair_key(e.word1, e.word2) for e in planned}  # don't take a pair the workflow has planned
        try:
            word1, word2, _ = pick_pair(
                lex, rng, lambda w1, w2, answers: pair_key(w1, w2) not in used and any(map(is_win_word, answers))
            )
        except LookupError:
            raise ApiError(503, "no_pair_available", "No daily pair is available right now. Please try again.") from None
        conn.execute(
            "INSERT OR IGNORE INTO daily_challenges (date, word1, word2, picked_by) VALUES (?, ?, ?, 'fallback')",
            (day.isoformat(), word1, word2),
        )
        log.warning("no daily pair for %s; picked a fallback pair", day.isoformat())
    return get_daily_pair(conn, day)


def daily_answers(conn: sqlite3.Connection, lex: Lexicon, user_id: str | None, day: date, today: date) -> dict:
    """A past day's full answer list, plus the caller's own finds. Today and later stay hidden (S3)."""
    if day >= today:
        raise ApiError(403, "day_not_over", "Answers are published after midnight UTC.")
    entry = get_daily_pair(conn, day)
    if entry is None:
        raise ApiError(404, "not_found", "There was no daily challenge on that day.")
    answers = lex.solve(entry.word1, entry.word2)
    found = None
    if user_id is not None:
        game = conn.execute(
            "SELECT id FROM games WHERE user_id = ? AND daily_date = ?", (user_id, day.isoformat())
        ).fetchone()
        if game is not None:
            found = [r["word"] for r in conn.execute(
                "SELECT word FROM submissions WHERE game_id = ? AND is_valid = 1 ORDER BY id", (game["id"],)
            )]
    return {
        "date": day.isoformat(),
        "word1": entry.word1,
        "word2": entry.word2,
        "total_words": len(answers),
        "words": [{"word": w, "points": points(w)} for w in sorted(answers, key=lambda w: (-points(w), w))],
        "found": found,
    }
