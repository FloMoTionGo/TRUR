"""Feasibility report for step 1 (docs/plan.md: Build order).

    python -m scripts.spike_stats                    # every ordered source pair (a few minutes)
    python -m scripts.spike_stats --sample 5000      # a seeded random sample

For each ordered pair (word1 across, word2 down) it solves the grid and reports how many words
it has, how many points they are worth, and whether it has a win word (10+ letters).
"""

import argparse
import random
import statistics
import time
import tracemalloc
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path

from trur.solver import is_win_word, load_lexicon, pair_key, points

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


def main(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data"), help="folder with words.txt and source_words.txt")
    parser.add_argument("--sample", type=int, default=0, help="solve a random sample of this many pairs (0 = all)")
    parser.add_argument("--seed", type=int, default=0)
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
    text = report(solve_pairs(lex, pairs), total, load_s, lexicon_mb)
    print(text, end="")
    return text


if __name__ == "__main__":
    main()
