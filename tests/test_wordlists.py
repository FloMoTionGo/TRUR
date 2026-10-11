import re
from pathlib import Path

import pytest

from scripts import build_wordlists as bw

DATA = Path(__file__).resolve().parent.parent / "data"


def test_read_blocklist_ignores_comments_and_case(tmp_path: Path) -> None:
    path = tmp_path / "blocklist.txt"
    path.write_text("# header\nBadWord\n\n  other  # note\n", encoding="utf-8")
    assert bw.read_blocklist(path) == {"badword", "other"}


def test_filter_dictionary_keeps_only_plain_lowercase_words() -> None:
    lines = ["garden", "Garden", "Toronto", "AA", "garden's", "can't", "gardens", "garden", "badword", "café", ""]
    assert bw.filter_dictionary(lines, frozenset({"badword"})) == ["GARDEN", "GARDENS"]


def test_pick_source_takes_frequency_order_within_length() -> None:
    words = ["ABOUT", "GARDEN", "SUMMER", "CAT", "ELEPHANTINE", "MEDICAL"]
    order = ["the", "cat", "about", "elephantine", "garden", "about", "summer", "medical"]
    assert bw.pick_source(words, order, count=3) == ["ABOUT", "GARDEN", "SUMMER"]


def test_pick_source_fails_when_too_few() -> None:
    with pytest.raises(ValueError, match="only 1"):
        bw.pick_source(["GARDEN"], ["garden", "summer"], count=2)


def test_main_writes_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scowl = tmp_path / "en_CA.txt"
    scowl.write_text("summer\ngarden\nGARDEN\nnaughty\nmedical\n", encoding="utf-8")
    (tmp_path / "blocklist.txt").write_text("naughty\n", encoding="utf-8")
    monkeypatch.setattr(bw, "SOURCE_COUNT", 2)
    monkeypatch.setattr(bw, "frequency_order", lambda: ["medical", "naughty", "garden", "summer"])

    bw.main(["--scowl", str(scowl), "--data", str(tmp_path)])

    assert (tmp_path / "words.txt").read_bytes() == b"GARDEN\nMEDICAL\nSUMMER\n"
    assert (tmp_path / "source_words.txt").read_bytes() == b"MEDICAL\nGARDEN\n"


# --- the committed data (docs/test-strategy.md step 1, "Real data") ---

def _lines(name: str) -> list[str]:
    return (DATA / name).read_text(encoding="utf-8").splitlines()


@pytest.mark.realdata
def test_source_words() -> None:
    source = _lines("source_words.txt")
    words = set(_lines("words.txt"))
    assert len(source) == 500
    assert len(set(source)) == 500
    assert all(re.fullmatch(r"[A-Z]{5,10}", w) for w in source)
    assert set(source) <= words


@pytest.mark.realdata
def test_dictionary_is_clean() -> None:
    words = _lines("words.txt")
    assert words == sorted(set(words))
    assert all(re.fullmatch(r"[A-Z]+", w) for w in words)
    blocked = {w.upper() for w in bw.read_blocklist(DATA / "blocklist.txt")}
    assert blocked.isdisjoint(words)
