# TRUR – Task list

Overview and risks are in [plan.md](plan.md). "TS" refers to [docs/test-strategy.md](../docs/test-strategy.md), "Arch" to [docs/architecture.md](../docs/architecture.md).

**Default verification for every task:**
- `pytest -q -m "not e2e and not smoke"` passes, with coverage at or above the TS §1 gates
- `uvicorn trur.app:create_app --factory` starts (from T10 on)
- the CI run on the push is green

---

## Phase 1: Foundation and feasibility spike (doc step 1)

### T1: Project skeleton and CI
**Description:** Set up the Arch §1 layout: `pyproject.toml` (pytest config, markers `e2e`/`realdata`/`smoke`), `requirements*.in` with hashed locks, `.gitignore` entries (`var/`, `*.db*`, `.venv/`, `build/`, caches), `trur/__init__.py`, `clock.py` (Clock, SystemClock), `config.py` (Settings + `load_settings`, all Arch §5 fields), `errors.py` (ApiError), `tests/conftest.py` with FakeClock, the AST guards (time only in `clock.py`, no module-level `random.*`), and `.github/workflows/tests.yml` (SHA-pinned, `contents: read`, `TZ=Pacific/Kiritimati`, pip-audit, the `git ls-files '*.db' 'var/'` check).
**Acceptance criteria:**
- [x] `pip install --require-hashes -r requirements-dev.txt` works in a fresh venv
- [x] The AST guard fails on a planted `datetime.now()` outside `clock.py`
- [x] `load_settings({})` gives the "local" column of Arch §5
**Dependencies:** none · **Files:** pyproject.toml, requirements*.in/.txt, .gitignore, trur/{__init__,clock,config,errors}.py, tests/conftest.py, tests/test_static.py, .github/workflows/tests.yml · **Scope:** M

### T2: Word lists
**Description:** Check the SCOWL `en_CA` licence and size first. Then write `scripts/build_wordlists.py`: SCOWL in `build/` + `wordfreq` → `data/words.txt` (UPPERCASE A–Z, no proper nouns or abbreviations, blocklist filtered, inflections kept) and `data/source_words.txt` (the top 500 of 5–10 letters, in frequency order). Hand-curate `data/blocklist.txt`. Record provenance and licence in `data/README.md`.
**Acceptance criteria:**
- [x] `realdata` tests: exactly 500 source words, 5–10 letters, A–Z, all in the dictionary, no duplicates
- [x] A rerun of the script produces byte-identical output
- [x] The licence notice is in `data/README.md`
**Dependencies:** T1 · **Files:** scripts/{__init__,build_wordlists}.py, data/{words.txt,source_words.txt,blocklist.txt,README.md}, tests/test_wordlists.py · **Scope:** M

### T3: Solver core
**Description:** `trur/solver.py`: `Lexicon`, `load_lexicon`, `word_from_tiles`, `points`, `is_win_word`, and `solve` with the mask prefilter and pair counts (LRU cache). Add the TS §2 word fixture (GARDEN × SUMMER = 9 words worth 56 points, MEDICAL × CORRECT, STRONG × MEANING, synthetic words marked).
**Acceptance criteria:**
- [x] All TS step-1 tile-mapping, solver and scoring cases pass, including the backtracking oracle over 200 seeded pairs
- [x] 100 % branch coverage on `solver.py`
- [x] `realdata`: the largest 10×10 pair solves in under 100 ms
**Dependencies:** T1 (T2 for the realdata timing) · **Files:** trur/solver.py, tests/fixtures/*, tests/test_solver.py · **Scope:** M

### T4: Pair picking and daily-JSON validation
**Description:** Add `pair_key`, `pick_pair` (orientation drawn before `accept`, `max_attempts` → LookupError), `DailyEntry`, `parse_daily_json` and `validate_daily_entry` to the solver. The daily-JSON fixtures are `valid`, `mixed` and `broken`.
**Acceptance criteria:**
- [x] CORRECT × MEDICAL is rejected by the daily `accept`; MEDICAL × CORRECT is accepted
- [x] Each `mixed.json` rejection reason returns `None`
- [x] The same seed always gives the same pick
**Dependencies:** T3 · **Files:** trur/solver.py, tests/test_solver.py, tests/fixtures/daily/* · **Scope:** S

### T5: Feasibility spike
**Description:** `scripts/spike_stats.py`: words per ordered source pair (distribution), the share of pairs with a 10+-letter word, solve time, and the lexicon's RAM use. Write a short report (e.g. `docs/spike-report.md`) and set `MIN_FREE_WORDS` and the `PLAUS_*` defaults from it.
**Acceptance criteria:**
- [x] On the fixture data, the script reports 2 qualifying ordered pairs out of 30
- [x] The report is committed, and the thresholds are written into `config.py` defaults and Arch §5
**Dependencies:** T2, T4 · **Files:** scripts/spike_stats.py, docs/spike-report.md, trur/config.py, docs/architecture.md, tests/test_spike.py · **Scope:** S

### ✅ Checkpoint A: go/no-go
- [x] Review the spike report with the owner. If most pairs give only 0–3 words, **stop** and adjust the rules in docs/plan.md before any UI or backend work.

---

## Phase 2: Design and static frontend (doc step 2; T6 can start alongside phase 1)

### T6: Accessible palette, then the Claude Design pass
**Description:** The palette keeps its colours and pairs each one with a text colour that passes WCAG AA (decided: near-black on white and orange, white on olive and red; see docs/plan.md, Layout & style). Don't encode tile state by colour alone (colour-blind players). Then design screens 1–6, grid sizes 5×5 and 10×10, the tile states, and the full and compact logo in Claude Design. Put the link in the README.
**Acceptance criteria:**
- [x] Every text/background pair is at least 4.5:1 (large text at least 3:1)
- [x] The design is approved by the owner, and the link is in the README
**Dependencies:** none · **Files:** docs/plan.md (palette), README.md · **Scope:** S (mostly design work, not code)

### T7: Static shell
**Description:** `web/index.html` (no inline script or style), `css/app.css` with the design tokens, self-hosted fonts, `img/logo.svg`, `logo-compact.svg`, `favicon.svg`, `js/main.js` (screen switching), and `js/api.js` (fetch wrapper that parses the error envelope).
**Acceptance criteria:**
- [x] The page renders at 640×640 and matches the design by eye
- [x] There are no inline scripts or styles (checked by a static test)
**Dependencies:** T6 · **Files:** web/index.html, web/css/app.css, web/js/{main,api}.js, web/img/*, web/fonts/* · **Scope:** M

### T8: Game screen against a mocked API
**Description:** `web/js/game.js`: grid render (`data-row`, `data-col`, `data-state`), click to select, click again to undo, Clear, solution area, Submit against `page.route` mocks, the reject animation, the found list and the counter, all with `textContent` only. Add the feedback popup from the mocks (owner request): a rejected word shows a red popup naming the word, "not in the dictionary", the tries left, and the even-length rule; a duplicate shows an olive popup (no points, no try used). It fades after about 3 s and needs no API change. Set up the Playwright harness.
**Acceptance criteria:**
- [x] E2E flows 2, 3, 4 and 9 (TS §5) pass against the mocks
- [x] No console errors and no CSP violations
**Dependencies:** T7 · **Files:** web/js/game.js, web/css/app.css, tests/e2e/{conftest,test_flows}.py · **Scope:** M

### ✅ Checkpoint B
- [x] The browser version matches the approved design at 640×640 and plays with mocks

---

## Phase 3: Free play end to end (doc step 3)

### T9: Database layer
**Description:** `trur/db.py`: `MIGRATIONS[0]` (the Arch §4 schema), `migrate`, `connect` / `connect_ro` (`check_same_thread=False`, `busy_timeout`, `foreign_keys`, then the authorizer), `tx()`, `iso()`, `sync_words`, `purge_events`. Add the template-DB fixture and `db_snapshot()`.
**Acceptance criteria:**
- [x] The authorizer refuses DROP, ALTER, CREATE, ATTACH and PRAGMA writes, while a game's statements still work (TS S1)
- [x] The read-only connection refuses INSERT
- [x] `sync_words` is a no-op when the hash is unchanged
**Dependencies:** T3 · **Files:** trur/db.py, tests/conftest.py, tests/test_db.py · **Scope:** M

### T10: App factory and security middleware
**Description:** `trur/app.py` `create_app(settings, clock, rng)` with the lifespan (Arch §2 steps 1–5 and 7; the daily import comes in T20), `trur/security.py` (pure-ASGI `BodySizeLimit` and `SecurityHeaders`), exception handlers that return the error envelope (generic 500), `/healthz`, the static mount of `web/` only, and docs routes off unless `DEBUG`.
**Acceptance criteria:**
- [x] S9: the DB and repo files are not downloadable (TS §4 paths → 404)
- [x] S12: headers on HTML, JSON, 404, 422 and 500 responses; a raising route returns the generic envelope with no traceback
- [x] S7: 16 KB + 1 → 413, also when the body is chunked
**Dependencies:** T9 · **Files:** trur/app.py, trur/security.py, trur/api.py, tests/test_security.py · **Scope:** M

### T11: Minimal player cookie (pulled forward from step 4; see plan.md)
**Description:** `trur/auth.py` token and hash, `create_user`, `user_for_token`. In `api.py`: `POST /api/users` (regex only for now), `GET /api/users/me`, the `get_player` dependency (→ 401), and `check_post` (JSON → 415, Origin → 403).
**Acceptance criteria:**
- [x] The cookie is HttpOnly, Secure and SameSite=Lax; the DB stores only the SHA-256
- [x] `/me` without a cookie → 401; a second registration → 409 `already_registered`
- [x] A POST with a foreign or missing Origin → 403; `text/plain` → 415
**Dependencies:** T10 · **Files:** trur/auth.py, trur/api.py, tests/test_auth.py · **Scope:** S

### T12: Create and resume a free game
**Description:** `games.py` `create_game` (free: `pick_pair` with `MIN_FREE_WORDS`; the open game is ended as `replaced`), `current_game`, `GameState`, plus `POST /api/games` and `GET /api/games/current`. Today's daily words are excluded later, in T21.
**Acceptance criteria:**
- [x] TS step 3 "Creating a game": UUID4 id, 3 tries, `total_words` 9, no answers in the body
- [x] A reload resumes the game; with none open → `{"game": null}`
- [x] Starting B ends A as `replaced`
**Dependencies:** T11 · **Files:** trur/games.py, trur/api.py, tests/test_games.py · **Scope:** M

### T13: Submit transaction
**Description:** `submit()` as in Arch §2 "Submit transaction": `BEGIN IMMEDIATE`, ownership check (→ 404), day-closed check, `word_from_tiles`, accepted / duplicate / rejected, 3 tries, `all_found`. Pydantic tile model, and the `live_server` fixture.
**Acceptance criteria:**
- [x] All TS step 3 "Submitting", "Ending" and "Invalid tiles" cases pass (the two `POST /end` cases were added with T14)
- [x] S5: 20 parallel identical submits → 1 accepted and 19 duplicate; 10 parallel wrong submits → `wrong_count` 3; no 5xx
- [x] A body with `score`, `points`, `won` or `date` → 422
**Dependencies:** T12 · **Files:** trur/games.py, trur/api.py, tests/test_games.py, tests/conftest.py (live_server) · **Scope:** M

### T14: End game, sweep, frontend wired to the real API
**Description:** `end_by_player`, `_end` (missed words for free play, sorted by points then A–Z), `sweep_abandoned` (at startup and on any touch). Replace the step-2 mocks in `api.js` and `game.js` with real calls, and add the game-over screen.
**Acceptance criteria:**
- [x] E2E flows 2–6 pass on `live_server`; flow 9 keeps its mock
- [x] A free game open at 23:59:59 → a submit at 00:00 returns 409 `day_closed` and closes the game with its score
**Dependencies:** T13, T8 · **Files:** trur/games.py, trur/api.py, web/js/{api,game,main}.js · **Scope:** M

### T15: S1 injection suite
**Description:** The payload × input matrix from TS §4 S1, `PRAGMA integrity_check`, and an AST guard against SQL built by string formatting (`executescript` only in `db.migrate`).
**Acceptance criteria:**
- [x] Every payload → no 500, `db_snapshot()` unchanged
- [x] The nickname `a-- DROP users` is stored verbatim
**Dependencies:** T14 · **Files:** tests/test_security.py, tests/test_static.py · **Scope:** S

### ✅ Checkpoint C
- [x] A full free game runs in the browser against the real backend; the S1 and S5 tests are green; review with the owner

---

## Phase 4: Players and logging (doc step 4)

### T16: Nickname rules and prompt
**Description:** `fold_nickname`, `check_nickname` (regex, blocklist substring on the folded form), `nickname_key` uniqueness → 409, and the nickname-prompt screen driven by `/api/users/me`.
**Watch out:** substring matching with short blocklist words (`ass`, `tit`, `cum`) would also reject Cassandra, Titus and Cucumber. Decide on a minimum length for substring matches, with exact matching for shorter words, and agree it with the owner. **Decided (owner, 2026-10-11):** 5+ letters by substring, 4 or fewer only as a whole part; see architecture.md §8 item 11.
**Acceptance criteria:**
- [x] All TS S8 cases pass (length, characters, lookalikes, blocklist)
- [x] E2E flow 1 passes
**Dependencies:** T14 · **Files:** trur/auth.py, data/blocklist.txt, web/js/main.js, tests/test_auth.py · **Scope:** M

### T17: Rate limits
**Description:** `RateLimiter` (sliding window, Clock-driven, limit 0 = off), `client_ip` (trusts `Fly-Client-IP` only when configured, never stores it), wired for new players per IP, games per hour, the submit gap (S4), events per second, and the per-IP backstop.
**Acceptance criteria:**
- [x] TS S7 "New players" and "Other limits", and the S4 gap cases (the events-per-second limit is wired with its endpoint in T18)
- [x] A 429 never changes the DB, and the response has `Retry-After`
**Dependencies:** T16 · **Files:** trur/security.py, trur/api.py, trur/games.py, tests/test_security.py · **Scope:** M

### T18: Gameplay event log
**Description:** Server-side events (`game_started`, `word_*`, `game_ended`), `POST /api/events` with a discriminated Pydantic union (at most 50, client types only), `web/js/events.js` batching, and the 90-day purge at startup.
**Acceptance criteria:**
- [x] TS step 4 "Logging" cases pass; a server-only type from the client → 422
- [x] Retention boundary: T − 90 d deleted, T − 90 d + 1 s kept
**Dependencies:** T17 · **Files:** trur/games.py, trur/api.py, web/js/events.js, tests/test_games.py · **Scope:** M

### T19: S2 test suite
**Description:** Ownership 404s, no `user_id` input, 100 UUID4 ids, the raw token absent from the DB file, no ID or hash in any response, and no CORS headers.
**Acceptance criteria:**
- [x] All TS S2 cases pass
**Dependencies:** T18 · **Files:** tests/test_security.py · **Scope:** S

### ✅ Checkpoint D
- [x] Another player's game ID → 404; `<script>` as a nickname → 422; all tests green

---

## Phase 5: Daily challenge (doc step 5)

### T20: Daily import and fallback
**Description:** `daily.py` `import_daily` (path or https via urllib, timeout, size cap, validation, never raises, logs skips), `ensure_daily_pair` (process lock, SELECT → import → fallback avoiding pairs in the DB and the JSON → `INSERT OR IGNORE` → re-SELECT, 503 when no pair can be picked), and the lifespan import when today or tomorrow is missing.
**Acceptance criteria:**
- [x] TS step 5 "Import and fallback" cases, including the two-thread fallback race (the HTTP repeat needs daily mode: T21)
- [x] An `http://` source makes startup fail (S10)
**Dependencies:** T19 · **Files:** trur/daily.py, trur/app.py, tests/test_daily.py · **Scope:** M

### T21: Daily game mode
**Description:** `create_game` daily (one per player per day, 200 or 201), the `won` flag, the midnight close, no `missed_words` in daily, and free play excluding today's daily words (`ensure_daily_pair` before every free pick).
**Acceptance criteria:**
- [x] TS step 5 "Starting", "Winning" and "Midnight" cases, including the parallel daily start
- [x] Seeds 0–49: no free game uses today's words, even when only the JSON has today's pair
**Dependencies:** T20 · **Files:** trur/games.py, trur/api.py, tests/test_daily.py · **Scope:** M

### T22: Past answers endpoint and leak sweep
**Description:** `daily_answers` and `GET /api/daily/{date}/answers` (403 `day_not_over`, 404, 422, the caller's own `found`).
**Acceptance criteria:**
- [x] The TS answers-endpoint table passes
- [x] Leak sweep: no route's response contains an unfound word from today
**Dependencies:** T21 · **Files:** trur/daily.py, trur/api.py, tests/test_daily.py · **Scope:** S

### T23: `pick_daily` and the workflow (can start after T4)
**Description:** `scripts/pick_daily.py` (fills today and tomorrow, `--today`, `--seed`, `--data`, `--dry-run`, no repeats in either order, non-zero exit when nothing qualifies), and `.github/workflows/daily-challenge.yml` (cron `17 21 * * *`, dispatch input `dry_run`, job-level `contents: write`, SHA-pinned, concurrency group, `pull --rebase` before push). Set the repo's default workflow permissions to read-only.
**Acceptance criteria:**
- [x] TS step 5 `pick_daily` cases pass; the S10 static workflow checks pass
- [x] CI dry-runs it on the real data, and the realdata check of `daily_challenges.json` passes
- [x] Owner: set the repo's default workflow permissions to read-only (Settings → Actions → General); the CLI token can't
**Dependencies:** T4 · **Files:** scripts/pick_daily.py, .github/workflows/{daily-challenge,tests}.yml, data/daily_challenges.json, tests/test_pick_daily.py · **Scope:** M

### T24: Daily UI and past answers screen
**Description:** The Free | Daily mode switch, the won state, the "answers after midnight UTC" note, no missed words on game over, and the past-answers screen with the player's own finds marked.
**Acceptance criteria:**
- [x] E2E flow 7 passes
**Dependencies:** T22 · **Files:** web/js/{main,game,boards}.js, web/css/app.css · **Scope:** M

### ✅ Checkpoint E
- [x] A manual `workflow_dispatch` commits tomorrow's pair; a second run commits nothing
- [ ] After midnight UTC the backend serves that pair; today's answers can't be fetched by any route

---

## Phase 6: Rankings (doc step 6)

### T25: Free-play daily limit
**Description:** The 20-per-UTC-day limit inside the create transaction (429 `limit_reached`, open game untouched; abandoned and flagged games count; daily games don't).
**Acceptance criteria:**
- [x] TS step 6 "Free-play limits" cases pass
**Dependencies:** T21 · **Files:** trur/games.py, tests/test_rankings.py · **Scope:** S

### T25a: Cap on words per free grid
**Description:** Owner decision on spike finding 1 (2026-10-12): add `MAX_FREE_WORDS` (default 74, the spike's p90; 0 = off, the test default) so free play redraws the richest grids and a lucky draw can't dominate the monthly boards. The free-play `accept` rule becomes `MIN_FREE_WORDS ≤ len(answers) ≤ MAX_FREE_WORDS`; daily picks are unaffected. Update Arch §5 (picker rules and the settings table).
**Acceptance criteria:**
- [x] With a cap set, no free game has more answers than the cap; with 0, behaviour is unchanged
- [x] `MAX_FREE_WORDS` below `MIN_FREE_WORDS` (other than 0) is rejected at startup
**Dependencies:** T25 · **Files:** trur/config.py, trur/games.py, docs/architecture.md, tests/test_games.py, tests/test_config.py · **Scope:** S

### T26: Rankings, boards and the plausibility flag
**Description:** `rankings.py` (`periods`, `recompute_ranking` called from `_end`, `monthly_board`, `daily_board` on `connect_ro` after a sweep on the read-write connection), `is_implausible` setting `flagged` in `_end`, and both board endpoints.
**Acceptance criteria:**
- [x] TS step 6 "Boards", "Sweep" and "Plausibility check" cases pass, plus the month-boundary case
- [x] Rows contain exactly `rank`, `nickname` and `score`
**Dependencies:** T25a · **Files:** trur/rankings.py, trur/games.py, trur/api.py, tests/test_rankings.py · **Scope:** M

### T27: Admin script
**Description:** `scripts/admin.py`: `hide`, `unhide`, `flag`, `unflag` (with ranking recompute) and `delete-player` (cascade).
**Acceptance criteria:**
- [x] After unflag, the next board request shows the score; after unhide, the player is back
- [x] `delete-player` removes only that player's rows (S13)
**Dependencies:** T26 · **Files:** scripts/admin.py, tests/test_admin.py · **Scope:** S

### T28: Boards UI
**Description:** `web/js/boards.js`: monthly boards per mode and the daily board, rendered with `textContent`.
**Acceptance criteria:**
- [x] E2E flow 8 passes (the XSS nickname shows as literal text)
**Dependencies:** T26 · **Files:** web/js/{boards,main}.js, web/css/app.css · **Scope:** S

### ✅ Checkpoint F
- [x] Flagged games and hidden players are missing from every board; the full suite and E2E are green

---

## Phase 7: Go live (doc step 7)

### T29: Deploy artifacts and production tests
**Description:** `Dockerfile`, `.dockerignore`, `fly.toml` (Arch §5), production-settings tests (docs routes off, HSTS on), the route-inventory test (S11), `fly.toml` static checks, the `tests/smoke/` suite, and a short privacy note on the page (S13).
**Acceptance criteria:**
- [x] TS step 7 and S11, S12 and S13 automated cases pass
- [x] `docker build` succeeds and the container serves `/healthz`
**Dependencies:** T28 · **Files:** Dockerfile, .dockerignore, fly.toml, tests/test_prod.py, tests/smoke/*, web/index.html · **Scope:** M

### T30: Go-live (manual, with the owner)
**Description:** Install `flyctl`, turn on 2FA for GitHub and Fly, `fly apps create trur`, create the volume, set snapshot retention to 14 days, `pip-audit`, `fly deploy`, run the smoke tests and go-live flows 1, 2 and 7, delete the throwaway player, check `fly machine restart` persistence, do one snapshot restore drill, and update the README status.
**Acceptance criteria:**
- [ ] A free game and a daily game work at `https://trur.fly.dev`
- [ ] Scores survive a restart, and one snapshot has been restored
**Dependencies:** T29 · **Files:** README.md · **Scope:** S (mostly ops)

### ✅ Checkpoint G: live

---

## Phase 8: Multiplayer (M1–M6 before go-live, owner 2026-10-12)

### M1: Rules engine
**Description:** `trur/match.py` (multiplayer.md §6.3): `pct`, `word_points`, `submit`, `display`, `close_play`, `ballot_for`, `vote`, `bonus`, `finish`, `results`. Plus a dev sandbox, `python -m scripts.match_sandbox` (owner request): one shared board on localhost, hot-seat players, no clock, with the vote and the results.
**Acceptance criteria:**
- [x] TS §6 step M1 cases pass, including the worked example, the 1-point case and the worked results example
- [x] 100 % branch coverage on `match.py`
- [ ] The owner has played a 4-player game in the sandbox
**Dependencies:** T3 · **Files:** trur/match.py, scripts/match_sandbox.{py,html}, tests/test_match.py, tests/test_match_sandbox.py · **Scope:** M

The remaining steps follow [multiplayer.md §10](../docs/multiplayer.md#10-implementation-plan): M1 rules engine → M2 spike → M3 schema → M4 lobby → M5 API and SSE → M6 design and frontend → M7 release → M8 power-ups.
