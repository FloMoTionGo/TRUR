"""Build data/words.txt and data/source_words.txt (docs/plan.md: Word data & solver).

    python -m scripts.build_wordlists --scowl build/scowl/en_CA.txt --scowl-db build/scowl/scowl.txt

- words.txt: every SCOWL en_CA entry that is all lowercase a-z (this drops proper nouns,
  capitalised abbreviations, possessives and contractions), minus words the SCOWL database
  tags only as abbreviations (acct, geog, etc), minus data/blocklist.txt, plus the
  hand-maintained data/extra_words.txt. UPPERCASE, sorted, one per line.
- source_words.txt: the SOURCE_COUNT most frequent words of 5-10 letters from words.txt,
  by wordfreq's English list. Line n = freq_rank n.

Provenance and licences are recorded in data/README.md.
"""

import argparse
import re
from collections.abc import Iterable
from pathlib import Path

SOURCE_COUNT = 500
SOURCE_MIN_LEN, SOURCE_MAX_LEN = 5, 10
_LOWER_WORD = re.compile(r"[a-z]+")
# A scowl.txt entry: "<size info>: [<variant info>:] lemma <POS>[: derived, forms]"
_DB_ENTRY = re.compile(r"(?:^|: )([^:<>]+?) <([^>]*)>(?:: (.*))?$")


def read_word_list(path: Path) -> frozenset[str]:
    """Lowercase words; blank lines and # comments are ignored."""
    lines = (line.split("#", 1)[0].strip().lower() for line in path.read_text(encoding="utf-8").splitlines())
    return frozenset(line for line in lines if line)


def abbreviations(scowl_db_lines: Iterable[str]) -> frozenset[str]:
    """Lowercase forms that the SCOWL database lists only under an abbreviation POS.

    A word on any other line (another POS, or no POS tag at all) is kept, so only pure
    abbreviations are dropped.
    """
    abbr: set[str] = set()
    other: set[str] = set()
    for line in scowl_db_lines:
        if line.startswith("#") or not line.strip():
            continue
        m = _DB_ENTRY.search(line)
        if m and "abbr" in m.group(2):
            forms = [m.group(1), *(m.group(3) or "").split(",")]
            forms = [re.sub(r"\s*[{(].*$", "", f).strip() for f in forms]
            abbr.update(f for f in forms if _LOWER_WORD.fullmatch(f))
        else:
            other.update(re.findall(r"\b[a-z]+\b", re.sub(r"<[^>]*>|\[[^]]*\]", "", line)))  # not POS or tags
    return frozenset(abbr - other)


def filter_dictionary(
    scowl_lines: Iterable[str],
    blocklist: frozenset[str],
    abbrs: frozenset[str] = frozenset(),
    extra: frozenset[str] = frozenset(),
) -> list[str]:
    words = {line.strip() for line in scowl_lines}
    kept = {w for w in words if _LOWER_WORD.fullmatch(w) and w not in abbrs}
    kept |= {w for w in extra if _LOWER_WORD.fullmatch(w)}
    return sorted(w.upper() for w in kept - blocklist)


def pick_source(words: Iterable[str], frequency_order: Iterable[str], count: int = SOURCE_COUNT) -> list[str]:
    """The first `count` words of frequency_order that are in `words` and 5-10 letters long."""
    allowed = {w for w in words if SOURCE_MIN_LEN <= len(w) <= SOURCE_MAX_LEN}
    source: list[str] = []
    for token in frequency_order:
        word = token.upper()
        if word in allowed and word not in source:
            source.append(word)
            if len(source) == count:
                return source
    raise ValueError(f"only {len(source)} source words found, need {count}")


def frequency_order() -> Iterable[str]:
    from wordfreq import iter_wordlist  # dev-only dependency, imported lazily

    return iter_wordlist("en")


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scowl", type=Path, required=True, help="SCOWL en_CA.txt (one word per line)")
    parser.add_argument("--scowl-db", type=Path, required=True, help="SCOWL scowl.txt (database export with POS tags)")
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="folder with blocklist.txt and extra_words.txt; outputs go here")
    args = parser.parse_args(argv)

    words = filter_dictionary(
        args.scowl.read_text(encoding="utf-8").splitlines(),
        read_word_list(args.data / "blocklist.txt"),
        abbreviations(args.scowl_db.read_text(encoding="utf-8").splitlines()),
        read_word_list(args.data / "extra_words.txt"),
    )
    source = pick_source(words, frequency_order(), SOURCE_COUNT)
    write_lines(args.data / "words.txt", words)
    write_lines(args.data / "source_words.txt", source)
    print(f"words.txt: {len(words)} words; source_words.txt: {len(source)} words")


if __name__ == "__main__":
    main()
