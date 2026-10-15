"""Settings read from environment variables (docs/architecture.md §5).

Tests build Settings(...) directly. Multiplayer: the lobby's settings came with step M4, the
API's (submit gap, queue-join limit, tick, keepalive) with M5 (docs/multiplayer.md §6.7).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("./var")
    lexicon_dir: Path = Path("./data")
    web_dir: Path = Path("./web")
    daily_json_source: str = "./data/daily_challenges.json"  # a local path or an https:// URL
    daily_fetch_timeout_s: float = 5.0
    daily_fetch_max_bytes: int = 1_048_576
    allowed_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000")
    cookie_secure: bool = True
    hsts: bool = False
    trust_fly_client_ip: bool = False
    debug: bool = False
    max_body_bytes: int = 16_384
    free_games_per_day: int = 20
    min_free_words: int = 12  # from the step-1 spike (docs/spike-report.md)
    max_free_words: int = 74  # the spike's p90, so a lucky draw can't dominate the boards; 0 = off
    submit_min_gap_s: float = 1.0
    rl_new_players_per_ip_hour: int = 5  # rate limits: 0 = off
    rl_games_per_player_hour: int = 30
    rl_events_per_player_s: int = 1
    rl_requests_per_ip_min: int = 300
    event_retention_days: int = 90
    board_limit: int = 100
    plaus_max_wpm: float = 20.0  # plausibility: provisional until real play data (docs/spike-report.md)
    plaus_soft_wpm: float = 8.0
    plaus_min_cells: int = 49
    plaus_min_words: int = 5
    plaus_found_ratio: float = 0.9
    match_min_players: int = 2  # multiplayer lobby (docs/multiplayer.md §6.7)
    match_max_players: int = 6
    match_fill_s: float = 10.0  # fill window after the 2nd player joins
    match_queue_timeout_s: float = 120.0
    match_stream_gone_s: float = 10.0  # queued: dropped after this without a stream; voting: counts as done
    match_max_active: int = 8  # forming, countdown, running or voting
    match_max_queue: int = 24
    min_match_words: int = 60  # from step M2 (docs/spike-report.md)
    match_submit_min_gap_s: float = 0.5  # shared by submit and showcase word; lower than solo's, it's a race
    rl_queue_joins_per_player_hour: int = 30  # 0 = off
    match_tick_ms: int = 200  # 0 = no ticker: in-process tests call tick() themselves
    sse_keepalive_s: float = 5.0  # a ": ping" after this long without an event

    def __post_init__(self) -> None:
        if self.daily_json_source.lower().startswith("http://"):
            raise ValueError("DAILY_JSON_SOURCE must use https (S10)")
        if self.max_free_words < 0 or 0 < self.max_free_words < self.min_free_words:
            raise ValueError("MAX_FREE_WORDS must be 0 (off) or at least MIN_FREE_WORDS")
        if not 2 <= self.match_min_players <= self.match_max_players <= 6:
            raise ValueError("a match has 2 to 6 players: 2 <= MATCH_MIN_PLAYERS <= MATCH_MAX_PLAYERS <= 6")
        if self.match_tick_ms < 0 or self.sse_keepalive_s <= 0:
            raise ValueError("MATCH_TICK_MS must be 0 (off) or more, and SSE_KEEPALIVE_S more than 0")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trur.db"


def _parse_bool(raw: str) -> bool:
    if raw in ("1", "true", "True"):
        return True
    if raw in ("0", "false", "False"):
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    values: dict[str, object] = {}
    for f in fields(Settings):
        raw = env.get(f.name.upper())
        if raw is None:
            continue
        if f.type is bool:
            values[f.name] = _parse_bool(raw)
        elif f.type is Path:
            values[f.name] = Path(raw)
        elif f.type == tuple[str, ...]:
            values[f.name] = tuple(o.strip() for o in raw.split(",") if o.strip())
        else:
            values[f.name] = f.type(raw)
    return Settings(**values)
