"""Feasibility report for step 1 (docs/plan.md: Build order), and for matches with --match (step M2).

    python -m scripts.spike_stats                    # every ordered source pair (a few minutes)
    python -m scripts.spike_stats --sample 5000      # a seeded random sample
    python -m scripts.spike_stats --match --sample 5000 --min-words 20

For each ordered pair (word1 across, word2 down) it solves the grid and reports how many words
it has, how many points they are worth, and whether it has a win word (10+ letters).

--match (docs/multiplayer.md §10, M2) reports how many pairs a MIN_MATCH_WORDS rule accepts and how long
a pick takes, then plays seeded matches through trur.match: 2–6 greedy players, each submitting the best
word still open to them every 6 s for 60 s, then a display word each and random ballots.
"""

import argparse
import random
import statistics
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import permutations
from pathlib import Path

from trur import match
from trur.solver import Lexicon, Tile, is_win_word, load_lexicon, pair_key, pick_pair, points

THRESHOLDS = (1, 4, 8, 12, 16, 20, 30)


@dataclass
class PairResult:
    word1: str
    word2: str
    words: int
    points: int
    longest: int
    has_win: bool
    seconds: float


def solve_pairs(lex, pairs) -> list[PairResult]:
    results = []
    for word1, word2 in pairs:
        start = time.perf_counter()
        answers = lex._solve(word1, word2)  # uncached: every pair is solved once
        elapsed = time.perf_counter() - start
        results.append(PairResult(
            word1, word2, len(answers), sum(map(points, answers)),
            max(map(len, answers), default=0), any(map(is_win_word, answers)), elapsed,
        ))
    return results


def _pct(values: list[float], q: int) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1] if len(values) > 1 else values[0]


def report(results: list[PairResult], total_pairs: int, load_s: float, lexicon_mb: float) -> str:
    n = len(results)
    words = sorted(r.words for r in results)
    pts = sorted(r.points for r in results)
    times_ms = [r.seconds * 1000 for r in results]
    winners = [r for r in results if r.has_win]
    unordered = {pair_key(r.word1, r.word2) for r in winners}
    lines = [
        "# Feasibility spike",
        "",
        f"Ordered pairs solved: {n:,} of {total_pairs:,}",
        "",
        "## Words per grid",
        "",
        "| min | p10 | p25 | median | p75 | p90 | max |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        "| " + " | ".join(f"{v:g}" for v in (words[0], *(_pct(words, q) for q in (10, 25, 50, 75, 90)), words[-1])) + " |",
        "",
        "| Grids with fewer than N words | " + " | ".join(f"N={t}" for t in THRESHOLDS) + " |",
        "| --- |" + " --- |" * len(THRESHOLDS),
        "| share | " + " | ".join(f"{sum(w < t for w in words) / n:.1%}" for t in THRESHOLDS) + " |",
        "",
        "## Points per grid (all words found)",
        "",
        f"median {statistics.median(pts):g}, p90 {_pct(pts, 90):g}, max {pts[-1]}",
        "",
        "## Daily challenge supply",
        "",
        f"- Ordered pairs with a 10+-letter word: {len(winners):,} ({len(winners) / n:.1%})",
        f"- Distinct unordered pairs among them: {len(unordered):,} (each can be a daily pair once)",
        "",
        "## Performance",
        "",
        f"- Lexicon load: {load_s:.2f} s, about {lexicon_mb:.0f} MB of Python objects",
        f"- Solve time per grid: median {statistics.median(times_ms):.1f} ms, max {max(times_ms):.1f} ms",
    ]
    return "\n".join(lines) + "\n"


# --- step M2: matches (docs/multiplayer.md §10) ---

MATCH_THRESHOLDS = (12, 20, 30, 40, 60)
PLAYER_COUNTS = range(2, 7)
SIM_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def tile_options(word1: str, word2: str) -> dict[str, list[Tile]]:
    """Letter pair → the tiles that show it (column letter first)."""
    options: dict[str, list[Tile]] = {}
    for r, row_letter in enumerate(word2):
        for c, col_letter in enumerate(word1):
            options.setdefault(col_letter + row_letter, []).append((r, c))
    return options


def best_move(m: match.Match, p: match.PlayerState, words: list[str],
              options: dict[str, list[Tile]]) -> tuple[Tile, ...] | None:
    """The greedy player's choice: the open word worth most right now, each pair on its least-used free tile."""
    best = None
    for word in words:
        if word in p.found:
            continue
        tiles: list[Tile] = []
        for i in range(0, len(word), 2):
            free = [t for t in options[word[i:i + 2]] if t not in p.locked and t not in tiles]
            if not free:
                break
            tiles.append(min(free, key=lambda t: (m.uses.get(t, 0), t)))
        else:
            key = (match.word_points([m.uses.get(t, 0) for t in tiles]), -len(word), word)
            if best is None or key > best[0]:
                best = (key, tuple(tiles))
    return None if best is None else best[1]


@dataclass
class MatchResult:
    players: int
    word_scores: list[int]
    starved: bool          # someone had no word left at the end
    winner_changed: bool   # the vote bonus changed who is first
    max_bonus: int


def _first(scores: dict[str, int]) -> set[str]:
    top = max(scores.values())
    return {u for u, s in scores.items() if s == top}


def simulate(lex: Lexicon, word1: str, word2: str, answers: tuple[str, ...], n: int,
             rng: random.Random, every: int = 6) -> MatchResult:
    users = [(f"u{i}", f"Player {i}", f"player {i}") for i in range(n)]
    m = match.new_match("sim", word1, word2, answers, users, SIM_START, lex.max_tiles)
    words, options = sorted(answers), tile_options(word1, word2)
    for tick in range(1, match.DURATION_S // every + 1):
        now = SIM_START + timedelta(seconds=tick * every)
        order = list(m.players)
        rng.shuffle(order)
        for uid in order:
            tiles = best_move(m, m.players[uid], words, options)
            if tiles is not None and match.submit(m, uid, tiles, now).status != "accepted":
                raise AssertionError("the greedy player broke a rule")
    starved = any(best_move(m, p, words, options) is None for p in m.players.values())

    grid = [(r, c) for r in range(len(word2)) for c in range(len(word1))]
    for uid, p in m.players.items():  # a display word each: two of their free tiles
        free = [tile for tile in grid if tile not in p.locked]
        if len(free) >= 2:
            match.display(m, uid, rng.sample(free, 2), m.ends_at)
    closed = m.ends_at + timedelta(milliseconds=1)
    if match.close_play(m, closed, rng):
        for uid in m.players:
            ballot = match.ballot_for(m, uid, rng)  # shuffled for this voter: a random ranking
            if ballot:
                match.vote(m, uid, [c.id for c in ballot[:len(match.VOTE_POINTS)]], closed + timedelta(seconds=1))
    rows = match.results(match.finish(m, closed + timedelta(seconds=match.VOTE_S)))["players"]
    word = {r["user_id"]: r["word_score"] for r in rows}
    total = {r["user_id"]: r["score"] for r in rows}
    return MatchResult(n, list(word.values()), starved, _first(word) != _first(total),
                       max((r["display"] or {}).get("bonus", 0) for r in rows))


def match_report(lex: Lexicon, results: list[PairResult], total_pairs: int, min_words: int, per_count: int,
                 rng: random.Random, every: int = 6) -> tuple[str, float]:
    """The seeded report, and the slowest pick in seconds (reported apart, since timings vary)."""
    n = len(results)
    lines = [
        "# Match spike (step M2)",
        "",
        f"Ordered pairs solved: {n:,} of {total_pairs:,}. MIN_MATCH_WORDS = {min_words}. "
        f"{per_count} matches per player count ({per_count * len(PLAYER_COUNTS):,} in all), "
        f"a word every {every} s per player, BONUS_SCALE = {match.BONUS_SCALE}.",
        "",
        "## Pairs a MIN_MATCH_WORDS rule accepts",
        "",
        "| N | " + " | ".join(str(t) for t in MATCH_THRESHOLDS) + " |",
        "| --- |" + " --- |" * len(MATCH_THRESHOLDS),
        "| accepted | " + " | ".join(f"{sum(r.words >= t for r in results) / n:.1%}" for t in MATCH_THRESHOLDS) + " |",
    ]
    picks = []
    sims: list[MatchResult] = []
    for players in PLAYER_COUNTS:
        for _ in range(per_count):
            start = time.perf_counter()
            word1, word2, answers = pick_pair(lex, rng, lambda w1, w2, found: len(found) >= min_words)
            picks.append(time.perf_counter() - start)
            sims.append(simulate(lex, word1, word2, answers, players, rng, every))
    lines += [
        "",
        "## Simulated matches",
        "",
        "| Players | Median word score | Everyone still has a word at the end | All word scores within 2 points "
        "| Max vote bonus | Winner changed by random votes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for players in PLAYER_COUNTS:
        group = [s for s in sims if s.players == players]
        scores = [x for s in group for x in s.word_scores]
        lines.append(
            f"| {players} | {statistics.median(scores):g} | {sum(not s.starved for s in group) / len(group):.1%} "
            f"| {sum(max(s.word_scores) - min(s.word_scores) <= 2 for s in group) / len(group):.1%} "
            f"| {max(s.max_bonus for s in group)} | {sum(s.winner_changed for s in group) / len(group):.1%} |"
        )
    six = [s for s in sims if s.players == 6]
    close = sum(max(s.word_scores) - min(s.word_scores) <= 2 for s in sims) / len(sims)
    vote = max(sum(s.winner_changed for s in sims if s.players == k) / per_count for k in PLAYER_COUNTS)
    lines += [
        "",
        "## Checks (docs/multiplayer.md §10, M2)",
        "",
        f"- 6-player matches where everyone still has a word at the end: "
        f"{sum(not s.starved for s in six) / len(six):.1%} (need at least 95 %)",
        f"- Matches with all word scores within 2 points: {close:.1%} (need under 20 %)",
        f"- Highest share of matches whose winner random votes changed, at any player count: {vote:.1%} "
        "(need at most 30 %)",
    ]
    return "\n".join(lines) + "\n", max(picks)


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data"), help="folder with words.txt and source_words.txt")
    parser.add_argument("--sample", type=int, default=0, help="solve a random sample of this many pairs (0 = all)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--match", action="store_true", help="the step-M2 match report instead")
    parser.add_argument("--min-words", type=int, default=20, help="--match: the MIN_MATCH_WORDS to simulate")
    parser.add_argument("--matches", type=int, default=200, help="--match: matches per player count (2-6)")
    parser.add_argument("--every", type=int, default=6, help="--match: seconds between a player's words")
    parser.add_argument("--bonus-scale", type=int, default=None,
                        help="--match: the vote bonus scale to simulate (default: the rule in trur/match.py)")
    args = parser.parse_args(argv)

    tracemalloc.start()
    start = time.perf_counter()
    lex = load_lexicon(args.data)
    load_s = time.perf_counter() - start
    lexicon_mb = tracemalloc.get_traced_memory()[0] / 1e6
    tracemalloc.stop()

    pairs = list(permutations(lex.source, 2))
    total = len(pairs)
    if args.sample and args.sample < total:
        pairs = random.Random(args.seed).sample(pairs, args.sample)
    if args.match:
        rule = match.BONUS_SCALE
        if args.bonus_scale is not None:
            match.BONUS_SCALE = args.bonus_scale  # bonus() reads it at call time
        try:
            text, pick_s = match_report(lex, solve_pairs(lex, pairs), total, args.min_words, args.matches,
                                        random.Random(args.seed), args.every)
        finally:
            match.BONUS_SCALE = rule
        text += (f"\n## Performance\n\n- Slowest pick: {pick_s * 1000:.0f} ms on this machine "
                 "(need under 1 s on a shared-cpu-1x)\n")
    else:
        text = report(solve_pairs(lex, pairs), total, load_s, lexicon_mb)
    print(text, end="")
    return text


if __name__ == "__main__":
    main()
