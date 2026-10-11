"""Pair picking and daily-JSON validation (tasks/todo.md T4)."""

import datetime
import random

import pytest

from tests.conftest import FIXTURES
from trur.solver import (
    DailyEntry,
    Lexicon,
    is_win_word,
    pair_key,
    parse_daily_json,
    pick_pair,
    validate_daily_entry,
)

DAILY = FIXTURES / "daily"


def daily_accept(used: set[frozenset[str]]):
    """The daily rule from docs/architecture.md §2."""
    return lambda w1, w2, answers: pair_key(w1, w2) not in used and any(map(is_win_word, answers))


def test_pair_key_ignores_order() -> None:
    assert pair_key("MEDICAL", "CORRECT") == pair_key("CORRECT", "MEDICAL")
    assert pair_key("MEDICAL", "CORRECT") != pair_key("MEDICAL", "STRONG")


def test_pick_pair_is_deterministic_per_seed(lex: Lexicon) -> None:
    always = lambda *_: True  # noqa: E731
    assert pick_pair(lex, random.Random(3), always) == pick_pair(lex, random.Random(3), always)


def test_pick_pair_draws_distinct_words_in_both_orientations(lex: Lexicon) -> None:
    seen = set()
    for seed in range(50):
        word1, word2, answers = pick_pair(lex, random.Random(seed), lambda *_: True)
        assert word1 != word2 and answers == lex.solve(word1, word2)
        seen.add((word1, word2))
    assert any((b, a) in seen for a, b in seen)  # orientation varies


def test_accept_sees_the_exact_orientation(lex: Lexicon) -> None:
    calls = []

    def accept(w1, w2, answers):
        calls.append((w1, w2, answers))
        return (w1, w2) == ("MEDICAL", "CORRECT")

    assert pick_pair(lex, random.Random(0), accept)[:2] == ("MEDICAL", "CORRECT")
    assert all(answers == lex.solve(w1, w2) for w1, w2, answers in calls)


@pytest.mark.parametrize("seed", range(20))
def test_daily_rule_only_picks_qualifying_orientations(lex: Lexicon, seed: int) -> None:
    word1, word2, _ = pick_pair(lex, random.Random(seed), daily_accept(set()))
    assert (word1, word2) in {("MEDICAL", "CORRECT"), ("STRONG", "MEANING")}


@pytest.mark.parametrize("seed", range(20))
def test_daily_rule_skips_used_pairs(lex: Lexicon, seed: int) -> None:
    used = {pair_key("CORRECT", "MEDICAL")}
    assert pick_pair(lex, random.Random(seed), daily_accept(used))[:2] == ("STRONG", "MEANING")


def test_pick_pair_gives_up(lex: Lexicon) -> None:
    with pytest.raises(LookupError):
        pick_pair(lex, random.Random(0), lambda *_: False, max_attempts=10)


# --- daily JSON ---

def test_valid_json_imports_every_entry(lex: Lexicon) -> None:
    entries = [validate_daily_entry(lex, item) for item in parse_daily_json((DAILY / "valid.json").read_bytes())]
    assert entries == [
        DailyEntry(datetime.date(2026, 10, 10), "STRONG", "MEANING"),
        DailyEntry(datetime.date(2026, 10, 11), "MEDICAL", "CORRECT"),
    ]


def test_mixed_json_keeps_only_the_good_entry(lex: Lexicon) -> None:
    items = parse_daily_json((DAILY / "mixed.json").read_bytes())
    results = [validate_daily_entry(lex, item) for item in items]
    assert results[0] == DailyEntry(datetime.date(2026, 10, 11), "MEDICAL", "CORRECT")
    assert results[1:] == [None] * 6  # bad date, not a source word, same word, wrong orientation, missing, number


@pytest.mark.parametrize(
    "item",
    [
        "not an object",
        None,
        {"date": "2026-10-11", "word1": "MEDICAL", "word2": "CORRECT", "extra": 1},
        {"date": "20261011", "word1": "MEDICAL", "word2": "CORRECT"},  # fromisoformat would accept this
        {"date": 20261011, "word1": "MEDICAL", "word2": "CORRECT"},
        {"date": "2026-10-11", "word1": "medical", "word2": "CORRECT"},
        {"date": "2026-10-11", "word1": "GARDEN", "word2": "SUMMER"},  # no win word
    ],
)
def test_bad_entries_are_rejected(lex: Lexicon, item: object) -> None:
    assert validate_daily_entry(lex, item) is None


@pytest.mark.parametrize("raw", [(DAILY / "broken.json").read_bytes(), b'{"date": "2026-10-11"}', b"\xff\xfe", b""])
def test_parse_refuses_anything_but_a_json_array(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_daily_json(raw)
