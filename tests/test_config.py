from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trur.clock import SystemClock
from trur.config import Settings, load_settings
from trur.errors import ApiError


def test_defaults_are_the_local_column() -> None:
    s = load_settings({})
    assert s == Settings()
    assert s.db_path == Path("var/trur.db")
    assert s.allowed_origins == ("http://localhost:8000", "http://127.0.0.1:8000")
    assert (s.cookie_secure, s.hsts, s.trust_fly_client_ip, s.debug) == (True, False, False, False)


def test_fly_values_are_parsed() -> None:
    s = load_settings({
        "DATA_DIR": "/data",
        "DAILY_JSON_SOURCE": "https://raw.githubusercontent.com/FloMoTionGo/TRUR/main/data/daily_challenges.json",
        "ALLOWED_ORIGINS": "https://trur.fly.dev",
        "HSTS": "1",
        "TRUST_FLY_CLIENT_IP": "1",
        "SUBMIT_MIN_GAP_S": "0",
        "MIN_FREE_WORDS": "12",
        "MAX_FREE_WORDS": "80",
    })
    assert s.db_path == Path("/data/trur.db")
    assert s.allowed_origins == ("https://trur.fly.dev",)
    assert s.hsts and s.trust_fly_client_ip
    assert s.submit_min_gap_s == 0.0 and (s.min_free_words, s.max_free_words) == (12, 80)


def test_match_defaults_are_multiplayer_6_7() -> None:
    s = Settings()
    assert (s.match_min_players, s.match_max_players, s.match_max_active, s.match_max_queue) == (2, 6, 8, 24)
    assert (s.match_fill_s, s.match_queue_timeout_s, s.match_stream_gone_s) == (10, 120, 10)
    assert s.min_match_words == 60  # step M2 (docs/spike-report.md)
    s = load_settings({"MATCH_MIN_PLAYERS": "3", "MATCH_FILL_S": "2.5", "MIN_MATCH_WORDS": "1"})
    assert (s.match_min_players, s.match_fill_s, s.min_match_words) == (3, 2.5, 1)


@pytest.mark.parametrize("env", [
    {"HSTS": "yes"},
    {"MIN_FREE_WORDS": "many"},
    {"MAX_FREE_WORDS": "11"},  # below MIN_FREE_WORDS (12): no grid could qualify
    {"MAX_FREE_WORDS": "-1"},
    {"MATCH_MIN_PLAYERS": "1"},  # a match has 2–6 players (the matches.player_count CHECK)
    {"MATCH_MAX_PLAYERS": "7"},
    {"MATCH_MIN_PLAYERS": "4", "MATCH_MAX_PLAYERS": "3"},
])
def test_bad_values_fail_at_startup(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(env)


def test_max_free_words_defaults_to_the_spike_p90_and_0_switches_it_off() -> None:
    assert Settings().max_free_words == 74
    assert load_settings({"MAX_FREE_WORDS": "0", "MIN_FREE_WORDS": "500"}).max_free_words == 0


def test_http_daily_source_is_refused() -> None:
    with pytest.raises(ValueError, match="https"):
        load_settings({"DAILY_JSON_SOURCE": "http://example.com/daily.json"})


def test_system_clock_is_utc() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_fake_clock(clock) -> None:
    clock.advance(3600)
    assert clock.now() == datetime(2026, 10, 11, 13, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        clock.set(datetime(2026, 10, 11))


def test_api_error_carries_status_and_code() -> None:
    e = ApiError(429, "rate_limited", "Too many requests.", retry_after=3)
    assert (e.status, e.code, str(e), e.extra) == (429, "rate_limited", "Too many requests.", {"retry_after": 3})
