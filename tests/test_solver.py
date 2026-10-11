"""Step 1 solver tests (docs/test-strategy.md §3, step 1)."""

import random
import time
from itertools import permutations
from pathlib import Path

import pytest

from trur.solver import (
    MIN_TILES,
    Lexicon,
    is_win_word,
    load_lexicon,
    points,
    word_from_tiles,
)

DATA = Path(__file__).resolve().parent.parent / "data"
GARDEN_SUMMER = ("AMAM", "DEARER", "DEER", "GEAR", "GENRES", "NEAR", "NEARER", "RERUNS", "RUNE")


# --- tile mapping ---

@pytest.mark.parametrize(("tile", "pair"), [((0, 0), "GS"), ((1, 0), "GU"), ((4, 2), "RE"), ((5, 4), "ER")])
def test_tile_is_column_letter_then_row_letter(tile, pair) -> None:
    assert word_from_tiles("GARDEN", "SUMMER", [tile, (0, 5)]) == pair + "NS"


def test_word_from_tiles_builds_in_click_order() -> None:
    assert word_from_tiles("GARDEN", "SUMMER", [(4, 5), (5, 1), (5, 4)]) == "NEARER"
    assert word_from_tiles("GARDEN", "SUMMER", [(4, 2), (1, 2), (0, 5)]) == "RERUNS"


@pytest.mark.parametrize(
    ("tiles", "max_tiles", "message"),
    [
        ([], None, "too few"),
        ([(0, 0)], None, "too few"),
        ([(0, 0), (0, 1), (0, 2)], 2, "too many"),
        ([(0, 0), (0, 0)], None, "twice"),
        ([(0, 0), (0, 6)], None, "off the grid"),
        ([(0, 0), (6, 0)], None, "off the grid"),
        ([(-1, 0), (0, 0)], None, "off the grid"),
        ([(0, -1), (0, 0)], None, "off the grid"),
        ([("1", 0), (0, 0)], None, "integers"),
        ([(True, 0), (0, 0)], None, "integers"),
        ([(0, 1.0), (0, 0)], None, "integers"),
    ],
)
def test_word_from_tiles_rejects_bad_tiles(tiles, max_tiles, message) -> None:
    with pytest.raises(ValueError, match=message):
        word_from_tiles("GARDEN", "SUMMER", tiles, max_tiles)


# --- solver ---

def test_solve_garden_summer(lex: Lexicon) -> None:
    assert lex.solve("GARDEN", "SUMMER") == GARDEN_SUMMER
    assert lex.solve("GARDEN", "SUMMER") == GARDEN_SUMMER  # cached, same result


def test_solve_respects_the_rules(lex: Lexicon) -> None:
    found = lex.solve("GARDEN", "SUMMER")
    assert "URGE" not in found and "SAGE" not in found  # only RU and AS exist: fixed letter order
    assert "NENE" not in found  # one NE tile, each tile once per word
    assert "AMAM" in found  # two separate AM tiles
    assert "RANGE" not in found and "AS" not in found  # odd length, single tile
    assert all(len(w) % 2 == 0 and len(w) >= 2 * MIN_TILES for w in found)


def test_win_words_depend_on_orientation(lex: Lexicon) -> None:
    assert [w for w in lex.solve("MEDICAL", "CORRECT") if is_win_word(w)] == ["DEMOCRATIC", "DEMOCRATICLO"]
    assert [w for w in lex.solve("CORRECT", "MEDICAL") if is_win_word(w)] == []
    assert [w for w in lex.solve("STRONG", "MEANING") if is_win_word(w)] == ["GENERATION"]
    assert [w for w in lex.solve("MEANING", "STRONG") if is_win_word(w)] == []


def test_exactly_two_ordered_pairs_have_a_win_word(lex: Lexicon) -> None:
    winning = [(a, b) for a, b in permutations(lex.source, 2) if any(map(is_win_word, lex.solve(a, b)))]
    assert sorted(winning) == [("MEDICAL", "CORRECT"), ("STRONG", "MEANING")]


def test_is_win_word() -> None:
    assert not is_win_word("NEARER" + "ER")  # 8 letters
    assert is_win_word("DEMOCRATIC")
    assert is_win_word("DEMOCRATICLO")


def test_max_tiles_is_the_longest_even_word(lex: Lexicon) -> None:
    assert lex.max_tiles == 6  # DEMOCRATICLO
    assert Lexicon(frozenset({"AS", "RANGE"}), []).max_tiles == MIN_TILES


def test_source_must_be_in_the_dictionary() -> None:
    with pytest.raises(ValueError, match="GARDEN"):
        Lexicon(frozenset({"SUMMER"}), ["SUMMER", "GARDEN"])


def test_load_lexicon_skips_comments(lex: Lexicon) -> None:
    assert "AMAM" in lex.words and not any("#" in w or " " in w for w in lex.words)
    assert lex.source == ("GARDEN", "SUMMER", "MEDICAL", "CORRECT", "STRONG", "MEANING")


# --- oracle: the solver against a backtracking search over real coordinates ---

def _buildable(word: str, word1: str, word2: str) -> bool:
    """Reference: assign actual cells to each letter pair, each cell at most once."""
    if len(word) % 2 or len(word) < 2 * MIN_TILES:
        return False
    pairs = [word[i:i + 2] for i in range(0, len(word), 2)]
    cells = {
        p: [(r, c) for r, b in enumerate(word2) for c, a in enumerate(word1) if a + b == p] for p in set(pairs)
    }

    def place(i: int, used: frozenset) -> bool:
        if i == len(pairs):
            return True
        return any(place(i + 1, used | {cell}) for cell in cells[pairs[i]] if cell not in used)

    return place(0, frozenset())


def test_solver_matches_backtracking_oracle(lex: Lexicon) -> None:
    letters = "AEGMNRSU"
    rng = random.Random(0)
    words = frozenset(lex.words | {"".join(rng.choices(letters, k=rng.choice([4, 6, 8]))) for _ in range(400)})
    oracle_lex = Lexicon(words, [])
    for _ in range(200):
        word1 = "".join(rng.choices(letters, k=rng.randint(5, 10)))
        word2 = "".join(rng.choices(letters, k=rng.randint(5, 10)))
        expected = tuple(sorted(w for w in words if _buildable(w, word1, word2)))
        assert oracle_lex.solve(word1, word2) == expected, (word1, word2)


# --- scoring ---

@pytest.mark.parametrize(("tiles", "expected"), [(2, 4), (3, 9), (4, 16), (5, 25), (6, 36)])
def test_points_are_tiles_squared(tiles, expected) -> None:
    assert points("X" * 2 * tiles) == expected


def test_garden_summer_total_points(lex: Lexicon) -> None:
    assert sum(points(w) for w in lex.solve("GARDEN", "SUMMER")) == 56


# --- real data ---

@pytest.mark.realdata
def test_largest_real_pair_solves_fast() -> None:
    real = load_lexicon(DATA)
    tens = [w for w in real.source if len(w) == 10]
    word1, word2 = tens[0], tens[1]
    start = time.perf_counter()
    real.solve(word1, word2)
    assert time.perf_counter() - start < 0.1
