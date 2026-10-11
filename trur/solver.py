"""Lexicon, grid, tile→word rebuild, scoring and the solver (docs/architecture.md §2).

Stdlib only: scripts/pick_daily.py imports this module in GitHub Actions without installing anything.

The grid: word1 runs across the top (columns), word2 down the side (rows). Tile (row, col) is
word1[col] + word2[row]. A built word therefore has even length, with word1's letters at even
indexes (0, 2, …) and word2's letters at odd indexes.
"""

from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

MIN_TILES = 2
MAX_TRIES = 3
WIN_LETTERS = 10

Tile = tuple[int, int]  # (row → word2 index, col → word1 index)

_A = ord("A")


def _mask(letters: str) -> int:
    m = 0
    for ch in letters:
        m |= 1 << (ord(ch) - _A)
    return m


def _pairs(word: str) -> Counter[str]:
    return Counter(word[i:i + 2] for i in range(0, len(word), 2))


class Lexicon:
    def __init__(self, words: frozenset[str], source: Sequence[str]) -> None:
        missing = [w for w in source if w not in words]
        if missing:
            raise ValueError(f"source words not in the dictionary: {missing[:5]}")
        self.words = words
        self.source = tuple(source)  # index i ⇒ freq_rank i+1
        # Every candidate: even length ≥ 4 (at least MIN_TILES tiles).
        self._candidates = tuple(
            (w, _mask(w[0::2]), _mask(w[1::2]), tuple(_pairs(w).items()))
            for w in sorted(words)
            if len(w) % 2 == 0 and len(w) >= 2 * MIN_TILES
        )
        self.max_tiles = max((len(c[0]) // 2 for c in self._candidates), default=MIN_TILES)
        self.solve = lru_cache(maxsize=256)(self._solve)

    def _solve(self, word1: str, word2: str) -> tuple[str, ...]:
        """Every dictionary word that can be built in this grid, sorted A–Z."""
        count1, count2 = Counter(word1), Counter(word2)
        mask1, mask2 = _mask(word1), _mask(word2)
        found = []
        for word, odd_mask, even_mask, pairs in self._candidates:
            if odd_mask & ~mask1 or even_mask & ~mask2:
                continue
            if all(n <= count1[p[0]] * count2[p[1]] for p, n in pairs):
                found.append(word)
        return tuple(found)


def _read_words(path: Path) -> list[str]:
    """One word per line; blank lines and # comments are ignored (fixtures mark synthetic words)."""
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line]


def load_lexicon(data_dir: Path) -> Lexicon:
    return Lexicon(frozenset(_read_words(data_dir / "words.txt")), _read_words(data_dir / "source_words.txt"))


def word_from_tiles(word1: str, word2: str, tiles: Sequence[Tile], max_tiles: int | None = None) -> str:
    """Rebuild the word from tile coordinates. ValueError if off the grid, repeated, too few or too many."""
    if len(tiles) < MIN_TILES:
        raise ValueError("too few tiles")
    if max_tiles is not None and len(tiles) > max_tiles:
        raise ValueError("too many tiles")
    if len(set(tiles)) != len(tiles):
        raise ValueError("a tile is used twice")
    letters = []
    for row, col in tiles:
        if type(row) is not int or type(col) is not int:
            raise ValueError("tile coordinates must be integers")
        if not (0 <= row < len(word2) and 0 <= col < len(word1)):
            raise ValueError("tile is off the grid")
        letters.append(word1[col] + word2[row])
    return "".join(letters)


def points(word: str) -> int:
    """tiles², so 4 letters = 4, 6 = 9, 8 = 16, 10 = 25."""
    return (len(word) // 2) ** 2


def is_win_word(word: str) -> bool:
    """A daily challenge is won by a word of WIN_LETTERS or more letters."""
    return len(word) >= WIN_LETTERS
