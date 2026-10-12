# TRUR – Test strategy

How [plan.md](plan.md) gets tested. Module, setting and endpoint names come from [architecture.md](architecture.md). Multiplayer, built after go-live, has its own section, [§6](#6-multiplayer-after-go-live). Drafted on 2026-10-11 from a test-engineering review, with the owner's decisions applied:
- the daily challenge is won by a word of **10 or more letters**
- open free games **close at midnight UTC**

## 1. Test levels and tools

| Level | Scope | Speed |
| --- | --- | --- |
| Unit | solver, tile mapping, scoring, nickname folding, import validation, pair picking, plausibility, rank order | ms |
| Integration | FastAPI app in-process against a temporary SQLite file. Parallel tests use a live uvicorn server | about 1 s per module |
| Static | AST guards over `trur/` and `scripts/`, checks of the workflow files | ms |
| E2E | Playwright against a live server with fixture data | 1–2 min total |
| Smoke | read-only checks against `https://trur.fly.dev` | about 10 s, run by hand |

**Tools.** This is the complete list:
- **pytest:** one runner for every level. Markers: `e2e`, `realdata`, `smoke`.
- **pytest-cov:** coverage gate in CI.
  - 85 % for `trur/` and `scripts/`.
  - 100 % branch coverage for `trur/solver.py`.
- **FastAPI `TestClient` (httpx):** in-process requests with real cookies and headers.
  - `httpx.MockTransport` fakes the raw GitHub fetch.
  - `httpx.AsyncClient` sends the parallel requests.
- **uvicorn:** a `live_server` fixture on a random port. The parallel tests and E2E need it, because in-process requests never truly overlap.
  - Multiplayer adds `live_match_server`, which runs in a thread of the test process with a shared `FakeClock` (§6). `TestClient` can't test an endless SSE stream, because it waits for the response to finish.
- **pytest-playwright:** Chromium only, since the game is desktop-only. `page.route()` mocks the API during step 2.
- **pip-audit:** already required by S12.
- **No time-freezing library.** The injected `Clock` plus an AST guard (§2) make the midnight tests exact.
- **No Node toolchain.** The backend decides everything and the JS is thin, so E2E covers the JS.

## 2. Fixtures and test data

### Word fixture

Lives in `tests/fixtures/`.
- **Words:** `source_words.txt` has GARDEN, SUMMER, MEDICAL, CORRECT, STRONG and MEANING. `words.txt` has those plus the words below.
- **Tile rule:** tile `[r,c]` = word1[c] + word2[r].
- **Synthetic words** are marked `# synthetic` in the file.
- **No other 10-letter-or-longer words**, so the qualifying pairs below stay known.

GARDEN × SUMMER, solved by hand, gives **9 words worth 56 points**:

| Word | Tiles | Points |
| --- | --- | --- |
| GEAR, DEER, NEAR | GE[4,0] AR[5,1] · DE[4,3] ER[5,4] · NE[4,5] AR[5,1] | 4 each |
| RUNE | RU[1,2] NE[4,5] | 4 |
| AMAM (synthetic: no real word repeats a tile text) | AM[2,1] AM[3,1] | 4 |
| NEARER, DEARER | NE AR ER · DE AR ER | 9 each |
| GENRES | GE[4,0] NR[5,5] ES[0,4] | 9 |
| RERUNS | RE[4,2] RU[1,2] NS[0,5] | 9 |

These dictionary words must **not** be found in GARDEN × SUMMER:

| Word | Why not | Rule it tests |
| --- | --- | --- |
| URGE, SAGE | they need UR and SA, which exist only reversed (RU, AS) | fixed letter order |
| NENE | it needs NE twice, and there's only one NE tile | each tile once per word |
| RANGE | odd length | even-length rule |
| AS | a single tile | minimum length |

**Daily pairs.** Exactly two of the 30 ordered pairs have a win word (10 or more letters):

| Pair (word1 × word2) | Win words | Reversed pair |
| --- | --- | --- |
| MEDICAL × CORRECT | DEMOCRATIC = DE MO CR AT IC (10 letters, 25 points) and DEMOCRATICLO (synthetic) = DE MO CR AT IC LO (12 letters, 36 points) | CORRECT × MEDICAL has none, so orientation matters |
| STRONG × MEANING | GENERATION = GE NE RA TI ON | MEANING × STRONG has none |

**Daily JSON fixtures:**
- `valid.json`
- `mixed.json`: one good entry, plus one entry for each rejection reason
- `broken.json`: not valid JSON
- a body over the size cap, generated inside the test

### Time, randomness and the database

- **Clock:**
  - `create_app(settings, clock, rng)` gets a `FakeClock` with `set()` and `advance()`.
  - The default test time is `2026-10-11T12:00:00Z`. Boundary tests use `23:59:59.999999` and `00:00:00`.
  - `pick_daily` takes `--today`.
  - An AST guard allows `datetime.now`, `datetime.utcnow`, `date.today` and `time.time` only in `trur/clock.py`.
  - CI runs with `TZ=Pacific/Kiritimati` (UTC+14), so any leftover local-time call gives the wrong date.
- **Randomness:**
  - The free-play picker, the fallback and `pick_daily --seed` all take a `random.Random`. An AST guard bans module-level `random.*` calls.
  - Property tests loop over seeds 0–49.
  - Tokens use `secrets` and are never seeded.
- **Database:**
  - A template database (schema plus fixture words) is built once per session and copied to `tmp_path/trur.db` for each test.
  - It's a real file, not `:memory:`. Three connections are needed (migration, the request connection with the authorizer, and the read-only one), and `BEGIN IMMEDIATE` needs real locking.
  - `db_snapshot()` returns the schema SQL from `sqlite_master` plus the row count of every table.
- **Clients:**
  - `TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"})`. It has to be HTTPS, or the `Secure` cookie is never sent back.
  - The client is used as a context manager, so the lifespan runs.
  - A `player("Alice")` factory gives each player their own cookie jar.
- **Concurrency tests:** the S5 tests set `SUBMIT_MIN_GAP_S=0`. With the gap on, 19 of 20 parallel submits would get 429, and the transaction would never be tested. A separate test checks the gap itself.

## 3. Test cases per build step

### Step 1: word lists and solver (unit)

**Tile mapping:**
- `[0,0]` → `GS`, never `SG`.
- `[1,0]` → `GU`. Row indexes word 2 and column indexes word 1.
- `[4,2]` → `RE` and `[5,4]` → `ER`. Both exist as separate tiles.

**Solver:**
- `solve(GARDEN, SUMMER)` returns exactly the 9 words, sorted, the same on every call.
- URGE and SAGE are absent (no reversed pairs).
- NENE is absent (one NE tile).
- AMAM is present (two separate AM tiles).
- RANGE and AS are absent, and every result has an even length of at least 4.
- `is_win_word`: 8 letters → False, 10 → True, 12 → True.
- MEDICAL × CORRECT win words → `[DEMOCRATIC, DEMOCRATICLO]`. CORRECT × MEDICAL → `[]`.
- Oracle: for 200 seeded random pairs built from fixture letters, `solve` equals a backtracking reference that assigns real coordinates.

**Scoring:**
- `points` for 2, 3, 4, 5 and 6 tiles → 4, 9, 16, 25 and 36.
- All 9 GARDEN × SUMMER words together → 56.

**Real data and the spike report:**
- `realdata`: the source list has exactly 500 words, each 5–10 letters, A–Z only, all in the dictionary, no duplicates.
- `realdata`: solving the largest 10×10 source pair takes under 100 ms.
- `spike_stats` on the fixture data reports 2 qualifying ordered pairs out of 30.

### Step 2: static frontend

E2E against a mocked API (see §5).

### Step 3: game flow (integration; rerun with real players once step 4 exists)

**Creating a game:** a free game (seeded to GARDEN × SUMMER) returns a UUID4 id, `tries_left` 3, `total_words` 9, the documented GameState keys and no answer list.

**Submitting:**
- GE+AR → `accepted`, GEAR, 4 points, `found_count` 1.
- NE+AR+ER → `accepted`, NEARER, 9 points.
- GS+AS → `rejected`, 0 points, `tries_left` 2, stored with `is_valid = 0`.
- GEAR again → `duplicate`. Tries and score are unchanged and no row is stored.
- AMAM as [2,1],[3,1], then as [3,1],[2,1] → the second is `duplicate`. Uniqueness is per word, not per set of tiles.
- Re-submitting a word that was already rejected costs a try again. Only found words count as `duplicate`.

**Ending a game:**
- The 3rd rejected word → `game_over`, `missed_words` = the solutions not found, `ended_at` set.
- Finding all 9 words → the last submit returns `game_over`, `missed_words` `[]` and score 56.
- `POST /end` → GameState with `missed_words`.
- A second `/end` → 409 `game_over`, score unchanged.
- A submit after any ending → 409 `game_over`. No row is stored and tries are unchanged.

**Invalid tiles** each return 422 and cost no try:
- `[]` or a single tile
- off the grid: `[0,6]` on a 6×6 grid, `[-1,0]`
- the same tile twice
- wrong types: `["1","0"]` or `[true,0]`
- a pair with three elements
- more tiles than `max_tiles`

**Counting and resuming:**
- `found_count` rises only on `accepted`.
- `total_words` comes from the games row. Patching the solver after the game was created doesn't change it.
- `GET /api/games/current?mode=free` after a reload → same game, found words in order, same score and tries.
- `current` with no open game → `{"game": null}`.
- `won` is always false in free play.

### Step 4: players and logging

- **Players:**
  - `POST /api/users` → sets the cookie and returns 201 `{nickname}`. The database stores `sha256(token)`, never the token.
  - `GET /api/users/me` → the nickname, or 401 without a cookie.
  - `POST /api/users` with a valid cookie → 409 `already_registered`.
  - A game endpoint with no cookie or an unknown token → 401, and no player is created.
- **Logging:**
  - The server logs `game_started`, `word_accepted`, `word_rejected`, `word_duplicate` and `game_ended`, each with the game id.
  - `POST /api/events` with 50 `tile_clicked` → 204, stored with the player and game. Server-only types are refused with 422.
  - Startup at time T deletes events older than T − 90 days, and keeps one at T − 90 days + 1 s.

The S2, S4-gap, S7 and S8 tests are in §4.

### Step 5: daily challenge

Base fixture: the JSON has 10-10 = STRONG × MEANING and 10-11 = MEDICAL × CORRECT. The clock is at 2026-10-11T12:00Z.

**Starting the daily game:**
- `POST /api/games {mode: daily}` → 201, MEDICAL × CORRECT, `daily_date` 2026-10-11, set by the server.
- The same call again → 200, same `game_id`, still one row. This also holds after the game is over.
- One player's two daily starts in parallel → one row, and both responses have the same id.
- Two different players get the same pair.

**Winning:**
- DEMOCRATIC → `accepted`, 25 points, `won` true, `game_over` false. Play continues.
- In a separate game, DEMOCRATICLO (12 letters) also sets `won` and scores 36.

**Answers stay hidden:**
- The daily `/end` and the game-over submit carry no `missed_words`, and no response body contains an unfound word.
- Leak sweep: with today's game open, call every route. No body contains an unfound word from today's grid.

**Midnight:**
- A submit at 23:59:59.999999 → accepted.
- A submit at 00:00:00 → 409 `day_closed`, no try used, and the game is closed with its current score.
- A game started at 23:59:59 keeps that day's `daily_date`.
- Add a 10-12 entry to the JSON. A daily start at 10-12T00:00:00 → a new game with that pair.

**Answers endpoint:**

| Request | Clock | Result |
| --- | --- | --- |
| date 10-11 | 10-11 12:00 | 403 `day_not_over` |
| date 10-12 | 10-11 12:00 | 403 `day_not_over` |
| date 10-10 | 10-11 12:00 | 200, full list, plus the caller's `found` |
| date 10-11 | 10-12 00:00 | 200 |
| `2026-02-30` or `x` | any | 422 |
| a past date with no pair | any | 404 |

**Import and fallback:**
- Valid entries are stored with `picked_by 'workflow'`. A date that is already stored is never overwritten, so a fallback row survives a later JSON entry.
- `mixed.json`: each bad entry is skipped and logged (checked with caplog), and the good entry is still imported. The bad entries are:
  - a bad date
  - a word not in the source list
  - the same word twice
  - CORRECT × MEDICAL (no win word in that orientation)
  - a missing field
  - a number where a string belongs
- `broken.json`, a fetch timeout and a body over the cap → no crash, the failure is logged, and the fallback is used.
- The import runs at startup and when a daily game is requested for a missing date. If the JSON gains the date between two requests, the second request uses it.
- **Fallback:** the JSON has only 10-10 = MEDICAL × CORRECT, so today is missing → STRONG × MEANING is stored with `picked_by 'fallback'` and a warning is logged.
- **Fallback race:**
  1. Find two seeds that pick different pairs when run alone, and assert that first.
  2. Start two threads behind a barrier, each with its own connection and seed, both calling `ensure_daily_pair()`.
  3. Expect one row, and both threads return the same pair.
  4. Repeat over HTTP with two players on the live server.
- **Free play avoids today's words:** with daily = MEDICAL × CORRECT, no free game across seeds 0–49 contains MEDICAL or CORRECT. This also holds when `daily_challenges` is empty and today's pair is only in the JSON.

**`pick_daily`** (run as a subprocess with `--today`, `--data <tmp copy>`, `--seed`):
- It fills every missing date in [today, tomorrow]. Existing entries stay byte-identical.
- A second run → exit 0, file unchanged.
- Today and tomorrow both present → a no-op, whatever the seed.
- With MEDICAL × CORRECT (in either orientation) already used, seeds 0–19 all pick STRONG × MEANING.
- The chosen orientation always has a win word (seeds 0–19).
- Every qualifying pair already used → non-zero exit, file unchanged.
- New entries pass the backend's import validation.
- `--dry-run` prints the entries and writes nothing.

### Step 6: free-play limits and rankings

**Free-play limits:**
- 20 free starts in one UTC day succeed.
- The 21st → 429 `limit_reached`, and the open game is untouched.
- At 00:00 the next day, a new start succeeds.
- A game started and abandoned still counts toward the 20. Daily games don't count.
- Start game A, score 4, then start B:
  - A is ended (`replaced`) with 4 points and ranked
  - a submit to A → 409 `game_over`
  - `current` returns B
- A free game open at 23:59:59 → a submit at 00:00:00 returns 409 `day_closed`. The game is closed with its current score and ranked on the board of the month it started.

**Boards:**
- **Monthly board:** one player scores 10, 30 and 20 in October → one row with 30. A November game doesn't change October.
- **Month boundary:** a free game started 2026-10-31T23:59:50Z and closed at midnight → on the October board.
- **Daily board:** one row per player for that date, ordered by points.
- **Ties:** RANK gives 1, 1, 3, ordered by nickname.
- **Flags:** a player with a flagged 50 and a clean 30 → the board shows 30. After `scripts.admin unflag` → the next request shows 50.
- **Hidden players:** absent from all three boards. After unhide → back on the next request.
- **Row shape:** board rows contain exactly `nickname`, `score` and `rank`.

**Sweep:** yesterday's open daily and free games are closed with their current score. This happens:
- at startup
- before a board is served
- on any request that touches the game

**Plausibility check** (thresholds from settings):
- Fast, at least 90 % found, on a grid at or above the minimum size → flagged.
- The same speed on a small grid → not flagged.
- Every word found, but slowly → not flagged.
- Flagged games still count toward the 20-a-day limit.

**Bad input:** `mode=x`, `month=2026-13` and `date=yesterday` → 422.

### Step 7: go-live

- **Production settings:** `/docs`, `/redoc` and `/openapi.json` → 404. HSTS is present and debug is off.
- **`fly.toml` checks:**
  - `force_https = true`
  - `[http_service.concurrency]` is set
  - the mount is at `/data` and `DATA_DIR = "/data"`
  - the health check uses `/healthz`
- **Health check:** `GET /healthz` → 200. It sets no cookie and writes nothing.
- **Deleting a player:** `scripts.admin delete-player` removes that player's rows from users, games, submissions, events and rankings, and leaves other players untouched (S13).

## 4. Security tests (S1–S13)

**S1, SQL injection (step 3):**
- **Payloads:**
  - `' OR 1=1--`
  - `x'); DROP TABLE submissions;--`
  - `1; DROP TABLE users`
  - `' UNION SELECT token_hash FROM users--`
  - `%27`, a NUL byte, and a 10 KB string
- **Where they go:** every input:
  - nickname
  - `mode` (body and query), `month`, `date` (query and path)
  - game id
  - tiles
  - event type and payload
  - cookie
- **Expected:**
  - no 500
  - `db_snapshot()` unchanged and `PRAGMA integrity_check` = ok
  - no response mentions `sqlite` or `syntax`
- The nickname `a-- DROP users` uses only allowed characters, so it's accepted and stored exactly as typed. That proves the placeholders work, not just the validation.
- **Request connection (authorizer):**
  - DROP, ALTER, CREATE, ATTACH and `PRAGMA user_version = 1` → "not authorized".
  - SELECT, INSERT, UPDATE and `BEGIN IMMEDIATE` work, and a full game runs.
- **Read-only connection:** INSERT → "attempt to write a readonly database".
- **AST guard:** fails any `execute*` call whose SQL is built with an f-string, `%`, `.format()` or `+`. `executescript` is allowed only in `db.migrate`.

**S2, acting as another player (steps 3–4):**
- **Ownership:** player B calls `/submit` and `/end` on A's game → 404, the same response as for a random UUID. A's game is unchanged.
- **No `user_id` input:** `{"user_id": …}` in any body → 422, and no route has a `user_id` parameter.
- **IDs and secrets:**
  - 100 new game ids are all UUID version 4.
  - The cookie is HttpOnly, Secure and SameSite=Lax, and the token has at least 32 random bytes.
  - The raw token never appears in the database file.
- **No leaks:** no response contains a user id or a token hash.
- **Origin:** a POST with `Origin: https://evil.example`, or with no Origin → 403 `bad_origin`, nothing written.
- **Content type:** `Content-Type: text/plain` or a form-encoded body → 415.
- **CORS:** a preflight from another origin gets no `Access-Control-Allow-*` headers.

**S3, early answers (step 5):** hidden answers, closing at midnight, the answers endpoint and the leak sweep are all covered in step 5.

**S4, bots (steps 4 and 6):**
- **Submit gap:** submits 0.5 s apart → 429, no try used, no row stored. Submits 1.0 s apart → both processed.
- **The gap is per game:** two players submitting at the same moment are both processed.
- **Plausibility and `hidden`** are covered in step 6.

**S5, double scoring (step 3):**
- **Duplicate race:** 20 identical valid submits in parallel (live server, gap 0) → exactly 1 `accepted` and 19 `duplicate`, score 4, one valid row, no 5xx. A missing busy timeout would show up as a 5xx.
- **Wrong-submit race:** 10 wrong submits in parallel → `wrong_count` 3, three rejected rows, and 409 for the rest.
- **Unique index:** a direct duplicate INSERT of a valid `(game_id, word)` → `IntegrityError`.
- **No client scores:** `points`, `score`, `won` or `date` in a request body → 422.

**S6, rerolling (step 6):** the 20-a-day limit and the one-open-game rule are covered in step 6.

**S7, flooding (steps 3–4):**
- **Body size:** 16 KB + 1 to every POST route → 413. This also holds for a chunked body without `Content-Length`.
- **Events:** 51 events, an unknown type, or a payload with extra keys → 422.
- **New players:**
  - The 6th new player in an hour from one `Fly-Client-IP` → 429.
  - A different IP → ok.
  - After `clock.advance(3600)` → ok.
- **Other limits:** the 31st game call in an hour → 429. A second events request within 1 s → 429.
- **No side effects:** a 429 never changes the database.

**S8, nicknames (step 4):**
- **Length:** `ab` → 422, `abc` → ok, 16 characters → ok, 17 → 422.
- **Bad characters:** `<script>`, `FlоMo` with a Cyrillic о, an emoji, a tab, or leading, trailing or double spaces → 422.
- **Lookalikes:** with `FloMo` taken, `FIoMo`, `F1oMo`, `Fl0M0` and `FLOMO` → 409 `nickname_taken`. They all fold to `flomo`.
- **Blocklist:** a listed word → 422 `nickname_not_allowed`, also when spelled with `0` for `o` or split by `_`.
- **Rendering as text** is covered in E2E flow 8.

**S9, database file (step 3):**
- **Not downloadable:** `/trur.db`, `/var/trur.db`, `/../var/trur.db`, `/%2e%2e/var/trur.db`, `/.git/config`, `/fly.toml` and `/trur/app.py` → 404.
- **Location:** with default settings the database lives under `./var`, never under `web/` or `data/`.
- **Never committed:** `.gitignore` lists `var/` and `*.db`, and CI fails if `git ls-files '*.db' 'var/'` prints anything.

**S10, daily words (step 5):**
- **Import checks:** covered in step 5.
- **Fixed source:** no route accepts a URL, and an `http://` daily source makes startup fail.
- **Workflow files:**
  - every `uses:` is pinned to a 40-character SHA
  - no `pull_request_target`
  - top-level permissions are read-only, and `contents: write` appears only on the pick job

**S11, admin access (step 7):**
- **Route inventory:** the routes must equal the API tables plus static files and `/healthz`, so any new route fails the test. Once multiplayer exists, that includes its eight routes.
- **Manual checklist:** 2FA, snapshot retention and the restore drill.

**S12, web hardening (steps 3 and 7):**
- **Headers:** HTML, JS, JSON, 404, 422 and 500 responses all carry the CSP (`default-src 'self'`, `frame-ancestors 'none'`), `nosniff` and `Referrer-Policy: no-referrer`. HSTS is present with production settings.
- **Errors:** a test-only route that raises → 500 with the generic envelope and no traceback. The details go to the log.
- **Dependencies:** `pip-audit` runs in CI.

**S13, personal data (step 7):**
- **No stored IPs:** after requests with `Fly-Client-IP: 203.0.113.7`, that IP appears nowhere in the database file.
- **Deleting a player:** covered in step 7.

## 5. Frontend end-to-end (Playwright)

### Setup

- The `live_server` runs with the fixture words.
- Today's daily is MEDICAL × CORRECT, written to the fixture JSON for the real UTC date. The daily flow is skipped within 2 minutes of midnight.
- The seed is chosen so the first free game is GARDEN × SUMMER, and the test asserts that at the start.
- Selectors:
  - tiles carry `data-row`, `data-col` and `data-state`. Multiplayer tiles also carry `data-pct` and `data-locked`
  - score, tries, counter and buttons carry `data-testid`
  - these are cheap to add in step 2
- Every flow also asserts no console errors, no CSP violations and no failed requests.

### Flows

1. **First visit:** the nickname prompt appears. After entering `Tester`, a 6×6 grid shows GARDEN across and SUMMER down, score 0, three tries and `0 of 9 found`.
2. **Play a word:** click GE, AR → both tiles are selected and the solution area shows GEAR. Submit → `GEAR 4` appears in the list, score 4, `1 of 9 found`, and no tile stays selected.
3. **Undo and Clear:** click GE, AR, NE, then AR again → the solution area shows GENE. Clear → empty. No request is sent to the API apart from the batched event log (`/api/events`), which a separate check sees arrive with 204.
4. **Rejected word:** submit GS, AS → the tiles show the rejected state and then return to default. Two tries are left and the selection is empty.
5. **Game over:** after GEAR and three rejected words, the game-over screen lists the 8 missed words, and tiles can no longer be clicked.
6. **Reload:** after GEAR, reload the page → same grid, GEAR in the list, score 4, tries unchanged.
7. **Daily game:** the Daily tab shows MEDICAL × CORRECT. Playing DEMOCRATIC shows the won state. After End, no missed words appear, and a note says the answers come after midnight UTC.
8. **Nickname shown as text:** a nickname `<img src=x onerror=window.__x=1>` inserted directly into the database appears on the board as literal text, and `window.__x` stays undefined.
9. **Layout** (API mocked): 5×5 and 10×10 grids fit inside the 640×640 game area without overlapping tiles.
10. **Multiplayer match** (after go-live): two players in two browser contexts; see §6, step M6.

**Step 2:** before the backend exists, flows 2–4 and 9 run against `page.route('**/api/**')` mocks. Once step 3 lands they move to the live server, and only flow 9 keeps its mock.

### Not tested end to end

- **Game logic:** solver results, scoring, limits, rate limits, midnight logic, import and fallback, and ranking order are covered by integration tests, which are faster and exact.
- **Security plumbing:** headers, cookie flags, CSRF and injection are covered by integration tests.
- **Pixel comparison with the design:** too brittle. "Matches the design" in step 2 is checked by eye.
- **Other browsers, mobile and other viewports:** the game is desktop-only by decision.
- **Animation timing:** only "the rejected state appears and clears" is checked.

## 6. Multiplayer (after go-live)

How [multiplayer.md](multiplayer.md) gets tested. Names come from its §6. Drafted on 2026-10-11 from the multiplayer test review; the display word and creativity vote cases were added on 2026-10-12. Multiplayer is built after go-live, so none of this runs before step 8 of the plan.

### Fixtures and helpers

- **Words:** the GARDEN × SUMMER fixture and the worked example in [multiplayer.md §3.3](multiplayer.md#33-scoring). A case that needs repeated same-text tiles builds a `Match` directly with its own answer set: GARAGE × SUMMER with `{"AMAM"}` has four AM tiles, at [2,1], [3,1], [2,3] and [3,3].
- **`make_match(word1, word2, answers, players, starts_at)`** builds a running `Match` without the lobby.
- **`rng = random.Random(0)`** is passed to `close_play` and `ballot_for`, so candidate ids and ballot order are fixed.
- **`tile("GE")`** returns a tile's coordinates in the current grid. It raises if the text occurs more than once; pass coordinates then.
- **`drive(lobby, clock, picks=…, saves=…)`** calls `lobby.tick(clock.now())` and resolves the jobs it returns, synchronously:
  - a `PickJob` with a given pair, or with `LookupError`
  - a `SaveJob` with success, or with an exception

  It returns the jobs, so a test can inspect the `SaveJob` record.
- **`RecordingHub`:** a real `Hub` that also records `(user_ids, event, data)` for every publish.
- **`parse_sse()`:** turns `text/event-stream` lines into `(event, data)` pairs. It counts `: ping` comments but doesn't return them.
- **`live_match_server`:**
  - uvicorn in a thread of the test process, so coverage is still measured
  - a `FakeClock` shared with the test, and the real `run()` ticker every 10 ms
  - a test advances the clock, then waits for the expected event with a 2 s timeout. It never sleeps for a fixed time.
  - a full match takes seconds instead of 75

**Test settings:**
- `MATCH_TICK_MS = 0` in-process: the ticker is off, and API tests call `client.portal.call(drive, lobby, clock)`.
- `MATCH_TICK_MS = 10` on `live_match_server`.
- `SSE_KEEPALIVE_S = 0.2` and `MIN_MATCH_WORDS = 1`.
- `MATCH_MAX_ACTIVE` and `MATCH_MAX_QUEUE` small, so the caps are cheap to reach.
- `MATCH_SUBMIT_MIN_GAP_S = 0` and `RL_QUEUE_JOINS_PER_PLAYER_HOUR = 0`, except in their own tests.
- The 60-s vote limit is a rule, not a setting: tests advance the `FakeClock` past it.

**No sleeps:** a static check fails if `test_match.py` or `test_lobby.py` contains `sleep`.

### Step M1: rules engine (unit, `test_match.py`)

**Rounding.** These cases would catch Python's round-half-to-even:
- `pct` for `uses` 0–8 → 100, 50, 25, 13, 6, 3, 2, 1, 1. `pct(3) == 13` (12.5).
- A plays NEARER, then B plays NEARER → B scores **5** (4.5).
- After A and B both play NEARER, a third player C plays DEER → **3** (2.5).
- The worked example → 4, 8, 2 and 3. Totals: B 8, C 5, A 4. Final board: GE 25 %, AR 13 %, NE 50 %, ER 25 %, DE 50 %, the rest 100 %.
- **1-point minimum:** A, B and C play GEAR, D plays NEAR, then E plays GEAR → E scores 1.

**Rules:**
- A lock blocks your own reuse of a tile, but not other players' use of it.
- AMAM leaves both AM tiles at 50 %, which proves `uses` counts per tile, not per text.
- The same word by two players → A 4, B 2, and GE and AR end at 25 %.
- **The same word by the same player from other same-text tiles** (GARAGE × SUMMER, AMAM twice) → `duplicate`, no lock, no halving. The record saves without an `IntegrityError`.
- A rejected word changes nothing.
- Duplicate is checked before locks: rebuilding your own word with its own tiles → `duplicate`, not `tile_locked`.

**Time boundaries:**
- Before `starts_at` → not running. At `starts_at` → accepted.
- At `ends_at + 300 ms` → accepted. At `+301 ms` → not running.
- After `close_play()` → not running, whatever the time.
- `ends_in_ms` is never negative.

**Display word:**
- It locks the tiles for the sender, adds 1 to each tile's `uses`, and scores 0 points, whether or not the word is in `answers`.
- A second display word replaces the first. The first one's tiles stay locked and halved.
- A display word on a locked tile → `TileLocked`. Before `starts_at` or after `ends_at + 300 ms` → not running.
- A display word equal to a word you found is allowed, and changes nothing about that word.
- `close_play` returns False with zero or one display words, and True with two.

**Vote** (every ballot from `ballot_for` with `Random(0)`):
- A ballot never contains the voter's own word, and contains every other display word.
- `vote` refuses your own id, an unknown id, a repeated id and four entries (`ValueError`), a second ballot (`AlreadyVoted`), and anything before `close_play` or after `vote_ends_at + 300 ms` (`NotVoting`). At `+300 ms` it's accepted.
- With two candidates, a ballot of two gives 5 and 3; a ballot of one gives 5.
- `bonus(0, 0) == 0`; `bonus(15, 3) == 10` (the maximum); `bonus(5, 4) == 3` (2.5, rounded half up); `bonus(13, 3) == 9`.
- A player without a display word gets bonus 0 and can still vote.
- **The worked results example:** three players; C sets no display word; B and C each rank only A's word (5 + 5), A ranks B's (5) → two ballots count for A and two for B: A's bonus (4 × 10 + 2) // 4 = 10, B's (4 × 5 + 2) // 4 = 5; the scores and places include the bonus.

### Step M2: spike

- `spike_stats --match` on the fixture data with a fixed seed gives the same report twice. That report includes the acceptance rate and the time per pick.
- The simulated players obey the lock and duplicate rules: every simulated word passes `match.submit`.
- The vote simulation is seeded too: the same seed gives the same "winner changed by votes" rate.

### Step M3: schema and saving (integration)

- **Migration 2** applies on a fixture DB at `user_version` 1. Never use a copy of production, which holds personal data (S13).
- **The snapshot:** DB results deep-equal the in-memory results.
- **Rows:** only accepted and rejected submits become `match_submissions` rows with `kind = 'word'`. Duplicates store none.
- **Display words:** every one a player set becomes a `kind = 'display'` row with `is_valid = 0`, and `match_players.display_word` holds the last one.
- **Votes:** one `match_votes` row per ranked candidate; `match_players.score` = `word_score` + `vote_bonus`. Inserting a self-vote or a second row for the same voter and target fails.
- **The events** `match_started`, `match_word_*`, `match_display_word`, `match_vote_cast` and `match_ended` are written with their real timestamps.
- **A player deleted during the match:** the save succeeds without their rows, and they show as "deleted player". Votes they cast or received are left out of `match_votes`; the snapshot keeps the bonuses as computed.
- **After `delete-player`:** their `match_players`, `match_submissions`, `match_votes` and events rows are gone, and no stored results contain their nickname.
- **A failed save** is logged (`caplog`). The results are still served from memory for 10 minutes, and the lobby keeps forming matches.
- **S1:** the injection strings sent to the new routes leave `db_snapshot()` unchanged.

### Step M4: lobby (unit, `test_lobby.py`)

**Queue:**
- The 1st player alone never forms a match. The 2nd starts the fill window, the 6th forms the match at once, and a 7th starts a new queue.
- **Queue timeout:**
  - It fires at 120 s, but not inside a fill window. P1 joins at 0 s and P2 at 115 s → a match, no timeout.
  - A leave during the fill window that drops it to 1 player → no match.
- **Closed streams:**
  - A stream gone for 10.0 s → the player stays queued. At 10.001 s → dropped.
  - Joining without ever opening a stream → dropped after 10 s.
- **Limits:**
  - `MATCH_MAX_QUEUE` reached → 503 `server_busy` on join.
  - `MATCH_MAX_ACTIVE` reached when a group is ready → a `server_busy` event, and those players are idle.
- A join while in a match → `in_match`. Right after `end` the player is idle and can queue again.

**States:**
- `forming` publishes no `match_found` until `pair_picked`. `starts_in_ms` is 3000 from that moment.
- `pick_failed` → `match_cancelled`, and the players are idle.
- A player who leaves during the countdown is still in the results.
- `warning` is published once, even with two ticks at the same `now`.
- **Catch-up:** one tick jumping from the countdown past the end, with no display words, publishes `start`, `warning` and `end` in that order, and returns exactly one `SaveJob`. With two display words it publishes `start`, `warning` and `voting`, and no `end` until the vote is over.

**Vote:**
- running → voting when two display words exist; running → idle (`end`) when fewer do.
- Each player's `voting` event carries their own ballot, without their own word and without nicknames.
- `voted` is published after each ballot with counts only.
- The vote ends at once on the ballot that leaves every player done, and the `SaveJob` follows in the same step.
- **Absent players:** with P3 not voting and P3's stream gone since `closed_at` + 5 s, a tick at + 15.0 s keeps the vote open and a tick at + 15.001 s ends it (10 s gone).
- A non-voter who reconnects within 10 s keeps the vote open, and can still vote.
- A player who never opened a stream during the vote counts as gone from `closed_at`.
- With every stream gone, the next tick after 10 s ends the vote.
- Without any ballot, the vote ends at `vote_ends_at + 300 ms`, and every bonus is 0.
- A join while voting → `in_match`.
- The `found` event for a display word has exactly the keys of one for a real word.
- **Grace window:**
  - a tick at `ends_at + 1 ms` publishes no `end`
  - a submit at `+299 ms` is accepted
  - a tick at `+301 ms` → the word is in the `end` Results and in the `SaveJob` record

**Hub:**
- A second stream sends `replaced` to the first.
- The first stream's cleanup doesn't remove the second, so `tick(+11 s)` keeps the player queued.
- `found` reaches everyone except the finder.
- Rejected and duplicate submits publish nothing.
- `snapshot` is always the first item in a new stream's queue.

**Board picking:**
- Over seeds 0–49, neither today's nor tomorrow's daily word appears in a match pair.
- A pair with exactly `MIN_MATCH_WORDS` answers is accepted, and one with one fewer is rejected.

### Step M5: API and stream (`test_match_api.py`)

**In-process (`TestClient`):**
- **Auth:** every match route without a cookie → 401. A non-player → 404 on submit, stream and results, the same as for an unknown match. A non-UUID match ID → 404.
- **Contract:** status codes and the error envelope follow the [multiplayer.md §6.4](multiplayer.md#64-api-contract) table.
  - The submit response contains no points.
  - `{"score": …}` in a body → 422.
  - The stream when idle → 204.
- **Rate limits:** the queue-join rate limit. Submits 0.4 s apart → 429 with no change; 0.5 s apart → both handled. A display word 0.4 s after a submit → 429, because they share the gap.
- **Vote:** `POST …/vote` before the vote → 409 `match_not_voting`; a second ballot → 409 `already_voted`; your own or an unknown candidate id → 422 `invalid_ballot`. Results during the vote → 404.
- **Payload leaks (M2):** every MatchView and stream event, in every state, is checked against a key allow-list. Nothing may contain:
  - `word1` or `word2` before `start`
  - another player's words, or any score
  - `total_words` before `end`
  - another player's display word before `voting`, or a nickname next to one during the vote
  - a ballot, or who voted for whom, anywhere
  - a user ID, anywhere

**Live server (`live_match_server`, `httpx`):**
- **A full match:** two clients queue, play and get identical Results.
- **Concurrency:**
  - 20 parallel identical submits from one player (gap 0) → 1 accepted, 19 `duplicate`, no 5xx, one row saved.
  - Two players submit GEAR in parallel → scores 4 and 2 in some order, and GE and AR end at 25 %.
  - 10 parallel identical ballots from one player → one 204, nine `already_voted`, one set of `match_votes` rows.
- **SSE:**
  - `snapshot` comes first, and a reconnect gets a fresh one
  - a second stream sends `replaced` to the first
  - the keepalive arrives
  - the stream closes after `end`, and the next GET returns 204
- **Shutdown:** with streams open, the server stops within 4 s, and a pending save completes.
- **Restart detection** (`stream.js`, E2E on `live_match_server`): stop the server mid-match and start it again. The page shows "Match cancelled" and doesn't keep reconnecting.

### Step M6: frontend (E2E flow 10)

Two `browser.new_context()` on `live_match_server`, with separate cookies and nicknames:
- Both queue and see the fill countdown, then 3-2-1 without a grid. After GO, both see GARDEN × SUMMER (a seeded pick).
- Player 1 plays GEAR:
  - player 2 sees "*nickname* found a 4-letter word" (auto-waiting `expect`; popups stay at least 3 s)
  - GE and AR show `data-pct="50"` on both screens
  - GE and AR show `data-locked` on player 1's screen only
- Each player sets a display word. Player 2 sees the same "found a word" popup as for a real word.
- The test advances the clock past the end. Both see the vote screen with the other's display word (`data-candidate`) and no names. Both vote; the vote ends without waiting for the 60 s.
- Both see the same results, with each display word, its votes and the bonus, and reloading the results link shows them again.
- No `/stream` request is made within 5 s after `end`.
- Timer text isn't asserted, because the fake clock and the browser's own clock differ.

### Step M7: release

- **Static:**
  - `fly.toml`'s `hard_limit` and the settings defaults satisfy `MATCH_MAX_ACTIVE × MATCH_MAX_PLAYERS + MATCH_MAX_QUEUE + 25 ≤ hard_limit`
  - the Dockerfile CMD contains `--timeout-graceful-shutdown`
- **Smoke, read-only:** `/api/match/current` and `/api/match/stream` without a cookie → 401.
- **By hand:**
  - one match between two devices on production
  - a redeploy during a test match finishes within seconds, and both browsers show "Match cancelled"

### Step M8: power-ups

These are written with M8, once the open questions in [multiplayer.md §13](multiplayer.md#13-open-questions) are answered:
- each power-up gets unit tests for its effect
- personal hints are checked to reach only their owner

### Coverage

- 100 % branch coverage for `trur/match.py`, and for `trur/lobby.py` except the `run()` loop.
- `--cov-fail-under` only sets the overall gate, so CI runs an extra step (§7).

## 7. CI

### `.github/workflows/tests.yml`

Runs on every push and pull request.
- **Workflow settings:**
  - `permissions: contents: read`
  - actions pinned to SHAs, as S10 requires
  - a `concurrency` group that cancels runs replaced by a newer push
- **Job `test`:**
  - **Environment:** ubuntu, the production Python version, `TZ=Pacific/Kiritimati`, and a pip cache keyed on the lockfile.
  - **Runs:**
    - `pytest -m "not e2e and not smoke" --cov --cov-fail-under=85`
    - once multiplayer exists: `coverage report --include=trur/match.py,trur/lobby.py --fail-under=100`
    - `python -m scripts.pick_daily --dry-run` against the real data
    - the `git ls-files` database check
    - `pip-audit`
  - **Real data:** `realdata` also checks the committed `data/daily_challenges.json`:
    - every entry passes import validation
    - no date appears twice
    - no pair repeats, in either order
  - **Time budget:** under 2 minutes.
- **Job `e2e`** (in parallel): a cached Chromium, `pytest -m e2e`, and the Playwright trace uploaded on failure. Under 4 minutes.
- **Total:** the whole run stays under 5 minutes. A test that pushes past that gets smaller data rather than being skipped.

### Smoke test after a Fly deploy

- **How to run:** `pytest tests/smoke --base-url https://trur.fly.dev`, by hand right after `fly deploy`. Deploys are manual and no Fly token lives in CI (S11).
- **Read-only:** it takes about 10 s and allows 15 s for a cold start.
- **Checks:**
  - `/healthz` → 200, and `/` → 200 with all the security headers
  - `/trur.db`, `/docs` and `/openapi.json` → 404
  - a POST from another origin → 403
  - `/api/rankings?mode=free&month=<current>` → 200, and each row has only `nickname`, `score` and `rank`
  - from the second day on: `/api/daily/<yesterday>/answers` → 200 and not empty, while today's answers → 403
- **Go-live only (step 7):**
  - Run flows 1, 2 and 7 against production with a throwaway player.
  - Then delete that player with `scripts.admin delete-player`.
  - Restart persistence and the snapshot restore stay manual checks.

### Testing the daily-pick workflow

- **The pick logic** is covered by the step 5 tests, and the `test` job dry-runs the script on real data on every push.
- **Dry-run input:** `daily-challenge.yml` has a `workflow_dispatch` input `dry_run` (default false), and the commit step runs only `if: ${{ !inputs.dry_run }}`. Scheduled runs have no inputs, so they still commit.
- **After changing the workflow or `pick_daily.py`:**
  1. Run it by hand with `dry_run: true`. The pair appears in the log and nothing is committed.
  2. Run it once for real.
  3. Run it again. There must be no new commit.
- **Static checks:** S10 covers this file.
- **Failures:** a failed scheduled run emails the repo owner. The fallback covers that day, and rows with `picked_by = 'fallback'` are the signal to investigate.

## 8. Resolved gaps

The review found these gaps in the plan, and they are now settled:

| Gap | Where it's settled |
| --- | --- |
| No injectable clock or random seed | [architecture.md](architecture.md) §2 and §8 item 20 |
| Pair checked in one orientation, then flipped | §8 item 19 |
| Retry loops had no limit | `max_attempts`, `503 no_pair_available`, and `pick_daily` exits non-zero |
| Thresholds missing | named settings, §5 |
| Lookalike folding couldn't match | `fold_nickname` |
| Missing status codes | the error table, §3 |
| Stale ranks after a flag change | §8 item 1 |
| Sweep on a read-only connection | §8 item 14 |
| Open free games | §8 item 18 |
| "10-letter word" unclear | §8 item 2 |
| Missing Origin and allowed origins | `ALLOWED_ORIGINS`; a missing Origin gets 403 |
| `/docs` exposed | turned off unless `DEBUG` |
| Ties in rank | §8 item 12 |
| Month of a submission | §8 item 16 |
| Word-list path couldn't be set | `LEXICON_DIR` |
| `pick_daily` had no command-line options | §8 item 20 |
| Re-submitting a rejected word | costs a try again |
| Order of missed words and empty `current` | points then A–Z; `{"game": null}` |
| No SQLite busy timeout | `busy_timeout=5000` |
| The submit gap hid the parallel test | `SUBMIT_MIN_GAP_S=0` in that test |
| Multiplayer rounding tests passed with round-half-to-even | 4.5 → 5, 2.5 → 3 and 12.5 → 13 cases (§6, M1) |
| A grace-window submit after the match had ended | close only after the grace; tested in §6, M4 |
| One player scoring the same word twice broke the save | the per-player duplicate rule; tested in §6, M1 |
| An endless SSE stream hangs `TestClient` | `live_match_server` (§6) |
| Lobby timing needed real sleeps | a synchronous `tick()` that returns jobs, plus `drive()` |
