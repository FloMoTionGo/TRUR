"""The shots helper end to end (P15): it runs as its own process, as a developer would run it."""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.mobile_shots import ROOT

pytestmark = pytest.mark.e2e


def test_shots_helper_saves_a_screenshot(tmp_path: Path) -> None:
    run = subprocess.run(
        [sys.executable, "-m", "scripts.mobile_shots", "--device", "se-bars", "--screen", "game-6x6",
         "--screen", "found-sheet", "--theme", "dark", "--out", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stderr
    for screen in ("game-6x6", "found-sheet"):
        png = (tmp_path / "se-bars" / f"{screen}-dark.png").read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", png[16:24]) == (750, 1106)  # 375 × 553 at DPR 2
