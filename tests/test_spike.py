from scripts import spike_stats
from tests.conftest import FIXTURES


def test_fixture_report_counts_two_qualifying_pairs() -> None:
    text = spike_stats.main(["--data", str(FIXTURES)])
    assert "Ordered pairs solved: 30 of 30" in text
    assert "Ordered pairs with a 10+-letter word: 2 (6.7%)" in text
    assert "Distinct unordered pairs among them: 2" in text


def test_sample_is_seeded() -> None:
    args = ["--data", str(FIXTURES), "--sample", "10", "--seed", "4"]
    first, second = spike_stats.main(args), spike_stats.main(args)
    assert "Ordered pairs solved: 10 of 30" in first
    strip = lambda t: t.split("## Performance")[0]  # noqa: E731  (timings vary)
    assert strip(first) == strip(second)
