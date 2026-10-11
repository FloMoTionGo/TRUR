"""Settings read from environment variables (docs/architecture.md §5).

Tests build Settings(...) directly. Multiplayer settings are added with step M5.
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
    min_free_words: int = 8  # placeholder until the step-1 spike (tasks/todo.md T5)
    submit_min_gap_s: float = 1.0
    rl_new_players_per_ip_hour: int = 5  # rate limits: 0 = off
    rl_games_per_player_hour: int = 30
    rl_events_per_player_s: int = 1
    rl_requests_per_ip_min: int = 300
    event_retention_days: int = 90
    board_limit: int = 100
    plaus_max_wpm: float = 20.0  # plausibility thresholds: placeholders until the spike
    plaus_soft_wpm: float = 8.0
    plaus_min_cells: int = 49
    plaus_min_words: int = 5
    plaus_found_ratio: float = 0.9

    def __post_init__(self) -> None:
        if self.daily_json_source.lower().startswith("http://"):
            raise ValueError("DAILY_JSON_SOURCE must use https (S10)")

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
