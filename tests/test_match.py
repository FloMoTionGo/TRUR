"""Step M1: the multiplayer rules engine (docs/test-strategy.md §6, docs/multiplayer.md §3)."""

import random
from datetime import datetime, timedelta, timezone

import pytest

from trur import match
from trur.match import (
    AlreadyVoted,
    NotRunning,
    NotVoting,
    TileLocked,
    bonus,
    pct,
    word_points,
)
from trur.solver import Tile

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
PLAY = T0 + timedelta(seconds=1)
GARDEN_SUMMER = ("AMAM", "DEARER", "DEER", "GEAR", "GENRES", "NEAR", "NEARER", "RERUNS", "RUNE")


def make_match(word1="GARDEN", word2="SUMMER", answers=GARDEN_SUMMER, players="ABC", starts_at=T0) -> match.Match:
    return match.new_match("m1", word1, word2, answers, [(p, p, p.lower()) for p in players], starts_at)


def tile(m: match.Match, text: str) -> Tile:
    """The coordinates of the one tile showing `text`; raises if it occurs more than once."""
    hits = [(r, c) for r in range(len(m.word2)) for c in range(len(m.word1)) if m.word1[c] + m.word2[r] == text]
    if len(hits) != 1:
        raise ValueError(f"{text} occurs {len(hits)} times")
    return hits[0]


def tiles(m: match.Match, word: str) -> list[Tile]:
    return [tile(m, word[i:i + 2]) for i in range(0, len(word), 2)]


def play(m: match.Match, user: str, word: str, at: datetime = PLAY) -> match.SubmitOutcome:
    return match.submit(m, user, tiles(m, word), at)


def grid(m: match.Match) -> dict[str, int]:
    """Percentages of the tiles that changed, by text."""
    return {m.word1[c] + m.word2[r]: match.pct(n) for (r, c), n in m.uses.items()}


# --- rounding ---

def test_pct_rounds_half_up() -> None:
    assert [pct(u) for u in range(9)] == [100, 50, 25, 13, 6, 3, 2, 1, 1]


def test_word_points() -> None:
    assert word_points([0, 0]) == 4
    assert word_points([1, 1, 1]) == 5  # 4.5
    assert word_points([3, 4]) == 1  # 0.38 → floor of 1


def test_same_word_second_finder_rounds_half_up() -> None:
    m = make_match()
    assert play(m, "A", "NEARER").points == 9
    assert play(m, "B", "NEARER").points == 5  # 3 × 150 ÷ 100 = 4.5
    assert play(m, "C", "DEER").points == 3  # 2 × 125 ÷ 100 = 2.5


def test_worked_example() -> None:
    m = make_match()
    assert play(m, "A", "GEAR").points == 4
    assert play(m, "B", "NEARER").points == 8
    with pytest.raises(TileLocked):
        play(m, "A", "DEARER")
    with pytest.raises(TileLocked):
        play(m, "B", "GEAR")
    assert play(m, "C", "GEAR").points == 2
    assert play(m, "C", "DEER").points == 3
    assert grid(m) == {"GE": 25, "AR": 13, "NE": 50, "ER": 25, "DE": 50}
    assert match.pct_grid(m)[0] == [100] * 6
    totals = {p["nickname"]: p["score"] for p in match.results(match.finish(m, PLAY))["players"]}
    assert totals == {"B": 8, "C": 5, "A": 4}


def test_one_point_minimum() -> None:
    m = make_match(players="ABCDE")
    for p in "ABC":
        play(m, p, "GEAR")
    play(m, "D", "NEAR")
    assert play(m, "E", "GEAR").points == 1


# --- rules ---

def test_lock_is_per_player() -> None:
    m = make_match()
    play(m, "A", "GEAR")
    with pytest.raises(TileLocked):
        play(m, "A", "NEAR")
    assert play(m, "B", "NEAR").status == "accepted"


def test_uses_count_per_tile_not_per_text() -> None:
    m = make_match("GARAGE", "SUMMER", {"AMAM"})
    out = match.submit(m, "A", [(2, 1), (3, 3)], PLAY)
    assert out.status == "accepted"
    assert out.changes == (match.Change((2, 1), 50), match.Change((3, 3), 50))
    assert match.pct_grid(m)[3][1] == 100 and match.pct_grid(m)[2][3] == 100


def test_same_word_from_other_copies_is_duplicate() -> None:
    m = make_match("GARAGE", "SUMMER", {"AMAM"})
    match.submit(m, "A", [(2, 1), (3, 3)], PLAY)
    out = match.submit(m, "A", [(3, 1), (2, 3)], PLAY)
    assert out == match.SubmitOutcome("duplicate", "AMAM", 0, ())
    assert m.players["A"].locked == {(2, 1), (3, 3)}
    assert m.uses == {(2, 1): 1, (3, 3): 1}
    accepted = [(e.user_id, e.word) for e in match.finish(m, PLAY).log if e.status == "accepted"]
    assert accepted == [("A", "AMAM")]  # the unique index on valid words can't fire


def test_same_word_two_players() -> None:
    m = make_match()
    assert play(m, "A", "GEAR").points == 4
    assert play(m, "B", "GEAR").points == 2
    assert grid(m) == {"GE": 25, "AR": 25}


def test_rejected_word_changes_nothing() -> None:
    m = make_match()
    out = play(m, "A", "RUAR")
    assert out == match.SubmitOutcome("rejected", "RUAR", 0, ())
    assert m.uses == {} and m.players["A"].locked == set()


def test_duplicate_is_checked_before_locks() -> None:
    m = make_match()
    play(m, "A", "GEAR")
    assert play(m, "A", "GEAR").status == "duplicate"


def test_invalid_tiles() -> None:
    m = make_match()
    with pytest.raises(ValueError):
        match.submit(m, "A", [(0, 0)], PLAY)


# --- time boundaries ---

def test_time_boundaries() -> None:
    m = make_match()
    with pytest.raises(NotRunning):
        play(m, "A", "GEAR", T0 - timedelta(microseconds=1))
    assert play(m, "A", "GEAR", T0).status == "accepted"
    assert play(m, "B", "GEAR", m.ends_at + timedelta(milliseconds=300)).status == "accepted"
    with pytest.raises(NotRunning):
        play(m, "C", "GEAR", m.ends_at + timedelta(milliseconds=301))


def test_closed_play_is_not_running() -> None:
    m = make_match()
    match.close_play(m, PLAY, random.Random(0))
    with pytest.raises(NotRunning):
        play(m, "A", "GEAR", PLAY)


def test_ends_in_ms_never_negative() -> None:
    m = make_match()
    assert match.ms_until(m.ends_at, T0) == 60_000
    assert match.ms_until(m.ends_at, m.ends_at + timedelta(seconds=5)) == 0


# --- display word ---

@pytest.mark.parametrize("word", ["GEAR", "RUAR"])  # in answers or not
def test_display_word_locks_halves_and_scores_nothing(word: str) -> None:
    m = make_match()
    out = match.display(m, "A", tiles(m, word), PLAY)
    assert out.word == word and all(c.pct == 50 for c in out.changes)
    assert m.players["A"].locked == set(tiles(m, word))
    assert m.players["A"].found == {}
    rec = match.finish(m, PLAY)
    assert match.results(rec)["players"][0]["word_score"] == 0


def test_second_display_word_replaces_first() -> None:
    m = make_match()
    match.display(m, "A", tiles(m, "GEAR"), PLAY)
    match.display(m, "A", tiles(m, "DEER"), PLAY)
    assert m.players["A"].display.word == "DEER"
    assert m.players["A"].locked == set(tiles(m, "GEAR") + tiles(m, "DEER"))
    assert grid(m) == {"GE": 50, "AR": 50, "DE": 50, "ER": 50}
    assert [e.word for e in m.log if e.kind == "display"] == ["GEAR", "DEER"]


def test_display_word_refusals() -> None:
    m = make_match()
    play(m, "A", "GEAR")
    with pytest.raises(TileLocked):
        match.display(m, "A", tiles(m, "NEAR"), PLAY)
    with pytest.raises(NotRunning):
        match.display(m, "B", tiles(m, "NEAR"), T0 - timedelta(microseconds=1))
    with pytest.raises(NotRunning):
        match.display(m, "B", tiles(m, "NEAR"), m.ends_at + timedelta(milliseconds=301))


def test_display_word_equal_to_a_found_word() -> None:
    m = make_match("GARAGE", "SUMMER", {"AMAM"})
    match.submit(m, "A", [(2, 1), (3, 3)], PLAY)
    match.display(m, "A", [(3, 1), (2, 3)], PLAY)
    assert m.players["A"].display.word == "AMAM"
    assert list(m.players["A"].found) == ["AMAM"] and m.players["A"].found["AMAM"].points == 4


@pytest.mark.parametrize("displays, expected", [("", False), ("A", False), ("AB", True)])
def test_close_play_needs_two_display_words(displays: str, expected: bool) -> None:
    m = make_match()
    for p, word in zip(displays, ["GEAR", "DEER"]):
        match.display(m, p, tiles(m, word), PLAY)
    assert match.close_play(m, PLAY, random.Random(0)) is expected
    assert sorted(m.candidates) == [f"c{i}" for i in range(1, len(displays) + 1)]


# --- the vote ---

CLOSED = T0 + timedelta(seconds=61)
VOTE = CLOSED + timedelta(seconds=1)


def voting_match(displays: dict[str, str], players: str = "ABCD") -> match.Match:
    m = make_match(players=players)
    for p, word in displays.items():
        match.display(m, p, tiles(m, word), PLAY)
    match.close_play(m, CLOSED, random.Random(0))
    return m


def cid(m: match.Match, user: str) -> str:
    return next(c for c, u in m.candidates.items() if u == user)


def test_ballot_has_every_other_word() -> None:
    m = voting_match({"A": "GEAR", "B": "DEER", "C": "RUNE"})
    rng = random.Random(0)
    assert sorted(c.word for c in match.ballot_for(m, "A", rng)) == ["DEER", "RUNE"]
    assert sorted(c.word for c in match.ballot_for(m, "D", rng)) == ["DEER", "GEAR", "RUNE"]
    assert {c.id for c in match.ballot_for(m, "D", rng)} == set(m.candidates)


@pytest.mark.parametrize("ranking", [["self"], ["c9"], ["b", "b"], ["b", "c", "self", "c9"], []])
def test_vote_refuses_bad_ballots(ranking: list[str]) -> None:
    m = voting_match({"A": "GEAR", "B": "DEER", "C": "RUNE"})
    ids = {"self": cid(m, "A"), "b": cid(m, "B"), "c": cid(m, "C")}
    with pytest.raises(ValueError):
        match.vote(m, "A", [ids.get(x, x) for x in ranking], VOTE)
    assert m.players["A"].ballot is None


def test_vote_once_and_in_time() -> None:
    m = voting_match({"A": "GEAR", "B": "DEER"})
    with pytest.raises(NotVoting):
        match.vote(m, "A", [cid(m, "B")], CLOSED)
    with pytest.raises(NotVoting):
        match.vote(m, "A", [cid(m, "B")], m.vote_ends_at + timedelta(milliseconds=301))
    match.vote(m, "A", [cid(m, "B")], m.vote_ends_at + timedelta(milliseconds=300))
    with pytest.raises(AlreadyVoted):
        match.vote(m, "A", [cid(m, "B")], VOTE)


def test_no_vote_before_close_or_when_skipped() -> None:
    m = make_match()
    match.display(m, "A", tiles(m, "GEAR"), PLAY)
    with pytest.raises(NotVoting):
        match.vote(m, "B", ["c1"], PLAY)
    match.close_play(m, CLOSED, random.Random(0))
    with pytest.raises(NotVoting):
        match.vote(m, "B", ["c1"], VOTE)


def test_two_candidates_points() -> None:
    m = voting_match({"A": "GEAR", "B": "DEER", "C": "RUNE"})
    match.vote(m, "C", [cid(m, "A"), cid(m, "B")], VOTE)
    match.vote(m, "B", [cid(m, "A")], VOTE)
    disp = {p["nickname"]: p["display"] for p in match.results(match.finish(m, VOTE))["players"]}
    assert disp["A"]["votes"] == 10 and disp["B"]["votes"] == 3 and disp["C"]["votes"] == 0
    assert disp["D"] is None


@pytest.mark.parametrize("received, ballots, expected", [(0, 0, 0), (15, 3, 5), (5, 4, 1), (13, 3, 4), (3, 2, 2), (0, 2, 0)])
def test_bonus(received: int, ballots: int, expected: int) -> None:
    assert bonus(received, ballots) == expected


def test_player_without_display_word_votes_and_gets_no_bonus() -> None:
    m = voting_match({"A": "GEAR", "B": "DEER"}, players="ABC")
    match.vote(m, "C", [cid(m, "A")], VOTE)
    rows = {p["nickname"]: p for p in match.results(match.finish(m, VOTE))["players"]}
    assert rows["C"]["display"] is None and rows["C"]["score"] == 0


def test_worked_results_example() -> None:
    m = make_match()
    play(m, "A", "GEAR")  # 4
    play(m, "B", "NEARER")  # 8
    play(m, "C", "DEER")  # 3 (DE 100, ER 50)
    match.display(m, "A", tiles(m, "RUNS"), PLAY)
    match.display(m, "B", [(0, 0), (1, 3)], PLAY)  # GSDU, not a word
    assert match.close_play(m, CLOSED, random.Random(0))
    match.vote(m, "B", [cid(m, "A")], VOTE)
    match.vote(m, "C", [cid(m, "A")], VOTE)
    match.vote(m, "A", [cid(m, "B")], VOTE)
    res = match.results(match.finish(m, VOTE))
    rows = [(p["place"], p["nickname"], p["word_score"], p["display"], p["score"]) for p in res["players"]]
    assert rows == [
        (1, "B", 8, {"word": "GSDU", "votes": 5, "bonus": 3}, 11),  # 2.5 rounds up
        (2, "A", 4, {"word": "RUNS", "votes": 10, "bonus": 5}, 9),
        (3, "C", 3, None, 3),
    ]


# --- results ---

def test_results_places_ties_and_missed() -> None:
    m = make_match(players="CAB")
    play(m, "C", "GEAR")  # 4
    play(m, "A", "DEER")  # 4
    res = match.results(match.finish(m, PLAY))
    assert [(p["place"], p["nickname"]) for p in res["players"]] == [(1, "A"), (1, "C"), (3, "B")]
    assert res["total_words"] == 9
    assert res["missed_words"] == ["DEARER", "GENRES", "NEARER", "RERUNS", "AMAM", "NEAR", "RUNE"]
    assert res["pct"][4][0] == 50 and res["players"][0]["user_id"] == "A"
    assert "ballot" not in str(res)
