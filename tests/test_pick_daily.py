"""scripts/pick_daily.py and the committed daily_challenges.json (TS §3 step 5 "pick_daily"; CI "Real data").

Most tests call main() in-process so coverage counts them; one runs the real `python -m` entry point.

The fixture dictionary has exactly two qualifying pairs: MEDICAL × CORRECT and STRONG × MEANING.
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from scripts import pick_daily
from tests.conftest import FIXTURES, ROOT
from trur.solver import load_lexicon, pair_key, validate_daily_entry

DATA = ROOT / "data"


@pytest.fixture
def data(tmp_path: Path) -> Path:
    for name in ("words.txt", "source_words.txt"):
        shutil.copyfile(FIXTURES / name, tmp_path / name)
    return tmp_path


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def run(data: Path, *args: str) -> Result:
    """main() in-process, so coverage sees it; test_runs_as_a_module checks the real entry point."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pick_daily.main(["--data", str(data), *args])
    return Result(code, out.getvalue(), err.getvalue())


def write(data: Path, entries: list[dict]) -> bytes:
    raw = (json.dumps(entries, indent=2) + "\n").encode()
    (data / "daily_challenges.json").write_bytes(raw)
    return raw


def read(data: Path) -> list[dict]:
    return json.loads((data / "daily_challenges.json").read_text(encoding="utf-8"))


def test_fills_today_and_tomorrow(data: Path) -> None:
    r = run(data, "--today", "2026-10-11", "--seed", "1")
    assert r.returncode == 0, r.stderr
    entries = read(data)
    assert [e["date"] for e in entries] == ["2026-10-11", "2026-10-12"]
    assert {pair_key(e["word1"], e["word2"]) for e in entries} == {
        pair_key("MEDICAL", "CORRECT"), pair_key("STRONG", "MEANING"),
    }
    lex = load_lexicon(data)
    assert all(validate_daily_entry(lex, e) is not None for e in entries)  # passes the backend's import checks
    raw = (data / "daily_challenges.json").read_bytes()
    assert raw == (json.dumps(entries, indent=2) + "\n").encode()  # the file format

    again = run(data, "--today", "2026-10-11", "--seed", "2")
    assert again.returncode == 0 and (data / "daily_challenges.json").read_bytes() == raw


@pytest.mark.parametrize("used", [("MEDICAL", "CORRECT"), ("CORRECT", "MEDICAL")])
def test_a_used_pair_is_never_picked_again_and_old_entries_stay(data: Path, used: tuple[str, str]) -> None:
    lex = load_lexicon(data)
    for seed in range(20):
        before = write(data, [{"date": "2026-10-10", "word1": used[0], "word2": used[1]}])
        r = run(data, "--today", "2026-10-10", "--seed", str(seed))
        assert r.returncode == 0, r.stderr
        old, new = read(data)
        assert (new["date"], new["word1"], new["word2"]) == ("2026-10-11", "STRONG", "MEANING")
        assert validate_daily_entry(lex, new) is not None  # this orientation has a win word
        after = (data / "daily_challenges.json").read_bytes()
        assert after.startswith(before[: before.rindex(b"}") + 1])  # the old entry is byte-identical


def test_nothing_to_do_when_both_days_exist(data: Path) -> None:
    raw = write(data, [
        {"date": "2026-10-11", "word1": "MEDICAL", "word2": "CORRECT"},
        {"date": "2026-10-12", "word1": "STRONG", "word2": "MEANING"},
    ])
    for seed in range(3):
        r = run(data, "--today", "2026-10-11", "--seed", str(seed))
        assert r.returncode == 0 and (data / "daily_challenges.json").read_bytes() == raw


def test_no_pair_left_is_a_failure_that_writes_nothing(data: Path) -> None:
    raw = write(data, [
        {"date": "2026-10-01", "word1": "MEDICAL", "word2": "CORRECT"},
        {"date": "2026-10-02", "word1": "STRONG", "word2": "MEANING"},
    ])
    r = run(data, "--today", "2026-10-11", "--seed", "0")
    assert r.returncode != 0 and "no unused pair" in r.stderr
    assert (data / "daily_challenges.json").read_bytes() == raw


def test_one_pair_short_writes_nothing(data: Path) -> None:
    """Today can be filled but tomorrow can't: the run fails as a whole, so no half-written file."""
    raw = write(data, [{"date": "2026-10-01", "word1": "MEDICAL", "word2": "CORRECT"}])
    assert run(data, "--today", "2026-10-11").returncode != 0
    assert (data / "daily_challenges.json").read_bytes() == raw


def test_dry_run_prints_and_writes_nothing(data: Path) -> None:
    r = run(data, "--today", "2026-10-11", "--seed", "1", "--dry-run")
    assert r.returncode == 0
    assert "2026-10-11: " in r.stdout and "2026-10-12: " in r.stdout
    assert not (data / "daily_challenges.json").exists()


def test_runs_as_a_module(data: Path) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "scripts.pick_daily", "--data", str(data), "--today", "2026-10-11", "--seed", "1"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert [e["date"] for e in read(data)] == ["2026-10-11", "2026-10-12"]


# --- the committed file (CI "Real data") ---

@pytest.mark.realdata
def test_committed_daily_challenges() -> None:
    lex = load_lexicon(DATA)
    entries = json.loads((DATA / "daily_challenges.json").read_text(encoding="utf-8"))
    assert entries and all(validate_daily_entry(lex, e) is not None for e in entries)
    dates = [e["date"] for e in entries]
    assert dates == sorted(set(dates))  # sorted, no date twice
    pairs = [pair_key(e["word1"], e["word2"]) for e in entries]
    assert len(set(pairs)) == len(pairs)  # no pair repeats, in either order
    raw = (DATA / "daily_challenges.json").read_bytes()
    assert raw == (json.dumps(entries, indent=2) + "\n").encode()
