# TRUR – Architecture

How [plan.md](plan.md) gets built.
- The plan decides **what** the game does; this file decides **how** it is built.
- If the two disagree, the plan wins and this file gets fixed.

Drafted on 2026-10-11 from an architecture review, with the owner's decisions applied.

**Multiplayer** (built after go-live) is designed in [multiplayer.md](multiplayer.md). This file lists its modules, routes, settings and schema change so the inventory stays complete, and links there for the details.

**Facts checked on the dev PC:**
- The git remote is `https://github.com/FloMoTionGo/TRUR`.
- Python is 3.12 with SQLite 3.49.1, so STRICT tables and every authorizer constant used here are available.
- `python -m sqlite3` works, so no separate `sqlite3` program is needed.

## 1. Repository layout

```text
TRUR/
├─ README.md, requirements             docs (README gets the Claude Design link + run/deploy notes)
├─ docs/plan.md, docs/architecture.md, docs/test-strategy.md, docs/multiplayer.md
├─ pyproject.toml          project metadata + [tool.pytest.ini_options] pythonpath=["."]; not a build
├─ requirements.in         runtime top-level deps: fastapi, uvicorn
├─ requirements.txt        hashed lock (pip-compile --generate-hashes); the only thing Docker installs
├─ requirements-dev.in     -r requirements.in, -c requirements.txt, test + tooling deps (§6)
├─ requirements-dev.txt    hashed dev lock
├─ Dockerfile              python:3.12-slim, installs the lock, runs uvicorn with 1 worker
├─ .dockerignore           .git, var/, *.db*, .venv/, tests/, build/  (S9: the DB never gets into the image)
├─ fly.toml                app trur / fra / trur_data→/data / http_service / checks / concurrency
├─ .gitignore              var/, *.db, *.db-wal, *.db-shm, .venv/, __pycache__/, .pytest_cache/, build/
├─ trur/                   backend package
│  ├─ __init__.py          empty, so `import trur.solver` never pulls in FastAPI
│  ├─ clock.py             Clock protocol + SystemClock: the only place that reads the system time
│  ├─ solver.py            lexicon, grid, tile→word rebuild, scoring, solver, pair picking, daily-JSON validation (stdlib only)
│  ├─ config.py            Settings dataclass read from env
│  ├─ errors.py            ApiError(status, code, message)
│  ├─ db.py                connections, authorizer, migrations, tx(), words-table sync, event purge
│  ├─ auth.py              token/cookie, nickname rules + folding, user lookup
│  ├─ daily.py             fetch/import daily JSON, ensure-today-has-a-pair + fallback, past answers
│  ├─ games.py             game service: create/resume/submit/end/sweep, plausibility, event log
│  ├─ match.py             multiplayer rules engine: tile %, points, locks, duplicates, time window, display words, vote and bonus (stdlib only)
│  ├─ lobby.py             multiplayer queue, matches, Hub (SSE fan-out), tick()/run() (stdlib + asyncio)
│  ├─ rankings.py          ranking recompute + board queries
│  ├─ security.py          body cap, security headers, JSON/Origin check, RateLimiter, client IP
│  ├─ api.py               routes, Pydantic models, dependencies, exception handlers
│  └─ app.py               create_app() factory, lifespan, middleware, static mount of web/
├─ web/                    the only static root (S9)
│  ├─ index.html           single page, no inline script/style (CSP)
│  ├─ css/app.css          tokens + layout from the approved Claude Design
│  ├─ js/main.js           ES-module entry: boot, /api/users/me, screen switching
│  ├─ js/api.js            fetch wrapper (JSON, same-origin, parses the error envelope)
│  ├─ js/game.js           selection/undo/clear, submit, reject animation (textContent only)
│  ├─ js/grid.js           grid render + sizing (fixed on desktop, measured on phones), shared with match.js (mobile.md §5)
│  ├─ js/sheet.js          <dialog> bottom sheet for phones: found list, End confirm (mobile.md §6.3–6.4)
│  ├─ js/boards.js         monthly + daily boards, past answers view
│  ├─ js/events.js         batches tile events (≤50, ≥1 s apart)
│  ├─ js/match.js          multiplayer: queue, countdown, match, display word, vote, results, results link
│  ├─ js/stream.js         EventSource wrapper for /api/match/stream
│  ├─ img/logo.svg, img/logo-compact.svg, favicon.svg (+ PNG home-screen icons, mobile.md §6.9)
│  ├─ manifest.webmanifest optional home-screen install (P17)
│  └─ fonts/*.woff2        self-hosted
├─ data/                   committed, public, never holds the DB
│  ├─ words.txt            full filtered en_CA dictionary, UPPERCASE, one per line (generated)
│  ├─ source_words.txt     500 source words; line n = freq_rank n (generated)
│  ├─ blocklist.txt        offensive words (dictionary filter + nickname check), hand-curated
│  ├─ daily_challenges.json  [{date, word1, word2}, …], written only by pick_daily
│  └─ README.md            provenance: SCOWL version/size/licence notice, wordfreq version, rebuild command
├─ scripts/                run as `python -m scripts.<name>` from repo root
│  ├─ __init__.py
│  ├─ build_wordlists.py   SCOWL file (in build/, gitignored) + wordfreq → data/words.txt, source_words.txt
│  ├─ spike_stats.py       step-1 report: words per pair, share of pairs with a 10+-letter word
│  ├─ pick_daily.py        workflow script; imports only trur.solver and trur.clock (stdlib), so Actions installs nothing
│  ├─ admin.py             hide/unhide, flag/unflag (+ ranking recompute), delete-player; via `fly ssh console`
│  └─ mobile_shots.py      screenshots per phone device, screen and theme for design review (P15)
├─ tests/                  see docs/test-strategy.md
│  ├─ conftest.py, fixtures/ (tiny words, source list, blocklist, daily JSON variants)
│  └─ test_solver.py, test_games.py, test_auth.py, test_daily.py, test_rankings.py, test_security.py,
│     test_pick_daily.py, test_static.py, test_match.py, test_lobby.py, test_match_api.py, e2e/, smoke/
└─ .github/workflows/
   ├─ tests.yml            push/PR → pytest + pip-audit; permissions: contents: read; SHA-pinned actions
   └─ daily-challenge.yml  cron '17 21 * * *' + workflow_dispatch (input dry_run); job-level contents: write;
                           SHA-pinned; concurrency group; setup-python 3.12 → `python -m scripts.pick_daily`
                           → commit as github-actions[bot] if data/daily_challenges.json changed (pull --rebase, push)
```

## 2. Backend modules

**Dependency rule:** only `api.py`, `app.py` and `security.py` import FastAPI, Starlette or Pydantic. Everything else is stdlib-only (`lobby.py` uses `asyncio`, which is stdlib). That keeps the solver, `pick_daily.py` and `admin.py` free of dependencies.

```text
clock   solver ◄──────────── scripts/pick_daily
config   errors
db ◄─ (config, solver.Lexicon type, match.MatchRecord type)
auth ◄─ db, errors           rankings ◄─ db            ◄── scripts/admin
daily ◄─ solver, db, errors, config, clock
games ◄─ solver, daily, rankings, db, errors, config, clock
match ◄─ solver                                         (multiplayer)
lobby ◄─ match, solver, errors, config, clock           (multiplayer)
security ◄─ config, errors, clock
api ◄─ auth, games, daily, rankings, lobby, security, db, errors
app ◄─ api, security, db, daily, games, lobby, solver, config, clock
```

```python
# trur/clock.py
class Clock(Protocol):
    def now(self) -> datetime: ...            # timezone-aware UTC
class SystemClock:                            # production; tests use FakeClock(set/advance)
    def now(self) -> datetime: ...

# trur/solver.py  (stdlib only, shared with scripts/)
MIN_TILES = 2; MAX_TRIES = 3; WIN_LETTERS = 10
Tile = tuple[int, int]            # (row → word2 index, col → word1 index)
class Lexicon:
    words: frozenset[str]         # UPPERCASE A–Z
    source: tuple[str, ...]       # index i ⇒ freq_rank i+1
    max_tiles: int                # longest even word // 2
    def solve(self, word1: str, word2: str) -> tuple[str, ...]  # LRU-cached (256 pairs)
def load_lexicon(data_dir: Path) -> Lexicon        # asserts source ⊆ words
def word_from_tiles(word1: str, word2: str, tiles: Sequence[Tile]) -> str  # ValueError: off-grid/repeat/<2/>max_tiles
def points(word: str) -> int                        # (len(word) // 2) ** 2
def is_win_word(word: str) -> bool                  # len(word) >= WIN_LETTERS (decided: 10 or more)
def pair_key(a: str, b: str) -> frozenset[str]
def pick_pair(lex: Lexicon, rng: random.Random,
              accept: Callable[[str, str, tuple[str, ...]], bool],
              max_attempts: int = 5000) -> tuple[str, str, tuple[str, ...]]
    # draws two distinct source words AND their orientation first, then calls accept(word1, word2, answers)
    # on that exact orientation (the grid is not symmetric); LookupError after max_attempts
class DailyEntry(NamedTuple): date: datetime.date; word1: str; word2: str
def parse_daily_json(raw: bytes) -> list[object]   # must be a top-level list, else ValueError
def validate_daily_entry(lex: Lexicon, item: object) -> DailyEntry | None
    # ISO date, both source words, distinct, and the stored orientation has a win word
```

**How the solver works:**
- At load time, each dictionary word of even length ≥ 4 is precomputed as `(word, odd_letter_mask, even_letter_mask, pair_counts)`.
- `solve()` builds `grid[a+b] = count1[a] * count2[b]`. It prefilters with two 26-bit mask tests, then checks `pair_counts ≤ grid`. This stays well under 50 ms in pure Python.

**Free play and daily use the same picker** with different `accept` rules:
- Free play: neither word is one of today's daily words, and `MIN_FREE_WORDS ≤ len(answers) ≤ MAX_FREE_WORDS` (the upper bound only when `MAX_FREE_WORDS` isn't 0).
- Daily: `pair_key` hasn't been used before, and `any(is_win_word(w) for w in answers)`.

```python
# trur/config.py
@dataclass(frozen=True)
class Settings: ...            # fields = §5 table
    @property
    def db_path(self) -> Path: ...   # data_dir / "trur.db"
def load_settings(env: Mapping[str, str] = os.environ) -> Settings

# trur/db.py
MIGRATIONS: tuple[str, ...]                      # constant DDL; entry i ⇒ user_version i+1
def migrate(db_path: Path) -> None               # own unrestricted conn: WAL + pending migrations only
def connect(db_path: Path) -> sqlite3.Connection # check_same_thread=False, isolation_level=None, Row,
                                                 # foreign_keys=ON, busy_timeout=5000, THEN set_authorizer(_authorizer)
def connect_ro(db_path: Path) -> sqlite3.Connection  # "file:…?mode=ro" via Path.as_uri(), uri=True, same flags + authorizer
@contextmanager
def tx(conn) -> Iterator[None]                   # BEGIN IMMEDIATE / COMMIT / ROLLBACK
def sync_words(conn, lex: Lexicon, files_sha256: str) -> None  # refill `words` only if meta hash differs
def purge_events(conn, older_than: datetime) -> int
def iso(dt: datetime) -> str                     # 'YYYY-MM-DDTHH:MM:SS.ffffffZ' (no deprecated adapters)
def save_match(conn, record: MatchRecord, now: datetime) -> None   # multiplayer: one tx (multiplayer.md §6.6)
def match_results(conn, match_id: str, user_id: str) -> dict       # snapshot + nicknames from users; 404 unless a player
```

The authorizer denies the following and allows everything else:
- every `SQLITE_CREATE_*` and `SQLITE_DROP_*` action
- `SQLITE_ALTER_TABLE`, `SQLITE_ATTACH` and `SQLITE_DETACH`
- `SQLITE_PRAGMA` with an argument, which means a write
- `SQLITE_FUNCTION` calls to `load_extension`

**`check_same_thread=False`:** FastAPI runs a sync dependency (such as `get_conn`) and the sync route that uses it as two separate threadpool calls, which can land on different threads. With the default `True`, `sqlite3` would then raise `ProgrammingError`. Each connection is still used by only one request at a time and closed after it, so this is safe.

```python
# trur/auth.py
COOKIE = "trur_token"; COOKIE_MAX_AGE = 400 * 86400
def fold_nickname(nick: str) -> str               # lower(); 'i','1'→'l'; '0'→'o'  → FIoMo and FloMo both fold to "flomo"
def check_nickname(nick: str, blocklist: frozenset[str]) -> str  # ApiError 422 invalid_nickname|nickname_not_allowed
def load_blocklist(data_dir: Path) -> frozenset[str]
def hash_token(token: str) -> str                 # sha256 hex
def create_user(conn, nickname: str, now: datetime) -> str  # returns token (secrets.token_urlsafe(32)); 409 nickname_taken
def user_for_token(conn, token: str | None) -> sqlite3.Row | None  # id, nickname, nickname_key

# trur/daily.py
def import_daily(conn, lex, s: Settings) -> list[DailyEntry]   # read source (https URL via urllib, timeout, read(max+1) cap;
                                                   # or local path), validate each entry, INSERT OR IGNORE 'workflow';
                                                   # never raises; logs skipped entries; returns the valid entries, which the fallback avoids
def ensure_daily_pair(conn, lex, s, rng, day: date) -> DailyEntry  # process Lock: SELECT → import → fallback
                                                   # (pick_pair avoiding DB ∪ JSON pairs) INSERT OR IGNORE 'fallback'
                                                   # + warning → re-SELECT; 503 no_pair_available if pick fails
def get_daily_pair(conn, day: date) -> DailyEntry | None
def daily_answers(conn, lex, user_id: str | None, day: date, today: date) -> dict  # 403 day_not_over / 404

# trur/rankings.py
class BoardRow(TypedDict): rank: int; nickname: str; score: int
def periods(game: sqlite3.Row) -> list[str]       # free: [month of started_at]; daily: [month of daily_date, daily_date]
def recompute_ranking(conn, user_id: str, mode: str, period: str) -> None
    # MAX(score) over ended, unflagged games of that user/mode/period → upsert; delete row if none
def monthly_board(conn_ro, mode: str, month: str, limit: int) -> list[BoardRow]
def daily_board(conn_ro, day: date, limit: int) -> list[BoardRow]

# trur/games.py
Mode = Literal["free", "daily"]
EndReason = Literal["player", "tries", "all_found", "replaced", "day_closed"]
class GameState(TypedDict): ...   # §3
class SubmitResult(TypedDict): ...# §3
def create_game(conn, lex, s, rng, user_id: str, mode: Mode, now: datetime) -> tuple[GameState, bool]  # bool = created
def current_game(conn, lex, s, user_id: str, mode: Mode, now: datetime) -> GameState | None
def submit(conn, lex, s, user_id: str, game_id: str, tiles: list[Tile], now: datetime) -> SubmitResult
def end_by_player(conn, lex, s, user_id: str, game_id: str, now: datetime) -> GameState
def sweep_abandoned(conn, lex, s, now: datetime, user_id: str | None = None) -> int
    # closes, with reason day_closed and the current score:
    #   open daily games with daily_date < today, and open free games whose started_at date < today (UTC)
def log_client_events(conn, user_id: str, events: list[dict], now: datetime) -> None  # 404 if any game not owned
def is_implausible(found: int, total: int, cells: int, minutes: float, s: Settings) -> bool  # §8 item 9
def pick_match_pair(conn, lex, s, rng, today: date) -> tuple[str, str, tuple[str, ...]]
    # a multiplayer board: ≥ MIN_MATCH_WORDS answers, no daily word of today or tomorrow (multiplayer.md §3.5)
# private: _load_owned (→404), _day_over(game, today), _end(conn, lex, s, game, reason, now)
#          [UPDATE ended_at+flagged, recompute_ranking for each period, log game_ended], _state, _log_event

# trur/security.py
class RateLimiter:   # in-memory sliding window + threading.Lock; single process only; time from Clock
    def check(self, key: str, limit: int, window_s: float) -> None  # ApiError 429 rate_limited + retry_after; limit 0 = off
def client_ip(request: Request, trust_fly: bool) -> str   # Fly-Client-IP if trusted else socket peer; never stored
class BodySizeLimit:      # pure ASGI: Content-Length and streamed bytes ≤ MAX_BODY_BYTES else 413
class SecurityHeaders:    # CSP "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                          # nosniff, Referrer-Policy no-referrer, HSTS if enabled, Cache-Control no-store on /api.
                          # Pure ASGI like BodySizeLimit: BaseHTTPMiddleware wraps streaming responses (SSE) in an extra
                          # task + memory stream, which hampers disconnect detection and cancellation
def check_post(request: Request, s: Settings) -> None  # every POST: content-type application/json (415), Origin ∈ allowed (403; missing = 403)

# trur/api.py: Pydantic v2 models (all extra="forbid", strict types), deps get_conn (per-request rw conn, closed after),
#   get_conn_ro, get_lexicon, get_player (→401), get_now (= app.state.clock.now()). All routes are plain `def`
#   (threadpool), because sqlite3 blocks. Exception: the multiplayer match routes are `async def` and never call
#   sqlite3 themselves; they read the time inside the handler (multiplayer.md §6.1).
# trur/app.py
def create_app(settings: Settings | None = None, clock: Clock | None = None,
               rng: random.Random | None = None) -> FastAPI
```

**`create_app`** builds the FastAPI app. `docs_url`, `redoc_url` and `openapi_url` are `None` unless `DEBUG` is on. The lifespan runs:
1. Check that SQLite is at least 3.37, which STRICT tables need.
2. Create `DATA_DIR`.
3. Load the lexicon and the blocklist.
4. Run `migrate`.
5. Run `sync_words`, then `purge_events(now − 90 days)`.
6. Run `import_daily`, but only if the database is missing today's or tomorrow's pair. This keeps cold starts fast.
7. Run `sweep_abandoned`.
8. Create the multiplayer `Hub` and `Lobby` and start `lobby.run()`, unless `MATCH_TICK_MS` is 0. On shutdown, stop it and wait up to 2 s for pending match saves, so the whole shutdown stays under Fly's default `kill_timeout` of 5 s.

Then it adds the middleware and the API router, and mounts `StaticFiles(web_dir, html=True)` at `/` last.

It runs with `uvicorn trur.app:create_app --factory --workers 1`. It has to be one worker, because the rate limiter, the daily lock and the multiplayer matches live in memory.

**Submit transaction (S5).** Before the transaction, the 1-second gap check runs in memory on the key `submit:{user}:{game}`. Then everything below happens inside one `with tx(conn):`
1. `_load_owned` loads the game, or returns 404 if it doesn't exist or isn't the player's.
2. If the game's day is over (daily: `daily_date < today`; free: the `started_at` date `< today`), `_end(reason="day_closed")` closes it and the outcome is `day_closed`.
3. Otherwise, if `ended_at` is already set, the outcome is `game_over`.
4. Otherwise `word_from_tiles` builds the word, or returns 422 `invalid_tiles`. Then exactly one of these applies:
   - **Duplicate:** the word is already a valid submission in this game. Nothing is stored and no try is used.
   - **Valid:** INSERT the submission and update the score. Set `won` if the game is daily and `is_win_word` is true. If the found count reaches `total_words`, end the game with reason `all_found`.
   - **Invalid:** INSERT with `is_valid=0` and add one to `wrong_count`. At 3, end the game with reason `tries`.
5. Log the event.

`day_closed` and `game_over` are raised **after** COMMIT, so the game stays closed.

**Creating a game.** It starts with `sweep_abandoned(user_id)` and `ensure_daily_pair(today)`. If no daily pair can be found (503), free play still starts, just without excluding today's words.
- **Free:** `pick_pair` runs outside the lock. Then one transaction:
  1. If the player already started 20 free games today, return 429 `limit_reached`.
  2. End any open free game with reason `replaced`.
  3. INSERT the new game.
- **Daily:** one transaction SELECTs the player's game for today. If it exists, return it with 200. Otherwise INSERT and return 201.

## 3. API contract

**Conventions:**
- Bodies are JSON. Every POST needs `Content-Type: application/json` and an allowed `Origin`.
- Bodies are capped at 16 KB. Unknown fields get 422, so `{"score": …}` is refused (S5).
- Words are UPPERCASE in responses.
- Game, user, event and answers endpoints identify the player by the cookie. Rankings, answers and healthz need no cookie.

**Cookie (S2):** `trur_token=<43 chars>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=34560000` (400 days).

**Error envelope.** Every non-2xx response has this shape, including validation errors and 500s:

```json
{"error": {"code": "game_over", "message": "This game is over."}}
```

- A 422 `invalid_request` may add `"fields": ["tiles"]`. It never echoes the input back.
- A 429 also sends `Retry-After`.
- No 4xx response changes stored data or costs a try. The one exception is `day_closed`, which closes the game.

**Error codes by status:**

| Status | Codes |
| --- | --- |
| 401 | `no_player` |
| 403 | `bad_origin`, `day_not_over` |
| 404 | `not_found` |
| 409 | `already_registered`, `nickname_taken`, `game_over`, `day_closed`; multiplayer: `in_match`, `match_not_running`, `tile_locked`, `match_not_voting`, `already_voted` |
| 413 | `payload_too_large` |
| 415 | `unsupported_media_type` |
| 422 | `invalid_request`, `invalid_tiles`, `invalid_nickname`, `nickname_not_allowed`; multiplayer: `invalid_ballot` |
| 429 | `rate_limited`, `limit_reached` |
| 503 | `no_pair_available`; multiplayer: `server_busy` |
| 500 | `internal_error` |

**Shared schemas:**

```jsonc
// GameState: returned by POST /api/games, GET /api/games/current, POST …/end
{"game_id":"uuid4","mode":"free|daily","daily_date":"YYYY-MM-DD|null",
 "word1":"GARDEN","word2":"SUMMER",              // word1 = columns, word2 = rows
 "tries_left":0-3,"total_words":23,
 "found":[{"word":"GEAR","points":4}],           // submission order
 "found_count":1,"score":4,"won":false,          // won only ever true in daily
 "game_over":false,"missed_words":null}          // [str] only for free play once game_over
// SubmitResult: the plan's sketch fields + score, total_words
{"status":"accepted|duplicate|rejected","word":"GEAR","points":4,  // points 0 unless accepted
 "score":8,"tries_left":3,"found_count":2,"total_words":23,"won":false,
 "game_over":false,"missed_words":null}
// Answers (past day)
{"date":"2026-10-10","word1":"…","word2":"…","total_words":23,
 "words":[{"word":"NEARER","points":9}],          // points desc, then A–Z
 "found":["GEAR"]|null}                           // caller's own finds that day, if any
// Event (discriminated on "type"; extra fields forbidden)
{"type":"tile_clicked","game_id":"uuid","row":0-9,"col":0-9,"selected":true,"client_ms":0}
{"type":"selection_cleared","game_id":"uuid","client_ms":0}
// BoardRow
{"rank":1,"nickname":"FloMo","score":61}
```

**Endpoints.** Besides the errors listed, every POST can return 403 `bad_origin`, 413, 415 or 422 `invalid_request`, and any endpoint can return 429 `rate_limited`.

| Endpoint | Request | Success | Endpoint-specific errors |
| --- | --- | --- | --- |
| `POST /api/users` | `{"nickname": str}`, matching `^[A-Za-z0-9_-][A-Za-z0-9 _-]{1,14}[A-Za-z0-9_-]$` with no double spaces | 201 `{"nickname"}` + Set-Cookie | 409 `already_registered` (a valid cookie was sent), 409 `nickname_taken` (the folded key collides), 422 `invalid_nickname` / `nickname_not_allowed`, 429 (5 per hour per IP) |
| `GET /api/users/me` | – | 200 `{"nickname"}` | 401: the frontend shows the nickname prompt |
| `POST /api/games` | `{"mode":"free"\|"daily"}` | 201 GameState for a new game; 200 with the existing GameState if a daily game for today already exists, open or over | 401; 429 `limit_reached` for the 21st free game today, which leaves the open game untouched; 429 at 30 calls per hour per player; 503 `no_pair_available` |
| `GET /api/games/current?mode=` | `mode` required | 200 `{"game": GameState \| null}`. Only open games are returned; games from a past day are closed first | 401 |
| `POST /api/games/{id}/submit` | `{"tiles":[[row,col],…]}`: 2–50 integer pairs, each 0–9, no repeats | 200 SubmitResult. A duplicate returns `status:"duplicate"`, stores no row and costs no try | 401; 404 (unknown, malformed or someone else's game); 409 `game_over`; 409 `day_closed` (no try used, game closed); 422 `invalid_tiles` (off this grid or more than `max_tiles`); 429 if under 1 s since the last submit in this game (no try used) |
| `POST /api/games/{id}/end` | `{}` | 200 GameState with `game_over:true`; `missed_words` only in free play | 401, 404, 409 `game_over`, 409 `day_closed` |
| `GET /api/daily/{date}/answers` | ISO date in the path | 200 Answers | 403 `day_not_over` for today or later, whether or not a pair exists; 404 if no pair is stored; 422 |
| `POST /api/events` | `{"events":[Event, …]}`, 1–50 events | 204 | 401; 404 if any `game_id` isn't the player's (the whole batch is dropped); 429 (1 per second per player) |
| `GET /api/rankings?mode=&month=` | `mode` required; `month` is `YYYY-MM` and defaults to the current UTC month | 200 `{"mode","month","entries":[BoardRow]}`, the top `BOARD_LIMIT` | 422 |
| `GET /api/rankings/daily?date=` | ISO date, defaults to today (UTC) | 200 `{"date","entries":[BoardRow]}` | 422 |
| `GET /healthz` | – | 200 `{"ok":true}` after a `SELECT 1`; not rate-limited | 500 |

**Multiplayer routes** (built after go-live). Full shapes, errors and stream events are in [multiplayer.md §6.4–§6.5](multiplayer.md#64-api-contract); they're listed here so the route inventory (S11) stays complete.

| Endpoint | Request | Success |
| --- | --- | --- |
| `POST /api/match/queue` | `{}` | 200 MatchView (`queued`) |
| `POST /api/match/queue/leave` | `{}` | 204 |
| `GET /api/match/current` | – | 200 MatchView |
| `GET /api/match/stream` | – | 200 `text/event-stream`, starting with `snapshot`; 204 when idle |
| `POST /api/match/{id}/submit` | `{"tiles":[[row,col],…]}` | 200 `{status, word, changes}`, no points |
| `POST /api/match/{id}/display` | `{"tiles":[[row,col],…]}` | 200 `{status:"displayed", word, changes}`, no check, no points |
| `POST /api/match/{id}/vote` | `{"ranking":[candidate id, …]}`, 1–3 entries | 204 |
| `GET /api/match/{id}/results` | – | 200 Results (players of that match only) |

**Server-only event types.** The server logs these itself and refuses them from clients: `game_started`, `word_accepted`, `word_rejected`, `word_duplicate` and `game_ended`. Payloads: `game_started` `{mode}`, `word_*` `{word, points}`, `game_ended` `{reason, score, flagged}`, logged once per game even when requests race. Client events store their fields minus `type` and `game_id`, with the server's time in `created_at`. Multiplayer adds `match_started`, `match_word_accepted`, `match_word_rejected`, `match_word_duplicate`, `match_display_word`, `match_vote_cast` and `match_ended`, written when a match is saved, one row per player with `match_id` set and no `game_id`. Payloads: `match_started` `{player_count}`, `match_word_*` `{word, points}`, `match_display_word` `{word}`, `match_vote_cast` `{ranked}` (how many words, never whom), `match_ended` `{place, score}`.

**Boards.** The board endpoints first run `sweep_abandoned` on a read-write connection, then run the query on `connect_ro`.

## 4. SQLite schema (migration 1)

```sql
CREATE TABLE users (
  id           TEXT PRIMARY KEY,                    -- uuid4, never leaves the server
  token_hash   TEXT NOT NULL UNIQUE,                -- sha256 hex of cookie token
  nickname     TEXT NOT NULL,
  nickname_key TEXT NOT NULL UNIQUE,                -- folded (S8)
  hidden       INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0,1)),
  created_at   TEXT NOT NULL
) STRICT;
CREATE TABLE daily_challenges (
  date      TEXT PRIMARY KEY,                       -- 'YYYY-MM-DD' UTC
  word1     TEXT NOT NULL,
  word2     TEXT NOT NULL,
  picked_by TEXT NOT NULL CHECK (picked_by IN ('workflow','fallback')),
  CHECK (word1 <> word2)
) STRICT;
CREATE TABLE games (
  id          TEXT PRIMARY KEY,                     -- uuid4
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode        TEXT NOT NULL CHECK (mode IN ('free','daily')),
  daily_date  TEXT REFERENCES daily_challenges(date),
  word1       TEXT NOT NULL,
  word2       TEXT NOT NULL,
  total_words INTEGER NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  wrong_count INTEGER NOT NULL DEFAULT 0 CHECK (wrong_count BETWEEN 0 AND 3),
  score       INTEGER NOT NULL DEFAULT 0,
  won         INTEGER NOT NULL DEFAULT 0 CHECK (won IN (0,1)),
  flagged     INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0,1)),
  CHECK ((mode = 'daily') = (daily_date IS NOT NULL)),
  UNIQUE (user_id, daily_date)                      -- one daily game per player per day
) STRICT;
CREATE UNIQUE INDEX games_one_open_free ON games(user_id) WHERE mode = 'free' AND ended_at IS NULL;
CREATE INDEX games_user_mode_started ON games(user_id, mode, started_at);   -- 20/day count, recompute
CREATE INDEX games_open ON games(mode, started_at) WHERE ended_at IS NULL;  -- abandon sweep
CREATE TABLE submissions (
  id           INTEGER PRIMARY KEY,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game_id      TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  game_mode    TEXT NOT NULL CHECK (game_mode IN ('free','daily')),
  word         TEXT NOT NULL,
  is_valid     INTEGER NOT NULL CHECK (is_valid IN (0,1)),
  points       INTEGER NOT NULL DEFAULT 0,
  submitted_at TEXT NOT NULL,
  month        TEXT NOT NULL                        -- the game's month (plan: Rankings)
) STRICT;
CREATE UNIQUE INDEX submissions_valid_once ON submissions(game_id, word) WHERE is_valid = 1;
CREATE INDEX submissions_game ON submissions(game_id, submitted_at);
CREATE TABLE rankings (
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game_mode  TEXT NOT NULL CHECK (game_mode IN ('free','daily')),
  period     TEXT NOT NULL,                         -- 'YYYY-MM' monthly, 'YYYY-MM-DD' daily board
  best_score INTEGER NOT NULL,
  PRIMARY KEY (user_id, game_mode, period)
) STRICT;
CREATE INDEX rankings_board ON rankings(game_mode, period, best_score DESC);
CREATE TABLE events (
  id         INTEGER PRIMARY KEY,
  user_id    TEXT REFERENCES users(id) ON DELETE CASCADE,
  game_id    TEXT REFERENCES games(id) ON DELETE CASCADE,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',            -- JSON, no IPs
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX events_created ON events(created_at);
CREATE INDEX events_game ON events(game_id);
CREATE TABLE words (
  id        INTEGER PRIMARY KEY,
  word      TEXT NOT NULL UNIQUE,
  length    INTEGER NOT NULL,
  is_source INTEGER NOT NULL DEFAULT 0 CHECK (is_source IN (0,1)),
  freq_rank INTEGER                                 -- 1..500 for source words, else NULL
) STRICT;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;  -- e.g. 'words_sha256'
```

**Board query.** Hidden players are filtered out before ranking, so they don't take up a place:

```sql
SELECT u.nickname, r.best_score AS score, RANK() OVER (ORDER BY r.best_score DESC) AS rank
FROM rankings r JOIN users u ON u.id = r.user_id
WHERE r.game_mode = ? AND r.period = ? AND u.hidden = 0
ORDER BY score DESC, u.nickname_key LIMIT ?
```

**Rankings.** The `rankings` table is a cache, rebuilt from `games` by `recompute_ranking`.
- It runs when a game ends, and when `scripts.admin flag` or `unflag` runs.
- That's why clearing a flag takes effect immediately.
- `hidden` is applied when a board is read.

**Setup and migrations:**
- `db.migrate()` runs once in the lifespan, on its own connection without the authorizer, before any request connection exists.
- It sets `PRAGMA journal_mode=WAL` and reads `PRAGMA user_version`.
- For each pending `MIGRATIONS[i]` it runs `executescript("BEGIN IMMEDIATE;" + SQL + "PRAGMA user_version=i+1; COMMIT;")`. The SQL is always a constant in the code, never input.
- Migrations only move forward: never edit one that has been applied; append a new one instead.
- **Migration 2** (multiplayer, after go-live) adds `matches`, `match_players` (with `word_score`, `vote_bonus` and `display_word`), `match_submissions` (with `kind`: word or display), `match_votes` and `events.match_id`. Its SQL is in [multiplayer.md §6.6](multiplayer.md#66-database-migration-2).
- Request connections set `foreign_keys=ON` and `busy_timeout` *before* installing the authorizer.

## 5. Configuration

All values come from environment variables through `config.load_settings()`. Tests build `Settings(...)` directly and pass a `FakeClock` and a seeded `random.Random` to `create_app`.

| Variable | Default (local) | Test | Fly |
| --- | --- | --- | --- |
| `DATA_DIR` | `./var` (DB = `trur.db`) | `tmp_path` | `/data` |
| `LEXICON_DIR` | `./data` | `tests/fixtures` | `/app/data` (Dockerfile) |
| `WEB_DIR` | `./web` | `./web` | `/app/web` |
| `DAILY_JSON_SOURCE` | `./data/daily_challenges.json` (path) | tmp copy of the fixture | `https://raw.githubusercontent.com/FloMoTionGo/TRUR/main/data/daily_challenges.json` |
| `DAILY_FETCH_TIMEOUT_S` / `DAILY_FETCH_MAX_BYTES` | 5 / 1048576 | same | same |
| `ALLOWED_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000` | `https://testserver` | `https://trur.fly.dev` |
| `COOKIE_SECURE` | 1 (Chrome, Firefox and Edge accept Secure cookies on localhost) | 1 (TestClient uses https) | 1 |
| `HSTS` / `TRUST_FLY_CLIENT_IP` / `DEBUG` | 0 / 0 / 0 | 0 / 0 / 0 | 1 / 1 / 0 |
| `MAX_BODY_BYTES` | 16384 | same | same |
| `FREE_GAMES_PER_DAY` | 20 | 20 | 20 |
| `MIN_FREE_WORDS` (N) | 12 (from the [spike](spike-report.md)) | 1 | 12 |
| `MAX_FREE_WORDS` | 74 (the spike's p90; 0 = off, otherwise at least `MIN_FREE_WORDS`) | 0 | 74 |
| `SUBMIT_MIN_GAP_S` | 1.0 | 0, except in the gap test | 1.0 |
| `RL_NEW_PLAYERS_PER_IP_HOUR` / `RL_GAMES_PER_PLAYER_HOUR` / `RL_EVENTS_PER_PLAYER_S` | 5 / 30 / 1 | 0 (off), except in the rate-limit tests | same as local |
| `RL_REQUESTS_PER_IP_MIN` (backstop) | 300 | 0 | 300 |
| `EVENT_RETENTION_DAYS` / `BOARD_LIMIT` | 90 / 100 | same | same |
| `PLAUS_MAX_WPM`, `PLAUS_SOFT_WPM`, `PLAUS_MIN_CELLS`, `PLAUS_MIN_WORDS`, `PLAUS_FOUND_RATIO` | 20 / 8 / 49 / 5 / 0.9 (the first three provisional until real play data; see the [spike](spike-report.md)) | small values | same as local |

**Multiplayer settings** (`MATCH_*`, `MIN_MATCH_WORDS`, `RL_QUEUE_JOINS_PER_PLAYER_HOUR`, `SSE_KEEPALIVE_S`) are listed in [multiplayer.md §6.7](multiplayer.md#67-configuration).

**Constants in code** (decided rules, not settings): `MIN_TILES=2`, `MAX_TRIES=3`, `WIN_LETTERS=10` (the win check is "10 or more letters"), 50 events per batch, and the cookie name.

**Fly needs no secrets.**

**`fly.toml`** at the repo root:
- `app="trur"`, `primary_region="fra"`; `[env]` holds the Fly column above.
- `[mounts] source="trur_data" destination="/data"`.
- `[http_service]`:
  - `internal_port=8080`, `force_https=true`
  - `auto_stop_machines="stop"`, `auto_start_machines=true`, `min_machines_running=0`
  - `[http_service.concurrency] type="requests" soft_limit=50 hard_limit=100`. With multiplayer, every open SSE stream holds one slot, so the match and queue caps follow the rule in [multiplayer.md §6.8](multiplayer.md#68-hosting-impact)
  - `[[http_service.checks]]`: GET `/healthz`, interval 10s, timeout 2s, grace period 10s
- `[[vm]]` `shared-cpu-1x`, 256mb. Measure the lexicon's RAM use first.

**Dockerfile:**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY trur trur
COPY web web
COPY data data
COPY scripts scripts
ENV PYTHONUNBUFFERED=1 LEXICON_DIR=/app/data WEB_DIR=/app/web
CMD ["uvicorn","trur.app:create_app","--factory","--host","0.0.0.0","--port","8080","--workers","1","--no-server-header","--timeout-graceful-shutdown","2"]
```

**`--timeout-graceful-shutdown 2`:** on a deploy, uvicorn waits for open connections to close before it runs the lifespan shutdown. Solo requests finish in milliseconds, but multiplayer streams stay open, so they're cut after 2 s instead of holding up the deploy until Fly kills the machine.

The image runs as root because the Fly volume is mounted root-owned. Running as a non-root user, with a `chown` at startup, is optional hardening.

## 6. Dependencies

**Runtime** (`requirements.in`):
- `fastapi`: routing and Pydantic v2 validation. It brings starlette, pydantic, anyio and typing-extensions.
- `uvicorn`, plain rather than `[standard]`: the ASGI server. Without uvloop and httptools, a lock compiled on Windows also installs on Linux. `--reload` then falls back to polling.

**Dev and test** (`requirements-dev.in`):
- `pytest`
- `pytest-cov`: coverage gate
- `httpx`: required by the Starlette TestClient
- `pytest-playwright`: E2E in Chromium
- `PyYAML`: checks the workflow files (S10)
- `pip-tools`: the lockfiles
- `pip-audit`: dependency check (S12)
- `wordfreq`: used only by `build_wordlists.py`

**Deliberately left out:**

| Left out | Why |
| --- | --- |
| an ORM | about 30 hand-written `?` queries are simpler, and they're what S1 assumes |
| `pydantic-settings` | a 15-line dataclass does the job |
| `slowapi` | the rate limiter is about 30 lines |
| `requests` or `httpx` at runtime | `urllib` is enough for one GET |
| `freezegun` | the injected `Clock` does the job |

**Lockfiles:**
- Build with `pip-compile --generate-hashes`, once for runtime and once for dev, and install with `--require-hashes`.
- The locks hash every wheel of each pinned version, so locks compiled on Windows also install in the Linux image.
- Run `pip-audit -r requirements.txt` in CI and before each `fly deploy`.

## 7. Local dev workflow (PowerShell, from the repo root)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install pip-tools                                 # first time only, to create the locks
pip-compile --generate-hashes requirements.in; pip-compile --generate-hashes requirements-dev.in
python -m pip install --require-hashes -r requirements-dev.txt
# step 1 (once / when the dictionary changes); raw SCOWL download goes in build\ (gitignored)
python -m scripts.build_wordlists --scowl build\scowl\en_CA.txt
python -m scripts.spike_stats                                   # prints the feasibility report
# run (http://localhost:8000; DB at .\var\trur.db)
uvicorn trur.app:create_app --factory --reload --port 8000
pytest -q -m "not e2e and not smoke"                            # fast suite; add -m e2e for the browser flows
python -m scripts.pick_daily --dry-run                          # show what would be picked
python -m scripts.pick_daily [--today 2026-10-11] [--seed 1] [--data PATH]   # fills missing dates; rerun = no-op
python -m sqlite3 var\trur.db                                   # ad-hoc inspection (no sqlite3 binary needed)
python -m scripts.admin hide <nickname> | unflag <game_id> | delete-player <nickname>
pip-audit -r requirements.txt; fly deploy                       # on Fly: fly ssh console -C "python -m scripts.admin …"
```

## 8. Decisions recorded here

These close gaps the review found in the plan. Items 1, 2 and 18 were decided by the owner. Items 21–24 come from the multiplayer review (2026-10-11).

1. **Flags take effect at once:** `recompute_ranking()` rebuilds a ranking from `games` (without flagged games) at game end and on `scripts.admin flag|unflag`. `python:3.12-slim` has no `sqlite3` program, so manual access uses `python -m sqlite3` and `scripts.admin`.
2. **Winning word:** **10 letters or more** (owner). One predicate, `solver.is_win_word`, is used both for `won` and for deciding which daily pairs qualify.
3. **Nickname prompt:** `GET /api/users/me` tells the frontend whether to show it, because the HttpOnly cookie isn't readable from JavaScript.
4. **One response shape:** a single `GameState` for create, current and end. `SubmitResult` adds `score` and `total_words`. `current` returns `{"game": null}` when no game is open.
5. **Submitting after the day ends:** returns 409 `day_closed`, uses no try and closes the game. This applies to both modes.
6. **Past answers:** they get a "past answers" screen in Claude Design. The answers endpoint also returns the caller's own `found` words for that date.
7. **Nickname uniqueness:** the folded key goes in `users.nickname_key UNIQUE`. The regex blocks leading, trailing and double spaces.
8. **Parallel-submit test:** `SUBMIT_MIN_GAP_S` is a setting, so the "20 parallel submits score once" test can set it to 0. Submits that come too fast cost no try.
9. **Plausibility check:**
   - The rule: `flagged = (wpm > PLAUS_MAX_WPM and found ≥ PLAUS_MIN_WORDS) or (cells ≥ PLAUS_MIN_CELLS and found ≥ PLAUS_FOUND_RATIO·total and wpm > PLAUS_SOFT_WPM)`.
   - `wpm` is valid words ÷ max(0.5, minutes from `started_at` to the last valid submit).
   - The threshold values come from the step-1 spike.
10. **Late workflow runs:** `pick_daily` fills every missing date from today to tomorrow, so a run delayed past midnight doesn't skip a day. If the backend already made a fallback pair, the workflow's entry for that date is ignored.
11. **Blocklist:** `data/blocklist.txt` is curated by hand.
    - Dictionary words are filtered against it by exact match.
    - Nicknames are checked on the folded form, with the blocklist folded the same way (owner, 2026-10-11):
      - words of 5+ letters match anywhere in the nickname with spaces, `_` and `-` removed
      - words of 4 letters or fewer match only the whole nickname (separators removed) or one part between separators, so `a_s_s` and `ass_king` are blocked but Cassandra, Titus and Cucumber are not. Compounds like `bigass` get through; add them to the blocklist if they show up.
      - except `auth.ANYWHERE` (fuck, shit, cunt, porn, slut, twat, wank, jizz, dick), which match anywhere like long words, so FuckYou and BigDick are refused; Dickens and Scunthorpe are the accepted cost
12. **Boards:** ties use `RANK()` (1, 1, 3) and are ordered by nickname. A board shows the top 100. `month` defaults to the current UTC month and `date` to today.
13. **Limits:** reaching the 20-game limit leaves the open game untouched. The 30-per-hour limit is per player and counts every `POST /api/games`. Only allowed requests count toward a rate limit, so a client that keeps getting 429 isn't locked out for longer. The new-player limit counts a registration only once the nickname passes the rules: typos don't use up the hour, but a taken nickname does.
14. **Read-only connection:** the sweep runs on the read-write connection, then the board query runs on the read-only one.
15. **Daily JSON format:** a top-level array of `{"date","word1","word2"}`, uppercase, sorted by date, written with `indent=2` and a trailing newline.
16. **Month of a submission:** `submissions.month` is the game's month, which also decides which monthly board the game lands on.
17. **Cookie lifetime:** `Max-Age` is 400 days, so closing the browser doesn't lose the player.
18. **Open free games:** they close at midnight UTC with their current score (owner). This is done by the abandon sweep and by any request that touches the game. A free game counts on the board of the month it started.
19. **Orientation:** the pickers draw the orientation **before** checking a pair, because the grid isn't symmetric. CORRECT × MEDICAL loses DEMOCRATIC.
20. **Time and randomness:** time comes only from the injected `Clock`. Every random pick takes a `random.Random`. `pick_daily` has `--today`, `--seed`, `--data` and `--dry-run`.
21. **Threads and SQLite:** `connect()` and `connect_ro()` use `check_same_thread=False`, because a sync dependency and its route can run on different threadpool threads. This applies to solo too.
22. **Pure ASGI middleware:** `SecurityHeaders` is pure ASGI, like `BodySizeLimit`, so streaming responses keep prompt disconnect detection and cancellation.
23. **Shutdown:** uvicorn runs with `--timeout-graceful-shutdown 2`, so open streams can't hold up a deploy.
24. **Async match routes:** the multiplayer routes are `async def`, so match state changes happen on the event loop without locks. They never call `sqlite3` directly ([multiplayer.md §6.1](multiplayer.md#61-overview)).
