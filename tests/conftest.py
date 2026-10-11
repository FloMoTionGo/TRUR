from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trur.solver import Lexicon, load_lexicon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEFAULT_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """Test clock (docs/test-strategy.md §2). Starts at 2026-10-11T12:00:00Z."""

    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        self.set(now)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("FakeClock needs a UTC-aware datetime")
        self._now = now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(scope="session")
def lex() -> Lexicon:
    """The fixture dictionary (docs/test-strategy.md §2)."""
    return load_lexicon(FIXTURES)
