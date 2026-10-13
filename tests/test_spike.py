import random

from scripts import spike_stats
from tests.conftest import FIXTURES
from trur.solver import load_lexicon, pick_pair


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


# --- step M2: matches (docs/test-strategy.md §6, step M2) ---

MATCH_ARGS = ["--data", str(FIXTURES), "--match", "--min-words", "4", "--matches", "8", "--seed", "5"]


def test_match_report_is_seeded() -> None:
    first, second = spike_stats.main(MATCH_ARGS), spike_stats.main(MATCH_ARGS)
    assert "| accepted |" in first and "Slowest pick:" in first
    strip = lambda t: t.split("## Performance")[0]  # noqa: E731  (timings vary)
    assert strip(first) == strip(second)  # the picks, the play and the random ballots repeat


def test_simulated_players_obey_the_match_rules() -> None:
    """Every greedy word goes through match.submit: a locked tile raises, anything but "accepted" fails."""
    lex = load_lexicon(FIXTURES)
    rng = random.Random(2)
    word1, word2, answers = pick_pair(lex, rng, lambda w1, w2, found: len(found) >= 6)
    for players in range(2, 7):
        result = spike_stats.simulate(lex, word1, word2, answers, players, rng, every=6)
        assert len(result.word_scores) == players and all(s >= 1 for s in result.word_scores)


def test_bonus_scale_option_applies_only_to_its_run() -> None:
    from trur import match

    rule = match.BONUS_SCALE
    assert "BONUS_SCALE = 2." in spike_stats.main([*MATCH_ARGS, "--bonus-scale", "2"])
    assert match.BONUS_SCALE == rule
    assert f"BONUS_SCALE = {rule}." in spike_stats.main(MATCH_ARGS)
