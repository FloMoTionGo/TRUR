"""Pick the daily pairs: fill every missing date from today to tomorrow (UTC) in data/daily_challenges.json.

    python -m scripts.pick_daily [--today 2026-10-11] [--seed 1] [--data PATH] [--dry-run]

The daily-challenge workflow runs this every evening. Filling today as well as tomorrow means a
run delayed past midnight doesn't skip a day (docs/architecture.md §8 item 10). Existing entries
are never changed, and a pair is never reused, in either order.

It imports only stdlib modules from trur (solver and clock), so Actions installs nothing.
Exit 1 when no unused qualifying pair is left; the file is then left unchanged.
"""

import argparse
import datetime
import json
import random
import sys
from pathlib import Path

from trur.clock import SystemClock
from trur.solver import Lexicon, is_win_word, load_lexicon, pair_key, parse_daily_json, pick_pair

FILE = "daily_challenges.json"


def pick_missing(
    lex: Lexicon, entries: list[dict], today: datetime.date, rng: random.Random,
) -> list[dict]:
    """New entries for the dates in [today, tomorrow] that have none. LookupError if no unused pair qualifies."""
    dates = {e.get("date") for e in entries if isinstance(e, dict)}
    used = {pair_key(e["word1"], e["word2"]) for e in entries if isinstance(e, dict) and {"word1", "word2"} <= set(e)}
    new = []
    for day in (today, today + datetime.timedelta(days=1)):
        if day.isoformat() in dates:
            continue
        word1, word2, _ = pick_pair(
            lex, rng, lambda w1, w2, answers: pair_key(w1, w2) not in used and any(map(is_win_word, answers))
        )
        used.add(pair_key(word1, word2))
        new.append({"date": day.isoformat(), "word1": word1, "word2": word2})
    return new


def dump(entries: list[dict]) -> str:
    """The file format (docs/architecture.md §8 item 15): sorted by date, indent=2, trailing newline."""
    return json.dumps(sorted(entries, key=lambda e: str(e.get("date"))), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--today", type=datetime.date.fromisoformat, help="UTC date to fill from (default: now)")
    parser.add_argument("--seed", type=int, help="seed for a repeatable pick")
    parser.add_argument("--data", type=Path, default=Path("data"), help="folder with the word lists and the JSON")
    parser.add_argument("--dry-run", action="store_true", help="print the new entries and write nothing")
    args = parser.parse_args(argv)

    today = args.today or SystemClock().now().date()
    path = args.data / FILE
    entries = parse_daily_json(path.read_bytes()) if path.exists() else []
    try:
        new = pick_missing(load_lexicon(args.data), entries, today, random.Random(args.seed))
    except LookupError:
        print("no unused pair with a win word is left; nothing written", file=sys.stderr)
        return 1
    for e in new:
        print(f"{e['date']}: {e['word1']} x {e['word2']}")
    if not new:
        print("today and tomorrow already have pairs")
    elif not args.dry_run:
        path.write_text(dump(entries + new), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
