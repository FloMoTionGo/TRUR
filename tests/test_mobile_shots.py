"""The phone screenshot helper's command line (scripts/mobile_shots.py, P15). The browser run is in e2e."""

import pytest

from scripts import mobile_shots
from tests.e2e.devices import DEVICES
from tests.e2e.screens import PHONE_STATES, STATES


def test_list_prints_every_device_and_screen(capsys: pytest.CaptureFixture[str]) -> None:
    assert mobile_shots.main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in [d.id for d in DEVICES] + list(STATES) + list(PHONE_STATES):
        assert f"  {name}" in out


def test_help_documents_every_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        mobile_shots.parse_args(["--help"])
    assert exit_.value.code == 0
    out = capsys.readouterr().out
    for option in ("--device", "--screen", "--theme", "--out", "--list"):
        assert option in out


def test_plan_defaults_to_everything_and_skips_phone_screens_on_desktop() -> None:
    shots = mobile_shots.plan(mobile_shots.parse_args([]))
    screens = len(STATES) + len(PHONE_STATES)
    desktops = sum(1 for d in DEVICES if not d.touch)
    assert len(shots) == 2 * (len(DEVICES) * screens - desktops * len(PHONE_STATES))
    assert not any(not d.touch and s in PHONE_STATES for d, s, _ in shots)


def test_plan_narrows_by_device_screen_and_theme() -> None:
    args = mobile_shots.parse_args(["--device", "se-bars", "--device", "s360", "--screen", "boards", "--theme", "dark"])
    assert [(d.id, s, t) for d, s, t in mobile_shots.plan(args)] == [("se-bars", "boards", "dark"), ("s360", "boards", "dark")]


def test_unknown_device_is_refused() -> None:
    with pytest.raises(SystemExit):
        mobile_shots.parse_args(["--device", "nokia"])


def test_default_out_is_per_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    monkeypatch.setattr(mobile_shots, "git_sha", lambda: "abc1234")
    monkeypatch.setattr(mobile_shots, "shoot", lambda shots, out: seen.setdefault("out", out) and [])
    assert mobile_shots.main(["--device", "s360", "--screen", "boards"]) == 0
    assert seen["out"] == mobile_shots.ROOT / "var" / "shots" / "abc1234"
