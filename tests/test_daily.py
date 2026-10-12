"""Daily challenge: import and fallback, the daily game, midnight, past answers (TS §3 step 5; S3, S10)."""

import asyncio
import io
import json
import logging
import random
import re
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.conftest import FIXTURES, FakeClock, LiveServer, make_client, make_settings
from tests.test_games import burst, live_player
from trur import auth, daily, db, games
from trur.api import router as api_router
from trur.app import create_app
from trur.config import Settings
from trur.errors import ApiError
from trur.solver import DailyEntry, Lexicon, pair_key, points

DAILY = FIXTURES / "daily"
OCT10, OCT11, OCT12 = date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)
MEDICAL_CORRECT = pair_key("MEDICAL", "CORRECT")
STRONG_MEANING = pair_key("STRONG", "MEANING")


def write_json(path: Path, entries: object) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A tmp copy of valid.json: 10-10 = STRONG × MEANING, 10-11 = MEDICAL × CORRECT."""
    return Path(shutil.copyfile(DAILY / "valid.json", tmp_path / "daily.json"))


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(db_path)
    yield c
    c.close()


def settings_for(db_path: Path, source: Path | str, **overrides: object) -> Settings:
    return make_settings(db_path.parent, daily_json_source=str(source), **overrides)


def stored(conn: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT date, word1, word2, picked_by FROM daily_challenges ORDER BY date")]


# --- import ---

def test_valid_entries_are_stored_as_workflow(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    entries = daily.import_daily(conn, lex, settings_for(db_path, source))
    assert entries == [DailyEntry(OCT10, "STRONG", "MEANING"), DailyEntry(OCT11, "MEDICAL", "CORRECT")]
    assert stored(conn) == [
        ("2026-10-10", "STRONG", "MEANING", "workflow"),
        ("2026-10-11", "MEDICAL", "CORRECT", "workflow"),
    ]


def test_a_stored_date_is_never_overwritten(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-11', 'STRONG', 'MEANING', 'fallback')")
    daily.import_daily(conn, lex, settings_for(db_path, source))
    assert ("2026-10-11", "STRONG", "MEANING", "fallback") in stored(conn)


def test_bad_entries_are_skipped_and_logged(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="trur"):
        entries = daily.import_daily(conn, lex, settings_for(db_path, DAILY / "mixed.json"))
    assert entries == [DailyEntry(OCT11, "MEDICAL", "CORRECT")]
    skipped = [r.getMessage() for r in caplog.records if "skipped" in r.getMessage()]
    assert skipped == [f"daily JSON entry {i} skipped: not a valid pair" for i in range(1, 7)]
    assert stored(conn) == [("2026-10-11", "MEDICAL", "CORRECT", "workflow")]


def failing_sources(tmp_path: Path) -> dict[str, tuple[str, dict]]:
    big = write_json(tmp_path / "big.json", [{"date": "2026-10-11", "word1": "MEDICAL", "word2": "CORRECT"}] * 50)
    return {
        "broken": (str(DAILY / "broken.json"), {}),
        "missing": (str(tmp_path / "nope.json"), {}),
        "over the cap": (str(big), {"daily_fetch_max_bytes": 1000}),
        "not an array": (str(write_json(tmp_path / "obj.json", {"date": "2026-10-11"})), {}),
    }


@pytest.mark.parametrize("case", ["broken", "missing", "over the cap", "not an array"])
def test_a_failing_source_is_logged_and_the_fallback_is_used(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture, case: str,
) -> None:
    path, overrides = failing_sources(tmp_path)[case]
    s = settings_for(db_path, path, **overrides)
    with caplog.at_level(logging.WARNING, logger="trur"):
        assert daily.import_daily(conn, lex, s) == []
        entry = daily.ensure_daily_pair(conn, lex, s, random.Random(0), OCT11)
    assert any(r.levelno == logging.ERROR and "could not be read" in r.getMessage() for r in caplog.records)
    assert stored(conn) == [("2026-10-11", entry.word1, entry.word2, "fallback")]


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


HTTPS = "https://raw.githubusercontent.com/FloMoTionGo/TRUR/main/data/daily_challenges.json"


def test_https_source_is_fetched_with_a_timeout(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_urlopen(url: str, timeout: float) -> FakeResponse:
        calls.append((url, timeout))
        return FakeResponse((DAILY / "valid.json").read_bytes())

    monkeypatch.setattr(daily.urllib.request, "urlopen", fake_urlopen)
    assert len(daily.import_daily(conn, lex, settings_for(db_path, HTTPS, daily_fetch_timeout_s=2.5))) == 2
    assert calls == [(HTTPS, 2.5)]


def test_a_fetch_timeout_is_logged_and_the_fallback_is_used(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def timeout(url: str, timeout: float) -> FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(daily.urllib.request, "urlopen", timeout)
    with caplog.at_level(logging.ERROR, logger="trur"):
        entry = daily.ensure_daily_pair(conn, lex, settings_for(db_path, HTTPS), random.Random(0), OCT11)
    assert "daily JSON could not be read: TimeoutError" in caplog.text
    assert stored(conn)[0][3] == "fallback" and entry.date == OCT11


# --- ensure_daily_pair ---

def test_ensure_imports_a_missing_date(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    s = settings_for(db_path, source)
    assert daily.ensure_daily_pair(conn, lex, s, random.Random(0), OCT11) == DailyEntry(OCT11, "MEDICAL", "CORRECT")
    entries = [{"date": "2026-10-12", "word1": "STRONG", "word2": "MEANING"}]
    write_json(source, entries)  # the JSON gains a date after the first import
    assert daily.ensure_daily_pair(conn, lex, s, random.Random(0), OCT12) == DailyEntry(OCT12, "STRONG", "MEANING")
    assert stored(conn)[-1] == ("2026-10-12", "STRONG", "MEANING", "workflow")


def test_fallback_avoids_pairs_in_the_db_and_the_json(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    write_json(source, [{"date": "2026-10-10", "word1": "MEDICAL", "word2": "CORRECT"}])
    s = settings_for(db_path, source)
    for seed in range(5):
        conn.execute("DELETE FROM daily_challenges")
        with caplog.at_level(logging.WARNING, logger="trur"):
            entry = daily.ensure_daily_pair(conn, lex, s, random.Random(seed), OCT11)
        assert (entry.word1, entry.word2) == ("STRONG", "MEANING")
        assert stored(conn)[-1] == ("2026-10-11", "STRONG", "MEANING", "fallback")
    assert "no daily pair for 2026-10-11; picked a fallback pair" in caplog.text


def test_planned_json_pairs_are_avoided_too(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    """Only the JSON knows MEDICAL × CORRECT (planned for a later date): the fallback must not take it."""
    write_json(source, [{"date": "2026-10-20", "word1": "MEDICAL", "word2": "CORRECT"}])
    for seed in range(5):
        conn.execute("DELETE FROM daily_challenges WHERE picked_by = 'fallback'")
        entry = daily.ensure_daily_pair(conn, lex, settings_for(db_path, source), random.Random(seed), OCT11)
        assert pair_key(entry.word1, entry.word2) == STRONG_MEANING


def test_no_unused_pair_is_503(conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path) -> None:
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-09', 'STRONG', 'MEANING', 'workflow')")
    write_json(source, [{"date": "2026-10-10", "word1": "MEDICAL", "word2": "CORRECT"}])
    with pytest.raises(ApiError) as exc:
        daily.ensure_daily_pair(conn, lex, settings_for(db_path, source), random.Random(0), OCT11)
    assert exc.value.status == 503 and exc.value.code == "no_pair_available"
    assert daily.get_daily_pair(conn, OCT11) is None


def picks_alone(lex: Lexicon, db_path: Path, s: Settings, seed: int) -> frozenset[str]:
    conn = db.connect(db_path)
    try:
        entry = daily.ensure_daily_pair(conn, lex, s, random.Random(seed), OCT11)
        conn.execute("DELETE FROM daily_challenges")
        return pair_key(entry.word1, entry.word2)
    finally:
        conn.close()


def test_fallback_race_stores_one_pair(lex: Lexicon, db_path: Path, source: Path) -> None:
    s = settings_for(db_path, write_json(source, []))
    picks = {seed: picks_alone(lex, db_path, s, seed) for seed in range(20)}
    seed_a = next(k for k, v in picks.items() if v == MEDICAL_CORRECT)
    seed_b = next(k for k, v in picks.items() if v == STRONG_MEANING)  # alone, the two seeds pick different pairs

    barrier = threading.Barrier(2)
    results: dict[int, DailyEntry] = {}

    def run(seed: int) -> None:
        conn = db.connect(db_path)
        try:
            barrier.wait()
            results[seed] = daily.ensure_daily_pair(conn, lex, s, random.Random(seed), OCT11)
        finally:
            conn.close()

    threads = [threading.Thread(target=run, args=(seed,)) for seed in (seed_a, seed_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    conn = db.connect(db_path)
    try:
        assert len(stored(conn)) == 1
    finally:
        conn.close()
    assert results[seed_a] == results[seed_b]


# --- startup ---

def startup_rows(db_path: Path, clock: FakeClock, source: Path) -> list[tuple]:
    with make_client(create_app(settings_for(db_path, source), clock)):
        pass
    conn = db.connect(db_path)
    try:
        return stored(conn)
    finally:
        conn.close()


def test_startup_imports_when_today_or_tomorrow_is_missing(db_path: Path, clock: FakeClock, source: Path) -> None:
    assert [r[0] for r in startup_rows(db_path, clock, source)] == ["2026-10-10", "2026-10-11"]


def test_startup_skips_the_import_when_both_days_are_stored(
    db_path: Path, clock: FakeClock, source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(db_path)
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-11', 'MEDICAL', 'CORRECT', 'workflow')")
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-12', 'STRONG', 'MEANING', 'workflow')")
    conn.close()
    calls: list[object] = []
    monkeypatch.setattr(daily, "import_daily", lambda *a: calls.append(a) or [])
    startup_rows(db_path, clock, source)
    assert calls == []


def test_a_broken_source_doesnt_stop_startup(db_path: Path, clock: FakeClock) -> None:
    assert startup_rows(db_path, clock, DAILY / "broken.json") == []


# --- the daily game (T21) ---

GAMES = "/api/games"
DAILY_START = {"mode": "daily"}
# MEDICAL across (columns), CORRECT down (rows); tile [r, c] = MEDICAL[c] + CORRECT[r]
DEMOCRATIC = [[4, 2], [1, 0], [2, 4], [6, 5], [0, 3]]  # DE MO CR AT IC
DEMOCRATICLO = DEMOCRATIC + [[1, 6]]  # + LO
WRONG = [[[0, 0], [0, 1]], [[0, 0], [1, 0]], [[0, 2], [0, 3]]]  # MCMO, MCEC, DCIC: not words
Player = Callable[[str], TestClient]


@pytest.fixture
def settings(db_path: Path, source: Path) -> Settings:
    """The app fixtures read the tmp JSON copy, so tests can change it."""
    return settings_for(db_path, source)


@pytest.fixture
def live_overrides(request: pytest.FixtureRequest, tmp_path: Path) -> dict[str, object]:
    """parametrize("live_overrides", ["empty json"], indirect=True) gives the live server a JSON with no pairs."""
    if getattr(request, "param", None) == "empty json":
        return {"daily_json_source": str(write_json(tmp_path / "empty.json", []))}
    return {}


def start_daily(c: TestClient, status: int = 201) -> dict:
    r = c.post(GAMES, json=DAILY_START)
    assert r.status_code == status, r.text
    return r.json()


def submit(c: TestClient, game: dict, tiles: list) -> httpx.Response:
    return c.post(f"{GAMES}/{game['game_id']}/submit", json={"tiles": tiles})


def game_rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT id, mode, daily_date, ended_at IS NOT NULL, score FROM games").fetchall()
    finally:
        conn.close()


def test_daily_start_gives_todays_pair(player: Player) -> None:
    game = start_daily(player("Alice"))
    assert (game["mode"], game["daily_date"], game["word1"], game["word2"]) == ("daily", "2026-10-11", "MEDICAL", "CORRECT")
    assert game["missed_words"] is None and game["won"] is False


def test_a_second_daily_start_returns_the_same_game(player: Player, db_path: Path) -> None:
    alice = player("Alice")
    game = start_daily(alice)
    assert start_daily(alice, 200)["game_id"] == game["game_id"]
    assert alice.post(f"{GAMES}/{game['game_id']}/end", json={}).status_code == 200
    again = start_daily(alice, 200)  # also once it's over
    assert again["game_id"] == game["game_id"] and again["game_over"] is True and again["missed_words"] is None
    assert len(game_rows(db_path)) == 1


def test_the_client_cant_choose_the_day(player: Player, db_path: Path) -> None:
    r = player("Alice").post(GAMES, json={"mode": "daily", "daily_date": "2026-10-10"})
    assert r.status_code == 422 and game_rows(db_path) == []


def test_two_players_get_the_same_pair(player: Player) -> None:
    a, b = start_daily(player("Alice")), start_daily(player("Bob"))
    assert (a["word1"], a["word2"]) == (b["word1"], b["word2"]) and a["game_id"] != b["game_id"]


def test_daily_and_free_games_live_side_by_side(player: Player) -> None:
    alice = player("Alice")
    daily_game = start_daily(alice)
    free = alice.post(GAMES, json={"mode": "free"}).json()
    assert alice.get(f"{GAMES}/current?mode=daily").json()["game"]["game_id"] == daily_game["game_id"]
    assert alice.get(f"{GAMES}/current?mode=free").json()["game"]["game_id"] == free["game_id"]


def test_a_win_word_sets_won_and_play_continues(player: Player) -> None:
    alice = player("Alice")
    result = submit(alice, start_daily(alice), DEMOCRATIC).json()
    assert (result["status"], result["word"], result["points"]) == ("accepted", "DEMOCRATIC", 25)
    assert result["won"] is True and result["game_over"] is False
    bob = player("Bob")
    result = submit(bob, start_daily(bob), DEMOCRATICLO).json()
    assert (result["word"], result["points"], result["won"]) == ("DEMOCRATICLO", 36, True)


def test_daily_answers_stay_hidden(player: Player, lex: Lexicon) -> None:
    alice = player("Alice")
    game = start_daily(alice)
    bodies = [submit(alice, game, DEMOCRATIC).text]
    bodies += [submit(alice, game, tiles).text for tiles in WRONG]  # the third ends the game
    bodies.append(alice.get(f"{GAMES}/current?mode=daily").text)
    bodies.append(alice.post(GAMES, json=DAILY_START).text)
    final = json.loads(bodies[-1])
    assert final["game_over"] is True and final["missed_words"] is None
    unfound = [w for w in lex.solve("MEDICAL", "CORRECT") if w != "DEMOCRATIC"]
    assert unfound
    for body in bodies:
        assert not [w for w in unfound if w in body.replace("DEMOCRATIC", "")]


def test_a_daily_end_carries_no_missed_words(player: Player) -> None:
    alice = player("Alice")
    game = start_daily(alice)
    ended = alice.post(f"{GAMES}/{game['game_id']}/end", json={}).json()
    assert ended["game_over"] is True and ended["missed_words"] is None


# --- midnight ---

def test_a_submit_just_before_midnight_counts(player: Player, clock: FakeClock) -> None:
    alice = player("Alice")
    game = start_daily(alice)
    clock.set(datetime(2026, 10, 11, 23, 59, 59, 999999, tzinfo=timezone.utc))
    assert submit(alice, game, DEMOCRATIC).json()["status"] == "accepted"


def test_a_submit_at_midnight_closes_the_game(player: Player, clock: FakeClock, db_path: Path) -> None:
    alice = player("Alice")
    game = start_daily(alice)
    assert submit(alice, game, DEMOCRATIC).status_code == 200
    clock.set(datetime(2026, 10, 12, tzinfo=timezone.utc))
    r = submit(alice, game, WRONG[0])
    assert r.status_code == 409 and r.json()["error"]["code"] == "day_closed"
    assert game_rows(db_path) == [(game["game_id"], "daily", "2026-10-11", 1, 25)]  # closed, score kept
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT wrong_count FROM games").fetchone() == (0,)  # no try used
    finally:
        conn.close()


def test_a_game_started_just_before_midnight_keeps_its_day(player: Player, clock: FakeClock) -> None:
    alice = player("Alice")
    clock.set(datetime(2026, 10, 11, 23, 59, 59, tzinfo=timezone.utc))
    assert start_daily(alice)["daily_date"] == "2026-10-11"


def test_the_next_day_brings_a_new_game_with_the_new_pair(player: Player, clock: FakeClock, source: Path) -> None:
    alice = player("Alice")
    first = start_daily(alice)
    entries = json.loads(source.read_text(encoding="utf-8"))
    write_json(source, entries + [{"date": "2026-10-12", "word1": "STRONG", "word2": "MEANING"}])
    clock.set(datetime(2026, 10, 12, tzinfo=timezone.utc))
    second = start_daily(alice)
    assert second["game_id"] != first["game_id"]
    assert (second["daily_date"], second["word1"], second["word2"]) == ("2026-10-12", "STRONG", "MEANING")


# --- free play keeps away from today's words ---

def new_user(conn: sqlite3.Connection, clock: FakeClock) -> str:
    auth.create_user(conn, "Alice", clock.now())
    return conn.execute("SELECT id FROM users").fetchone()[0]


@pytest.mark.parametrize("json_only", [False, True])
def test_free_play_never_uses_todays_daily_words(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path, clock: FakeClock, json_only: bool,
) -> None:
    s = settings_for(db_path, source, free_games_per_day=50)  # one player, 50 starts in one day
    user_id = new_user(conn, clock)
    for seed in range(50):
        if json_only:
            conn.execute("DELETE FROM daily_challenges WHERE date = '2026-10-11'")  # only the JSON has today
        state, _ = games.create_game(conn, lex, s, random.Random(seed), user_id, "free", clock.now())
        assert not {state["word1"], state["word2"]} & {"MEDICAL", "CORRECT"}, seed


def test_free_play_works_without_a_daily_pair(
    conn: sqlite3.Connection, lex: Lexicon, db_path: Path, source: Path, clock: FakeClock,
) -> None:
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-01', 'STRONG', 'MEANING', 'workflow')")
    conn.execute("INSERT INTO daily_challenges VALUES ('2026-10-02', 'MEDICAL', 'CORRECT', 'workflow')")
    s = settings_for(db_path, write_json(source, []))  # no unused pair left for today
    user_id = new_user(conn, clock)
    state, created = games.create_game(conn, lex, s, random.Random(0), user_id, "free", clock.now())
    assert created and state["mode"] == "free"
    with pytest.raises(ApiError) as exc:
        games.create_game(conn, lex, s, random.Random(0), user_id, "daily", clock.now())
    assert exc.value.status == 503


# --- parallel starts on the live server ---

def test_one_players_parallel_daily_starts_make_one_game(live_server: LiveServer, db_path: Path) -> None:
    headers = live_player(live_server.base_url, "Alice")
    responses = burst(live_server.base_url, headers, GAMES, DAILY_START, 2)
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert len({r.json()["game_id"] for r in responses}) == 1
    assert len(game_rows(db_path)) == 1


@pytest.mark.parametrize("live_overrides", ["empty json"], indirect=True)
def test_two_players_racing_for_a_fallback_get_one_pair(live_server: LiveServer, db_path: Path) -> None:
    """No JSON pair for today, two players start at once: one fallback row, the same pair for both."""
    heads = [live_player(live_server.base_url, nick) for nick in ("Alice", "Bob")]

    async def run() -> list[httpx.Response]:
        async with httpx.AsyncClient(base_url=live_server.base_url, timeout=30) as c:
            return await asyncio.gather(*(c.post(GAMES, json=DAILY_START, headers=h) for h in heads))

    responses = asyncio.run(run())
    assert [r.status_code for r in responses] == [201, 201]
    assert len({(r.json()["word1"], r.json()["word2"]) for r in responses}) == 1
    conn = db.connect(db_path)
    try:
        assert [r[3] for r in stored(conn)] == ["fallback"]
    finally:
        conn.close()


# --- past answers (T22) ---

def answers_url(day: str) -> str:
    return f"/api/daily/{day}/answers"


@pytest.mark.parametrize("day", ["2026-10-11", "2026-10-12", "2027-01-01"])
def test_answers_for_today_or_later_are_403(client: TestClient, day: str) -> None:
    r = client.get(answers_url(day))
    assert r.status_code == 403 and r.json()["error"]["code"] == "day_not_over"


def test_answers_for_a_past_day_list_every_word(client: TestClient, lex: Lexicon) -> None:
    r = client.get(answers_url("2026-10-10"))
    assert r.status_code == 200
    body = r.json()
    assert (body["date"], body["word1"], body["word2"]) == ("2026-10-10", "STRONG", "MEANING")
    expected = sorted(lex.solve("STRONG", "MEANING"), key=lambda w: (-points(w), w))
    assert [w["word"] for w in body["words"]] == expected and body["total_words"] == len(expected)
    assert body["words"][0] == {"word": "GENERATION", "points": 25}
    assert body["found"] is None  # no cookie


def test_answers_add_the_callers_own_finds(player: Player, clock: FakeClock) -> None:
    alice, bob = player("Alice"), player("Bob")
    game = start_daily(alice)
    submit(alice, game, DEMOCRATIC)
    submit(alice, game, WRONG[0])
    clock.set(datetime(2026, 10, 12, tzinfo=timezone.utc))  # the day is over
    assert alice.get(answers_url("2026-10-11")).json()["found"] == ["DEMOCRATIC"]
    assert bob.get(answers_url("2026-10-11")).json()["found"] is None  # Bob didn't play


@pytest.mark.parametrize("day", ["2026-02-30", "x", "20261010", "2026-10-1", "1700000000", "2026-10-10T00:00"])
def test_a_bad_date_is_422(client: TestClient, day: str) -> None:
    r = client.get(answers_url(day))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_a_past_day_without_a_pair_is_404(client: TestClient) -> None:
    r = client.get(answers_url("2026-09-01"))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_no_route_leaks_todays_unfound_words(player: Player, client: TestClient, lex: Lexicon) -> None:
    """With today's daily game open, call every API route; no body may hold a word not yet found."""
    alice = player("Alice")
    game = start_daily(alice)
    gid = game["game_id"]
    event = {"events": [{"type": "selection_cleared", "game_id": gid, "client_ms": 0}]}
    yesterday = set(lex.solve("STRONG", "MEANING"))  # yesterday's answers are public
    calls: dict[tuple[str, str], list[httpx.Response]] = {
        ("GET", "/healthz"): [alice.get("/healthz")],
        ("POST", "/api/users"): [client.post("/api/users", json={"nickname": "Bob"}), alice.post("/api/users", json={"nickname": "Eve"})],
        ("GET", "/api/users/me"): [alice.get("/api/users/me")],
        ("POST", "/api/games"): [alice.post(GAMES, json=DAILY_START)],
        ("GET", "/api/games/current"): [alice.get(f"{GAMES}/current?mode=daily"), alice.get(f"{GAMES}/current?mode=free")],
        ("POST", "/api/games/{game_id}/submit"): [submit(alice, game, DEMOCRATIC), submit(alice, game, WRONG[0])],
        ("POST", "/api/events"): [alice.post("/api/events", json=event)],
        ("GET", "/api/daily/{day}/answers"): [alice.get(answers_url("2026-10-11")), alice.get(answers_url("2026-10-10"))],
        ("POST", "/api/games/{game_id}/end"): [alice.post(f"{GAMES}/{gid}/end", json={})],  # last: it ends the game
    }
    routes = {(m, r.path) for r in api_router.routes if isinstance(r, APIRoute) for m in r.methods}
    assert set(calls) == routes  # a new route must join this sweep

    unfound = set(lex.solve("MEDICAL", "CORRECT")) - {"DEMOCRATIC"}
    for (method, path), responses in calls.items():
        for r in responses:
            assert r.status_code < 500
            public = yesterday if "2026-10-10" in str(r.url) else set()
            words = set(re.findall(r"[A-Z]{4,}", r.text))
            assert not (words & unfound) - public, (method, path)
