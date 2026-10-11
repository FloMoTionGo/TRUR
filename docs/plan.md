# TRUR – Plan

Notes from `requirements`, plus the answers to clarifying questions (2026-10-11).

## Goal

A browser word game. Two source words make a grid of letter pairs. The player clicks pairs to build words. Longer words score more points. There are two solo modes, and a multiplayer mode follows after go-live:

- **Free play:** a random word pair each game.
- **Daily challenge:** every player gets the same word pair that day, and the goal is to find a word of 10 or more letters.
- **Multiplayer:** 2–6 players race on one board for 60 seconds, and every use of a tile halves its value for everyone. Designed in [multiplayer.md](multiplayer.md).

## Decisions so far

| Topic | Decision |
| --- | --- |
| Source words | Taken from the 500 most common Canadian English words that are **5–10 letters** long ("syllables" in the requirements meant letters) |
| Grid | **Combination matrix.** Word 1's letters run across the top, word 2's letters down the side, and each cell = (letter of word 1) + (letter of word 2). There are no extra reversed tiles |
| Letter order | **Fixed:** the horizontal word's letter always comes first and the vertical word's letter second. GARDEN × SUMMER gives GS, never SG |
| Tiles | **Every cell is its own tile; nothing is merged or removed.** This keeps double letters (EE), repeated tiles (GM twice, from the two Ms in SUMMER) and pairs that happen to appear both ways (RE and ER) |
| Tile use | **Each tile once per word.** After submit, every tile is free again. Tiles with the same text are separate, so two GM tiles mean GM can be used twice in one word |
| Valid words | Checked against a **full Canadian English dictionary**, not just the 500-word list. **Plurals and other inflected forms count** (GENRES, NEARER, RERUNS) |
| 3 tries | The game ends after the **3rd rejected word**. Valid words don't use a try |
| Game end | Also ends **automatically when every word is found**, or when the player presses **End game** |
| Undo | Clicking a selected tile again removes it from the word. A **Clear** button empties the whole selection |
| Scoring | **points = tiles²**: 4 letters = 4, 6 = 9, 8 = 16, 10 = 25 |
| Word count | A **live counter** ("4 of 23 found") during play. **Missed words** are listed when a free-play game ends; for the daily challenge they appear **only after the day ends** |
| Daily challenge | **One game per player per day.** All valid words score points. Finding a word of **10 or more letters** marks the challenge **won**, and play continues until the game ends. Daily games **close at midnight UTC** |
| Free play limit | **At most 20 free games per player per day.** Starting a new one ends the open one, and every started game counts. An open free game **closes at midnight UTC** |
| Daily words | Picked by a **GitHub Actions workflow** about 3 hours before the day starts, then committed to the repo. The same two words **never appear together again**. The rare backend fallback checks the database for repeats too |
| Day boundary | **Midnight UTC** (8 pm Toronto in summer, 7 pm in winter). Daily dates and ranking months use UTC |
| Free play words | The backend picks them for each new game: two random source words, **never either of today's daily words** |
| Rankings | A **monthly board per mode** plus **one board per daily challenge**, all ranked by **best single game**. Flagged games and hidden players don't appear |
| Devices | **Desktop only** for now (fixed 640×640) |
| Logo | **The TRUR matrix:** TU × RR, a 2×2 grid with TR and UR marked on the diagonal (see [Logo](#logo)) |
| Layout design | Designed in **Claude Design** before the frontend is built (see [Designed in Claude Design](#designed-in-claude-design)) |
| Java | **Not needed.** HTML/CSS/JavaScript frontend, Python backend |
| Users | **Anonymous player + nickname.** The browser keeps a secret player token in an HttpOnly cookie. There is no login or password |
| Event table | **Gameplay log** (game started, tile clicked, word submitted/rejected, game ended) |
| Database | **SQLite.** Locally it's a file in `./var`, which git ignores. On Fly.io it's a file on a 1 GB volume, backed up by Fly's daily snapshots |
| Security | The fixes from the [Security audit](#security-audit) are part of the build steps |
| Implementation | Module layout, API contract and schema in [architecture.md](architecture.md); tests in [test-strategy.md](test-strategy.md) |
| Multiplayer | **Public quick-match for 2–6 players**, 60 s on one shared board. Each use of a tile halves its value for everyone, and scores stay hidden until the end. Rules, design and build steps in [multiplayer.md](multiplayer.md). It's built **after go-live** (step 8) |
| Hosting | **Fly.io app `trur`** in Frankfurt (`fra`), separate from GoCalypse. It **stops when idle** and **goes live at the end** of the solo build (step 7); multiplayer follows after go-live |

## Game rules

### Grid

Example: word 1 = `GARDEN` (columns), word 2 = `SUMMER` (rows):

```text
        G    A    R    D    E    N
  S   [GS] [AS] [RS] [DS] [ES] [NS]
  U   [GU] [AU] [RU] [DU] [EU] [NU]
  M   [GM] [AM] [RM] [DM] [EM] [NM]
  M   [GM] [AM] [RM] [DM] [EM] [NM]
  E   [GE] [AE] [RE] [DE] [EE] [NE]
  R   [GR] [AR] [RR] [DR] [ER] [NR]
```

Words found in this grid include:

- 4 letters: `GE+AR` = GEAR, `DE+ER` = DEER, `NE+AR` = NEAR
- 6 letters: `NE+AR+ER` = NEARER, `DE+AR+ER` = DEARER, `GE+NR+ES` = GENRES, `RE+RU+NS` = RERUNS

What this means:

- Grid size is len(word 1) × len(word 2), from 5×5 to 10×10, so at most 100 tiles.
- Every built word has an **even length**. Its odd-position letters come from word 1 and its even-position letters from word 2.
- A 10-letter word is 5 tiles.
- RE (column R, row E) and ER (column E, row R) are both in the grid because R and E appear in both words. Reversed pairs exist only when they come up naturally like this.

### A turn

1. The player clicks tiles. Each one appears in the solution area in click order and stays highlighted.
2. To undo, the player clicks a selected tile again to remove it, or presses **Clear** to empty the whole selection.
3. The player presses **Submit** (the requirements' "check" and "submit" buttons are treated as one button).
4. If the word is valid, it is added to the found-solutions list, its points are added to the score, and the counter goes up ("5 of 23 found").
5. If the word is invalid, a red reject animation plays and the player loses one try.
6. Either way, the highlights and the solution area are cleared.
7. The game is over after 3 rejected words, when every word has been found, or when the player presses **End game**. In free play the missed words are listed then; in the daily challenge they appear after the day ends.

### Scoring

`points = tiles²`, so 4 letters = 4, 6 = 9, 8 = 16 and 10 = 25 points.

### Daily challenge

- A new challenge starts at **midnight UTC**.
- A GitHub Actions workflow picks each day's pair (see [Picking words](#picking-words)). Only pairs with **at least one valid word of 10 or more letters**, in the orientation that will be shown, qualify.
- Each player gets **one game per day**, enforced by the server. The server sets the game's date; the browser can't choose it.
  - Clearing cookies creates a new player and another daily game. That's accepted: the game itself doesn't show the answers before the day is over (S3). Someone determined could still compute them from the public word lists (S4).
- All valid words score points, the same as in free play.
- Finding any valid word of 10 or more letters marks the challenge **won**. Play continues until the game ends normally.
- A daily game **closes at midnight UTC**. Submits after that are rejected.
- **Missed words appear only after the day ends.** Then anyone can see that day's full answer list.

### Free play

- **At most 20 free games per player per day** (UTC day).
- **One open free game at a time.** Starting a new one ends the open one with its current score.
- Every started game counts toward the limit, so rerolling for an easy grid quickly runs out (S6).
- Missed words are listed as soon as the game ends.
- **An open free game closes at midnight UTC** with its current score. Until then, a reload resumes it.

### Rankings

- **Monthly boards:** one for free play and one for the daily challenge. Each ranks players by their best single-game score that month.
- **Daily board:** one for each day's challenge, ranked by points. Each player has exactly one game there.
- **Kept off every board:** games flagged by the plausibility check, and players marked hidden (S4).
- **Rank is worked out when a board is requested**, never stored. Hiding a player or clearing a flag takes effect immediately.
  - Each player's best score is cached.
  - The cache is rebuilt when a game ends and when an admin flags or unflags a game.
- **Abandoned games:** any open game, daily or free, is closed with its current score once midnight UTC of its day has passed.
  - This happens when a request touches it, and in a sweep at startup and before a board is served.
  - There is no timer, because the machine stops when idle.
- **A game's month** comes from `daily_date` for daily games and from `started_at` for free play.

## Layout & style

All of it fits a fixed **640×640 px** game area. This sketch is the starting brief for the design ([Designed in Claude Design](#designed-in-claude-design)):

```text
+--------------------------------------------------+
| ▚ TRUR  Score 26  Tries ● ● ○       Free | Daily |   header (▚ = compact logo)
+-------------------------------+------------------+
|       G  A  R  D  E  N        | Found 4 of 23    |
|  S   GS AS RS DS ES NS        |  GEAR       4    |
|  U   ..                       |  DEER       4    |
|  ...                          |  ...             |
|  grid up to 10×10, ~40px tiles|                  |
+-------------------------------+------------------+
| [GE][AR]  → GEAR       [End] [Clear] [Submit]    |   solution area
+--------------------------------------------------+
```

- **Palette** (matte, starting values): warm orange `#D9853B`, olive green `#6B7B3A`, near-black `#1E1E1E`, white `#FFFFFF`. Red `#C0392B` is used only for the reject animation.
- **Text colour per background** (decided 2026-10-11, for WCAG AA contrast of at least 4.5:1):
  - near-black on white (about 17:1) and on orange (about 5.9:1)
  - white on olive (about 4.6:1) and on red (about 5.4:1)
  - never white on orange (about 2.9:1) or near-black on olive (about 3.6:1)
  - The logo is exempt, so it keeps its black lettering.
- **Chosen direction: "Olive board"** (owner, 2026-10-11, from the [screen mocks](https://claude.ai/artifact/4cJYXZSdvg5hD8mLkxaQSU)):
  - white ground
  - olive tiles (`#6B7B3A`, edge `#4D5829`) with white letters
  - orange selected tiles with near-black letters
  - olive headings and active states
  - light olive panels (`#EEF0E6`)
  - Fonts: Space Grotesk for the interface, Space Mono for tiles and numbers, both self-hosted.
- **Look:** flat design with a sharp, subtle drop shadow (no blur), e.g. `box-shadow: 3px 3px 0 rgba(0,0,0,.2)`.
- **Tile states:** default, selected (orange) and rejected (red shake, about 400 ms).

### Logo

The logo is the smallest TRUR grid: **TU** across the top and **RR** down the side. The 2×2 matrix holds TR and UR twice. The two marked tiles (`*`) on the diagonal read TR + UR = **TRUR**:

```text
        T     U
  R   *TR*  [UR]
  R   [TR]  *UR*
```

- **Full logo:** the 2×2 matrix with its headers (T U across, R R down). The marked tiles, TR top-left and UR bottom-right, are warm orange; the other two are olive green.
  - Use it on the start screen, in the README and for link previews.
- **Compact logo:** the same four tiles without headers.
  - Use it in the header and as the favicon.
  - At favicon size it becomes two orange squares on a diagonal (▚), the same shape as the game's tiles.
- **Same style as the game:** flat tiles with the sharp drop shadow, black lettering, white background.
- **A small puzzle for players:** anyone who knows the rules can read TR + UR = TRUR.
- **Final colours, proportions and lettering** are settled in Claude Design.

### Designed in Claude Design

**Requirement:** the layout and the logo are designed in **Claude Design** (claude.ai/design) before the frontend is built. The sketch, palette, look and tile states above are the brief.

**Screens to design** (all 640×640):

1. **Free-play game screen:** header with compact logo, score, tries and mode switch; the grid; found words with the counter; solution area with End, Clear and Submit.
2. **Daily challenge screen:** including the "won" state, and a note that the answers appear after midnight UTC.
3. **Game over:** free play lists the missed words; daily doesn't.
4. **Leaderboards:** monthly per mode, and the board for one day.
5. **Nickname prompt** for new players.
6. **Past answers:** the full answer list for an earlier day, with the player's own finds marked. This is where daily answers become visible after midnight UTC.

**Also design:**

- **Grid sizes** 5×5 and 10×10, to check tile size and fit.
- **Tile states:** default, selected and rejected.
- **The full and compact logo.**

**Output:**

- **One approved design** that the frontend copies. Colours, sizes, spacing and fonts come from it, and the ASCII sketch stops being the reference.
- **Multiplayer screens 7–10** (queue, countdown, match, results) join this brief when multiplayer is built. They're listed in [multiplayer.md §8](multiplayer.md#8-layout-claude-design).
- **The design link** goes in the README.
- **The CSP (S12) applies to the build:** fonts are self-hosted, and styles live in CSS files, not inline.
- **Optional:** if reusable parts grow out of it (tile, button, colour tokens), they can become a Claude Design design system, kept in sync with `web/` through `/design-sync`.

## Architecture

The detailed implementation design is in **[architecture.md](architecture.md)**:

- repo layout and modules
- the exact API contract with error codes
- the SQLite schema
- configuration, dependencies and local dev commands

The test plan for every build step and security finding is in **[test-strategy.md](test-strategy.md)**. Multiplayer has its own design doc, **[multiplayer.md](multiplayer.md)**. This section keeps the overview.

```text
                                    ┌──────── Fly.io app "trur" (fra) ────────┐
Browser (HTML/CSS/vanilla JS)  ──>  │  Python backend (FastAPI)  ──>  SQLite  │
                                    │                 on /data volume (1 GB)  │
                                    └────────────▲────────────────────────────┘
                                                 │ fetches new daily pairs
GitHub Actions (daily)  ──commits──>  data/daily_challenges.json (in the repo)
```

- The **backend is authoritative.** It checks words, counts tries and records scores, so rankings can't be faked from the browser.
- On submit, the frontend sends **tile coordinates**, not the word text. The server rebuilds the word from the grid and checks that it can actually be built.
- The backend also serves the static frontend, **from the `web/` folder only**, so there is one process to run locally.
- **Every endpoint follows the audit rules:**
  - the player comes from the cookie, and the server checks they own the game (S2)
  - SQL uses placeholders only (S1)
  - input is checked with Pydantic (S1)
  - request size and rate limits apply (S7)

### API (summary)

Full request and response shapes, status codes and the error format are in [architecture.md §3](architecture.md#3-api-contract).

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/users` | `{nickname}` → sets the player cookie, returns `{nickname}` |
| GET | `/api/users/me` | the player's nickname, or 401, which tells the frontend to show the nickname prompt |
| POST | `/api/games` | `{mode}` → `{game_id (UUID4), word1, word2, tries_left, total_words}`. For `daily`, it returns the player's existing game for today if there is one |
| GET | `/api/games/current?mode=` | the player's open game, with found words, so a reload can resume it; `{"game": null}` if none |
| POST | `/api/games/{id}/submit` | `{tiles:[[row,col],…]}` → `{status (accepted / duplicate / rejected), word, points, tries_left, found_count, won, game_over, missed_words (free play, once game_over)}` |
| POST | `/api/games/{id}/end` | player ends the game → `{score, missed_words (free play only)}` |
| GET | `/api/daily/{date}/answers` | full answer list for a past day; refused for today and later |
| POST | `/api/events` | batch of client-side events, e.g. tile clicks (at most 50). The server logs game start, submits and game end itself |
| GET | `/api/rankings?mode=&month=` | monthly board: nickname, score, rank |
| GET | `/api/rankings/daily?date=` | board for one daily challenge: nickname, score, rank |
| GET | `/healthz` | Fly health check |

- **No endpoint takes a `user_id`.** The server finds the player from the cookie.
- **No endpoint accepts a score, points or the won flag** (S5).
- **Tile coordinates:** in `[row, col]`, `row` indexes word 2 (down the side) and `col` indexes word 1 (across the top).

### Database tables

Tables from the requirements:

- **words**: `id, word, length, is_source (top-500 flag), freq_rank`. Holds the full dictionary, with the source words flagged.
- **events**: `id, user_id, game_id, type, payload (JSON), created_at`. This is the gameplay log. It stores no IP addresses. Rows older than 90 days are deleted at startup.
- **submissions**: `id, user_id, game_id, game_mode, word, is_valid, points, submitted_at, month ('YYYY-MM')`.
  - A unique index on `(game_id, word)` where `is_valid = 1` stops a word from scoring twice.
  - Re-submitting an already-found word stores no row and returns `status: duplicate`.
- **rankings**: `user_id, game_mode, period ('YYYY-MM' for monthly boards, a date for daily boards), best_score`.
  - It's a cache, rebuilt from `games` when a game ends or a flag changes.
  - Rank is computed when a board is requested, not stored.

Tables I'm adding because the requirements need them to work (see Assumptions):

- **users**: `id (UUID, internal only), token_hash, nickname, nickname_key, hidden, created_at`.
  - `nickname_key` is the folded nickname, and it is unique, so lookalikes collide (S8).
  - `token_hash` is the SHA-256 hash of the cookie token.
  - `hidden` keeps the player off every board.
- **games**: `id (UUID4), user_id, mode, daily_date, word1, word2, total_words, started_at, ended_at, wrong_count, score, won, flagged`.
  - `total_words` is stored at creation, so a dictionary change in a deploy can't shift a running game.
  - The server needs it to enforce the 3-try rule, the one-daily-game rule (unique on `user_id` + `daily_date`) and the free-play limit.
  - `flagged` is set by the plausibility check.
  - It also gives a later time limit somewhere to live.
- **daily_challenges**: `date (UTC), word1, word2, picked_by ('workflow' | 'fallback')`. It makes sure every player gets the same pair.
- **meta**: a key–value table for bookkeeping, such as a fingerprint of the loaded word lists.

The full SQL with every constraint and index is in [architecture.md §4](architecture.md#4-sqlite-schema-migration-1).

## Word data & solver

- **Dictionary:** a Canadian English word list. SCOWL has an `en_CA` variant; check its licence and size first. Filter out proper nouns, abbreviations and offensive words. Keep inflected forms.
- **Source list:** the 500 most frequent dictionary words of 5–10 letters. A frequency list is needed for this, e.g. the `wordfreq` Python package (to be verified).
- **Solver:** for a word pair, scan the dictionary for even-length words whose letter pairs all exist in the grid, respecting how many times each tile appears. This is a linear scan of about 100k words, a few milliseconds, so it can run when each game is created.
- **Uses of the solver:**
  - reject word pairs with too few solutions in free play
  - pick daily pairs that have a word of 10+ letters, in the workflow
  - give the "x of y found" total and the missed-words list (free play at game end, daily after the day ends)
- The solver is one Python module, shared by the backend and the workflow's pick script.

## Picking words

### Daily challenge: GitHub Actions

**Workflow:** `.github/workflows/daily-challenge.yml`

- **Triggers:**
  - `schedule: '17 21 * * *'`, which is 21:17 UTC, about 2¾ hours before midnight UTC. It's deliberately off the full hour, when GitHub is busiest.
  - `workflow_dispatch`, so a run can also be started by hand.
- **Steps:** check out the repo, set up Python, run `scripts/pick_daily.py`, then commit and push `data/daily_challenges.json` if it changed. The commit is made as `github-actions[bot]`.
- **Hardening (S10):**
  - Actions are pinned to a commit SHA.
  - `permissions: contents: write` is granted to this job only.
  - The repo's default workflow permissions are read-only.
  - There are no `pull_request_target` triggers.

**`scripts/pick_daily.py`:**

1. It fills every date from today to tomorrow (UTC) that has no pair yet, so a run delayed past midnight doesn't skip a day. If both dates already have a pair, it does nothing, so a rerun is always safe.
2. It picks two different random source words **and which one goes horizontal**.
3. It then checks that exact orientation, because the grid isn't symmetric. A pair is skipped if that orientation has no word of 10+ letters, or if those two words were already paired on an earlier date (in either order).
4. It appends `{date, word1, word2}` to the JSON file.
5. If no pair qualifies after a capped number of attempts, it exits with an error.
6. Options for tests: `--today`, `--seed`, `--data` and `--dry-run`.

**Backend import:**

- The backend fetches the JSON file from its raw GitHub URL and inserts any dates it doesn't have yet.
  - It does this at startup and whenever a daily game is requested for a date the database doesn't have.
  - There's no hourly timer, because the Fly machine stops when idle and a timer wouldn't run.
- A local file path can replace the URL for tests.
- Raw URLs are cached for a few minutes, which is fine with a 3-hour lead.
- A date that is already in the database is never overwritten.
- **Each entry is checked before import (S10):**
  - the date is valid
  - both words are in the source list, and they are different
  - the pair, in its stored orientation, has a word of 10+ letters

  Anything else is skipped and logged.
- **The fetch itself** uses a fixed URL from config, HTTPS, a timeout and a size cap.

**Fallback:**

- If today still has no pair after that fetch, the backend picks one itself, using the same code. It stores the pair with `picked_by = 'fallback'` and logs a warning.
- The fallback checks repeats against the `daily_challenges` table as well as the JSON file.
- `date` is the primary key, and the pair is inserted with `INSERT OR IGNORE` and then read back, so two simultaneous first requests end up with the same pair.
- A later workflow commit for that date is ignored.
- Fallback pairs aren't in the JSON file, so the workflow's no-repeat check can't see them. They should be rare, so that's accepted.
- This "make sure today has a pair" step also runs before a free-play pair is picked. Otherwise free play could pick today's daily words before anyone has started a daily game.

**What follows from the repo being public:**

- Tomorrow's words can be seen in the repo for about 3 hours before they go live. That's accepted.
- GitHub can delay scheduled runs at busy times and sometimes drops them. The off-hour start, the 3-hour lead, the manual trigger and the fallback cover this.
- GitHub disables scheduled workflows in public repos after 60 days without repository activity. Check whether the workflow's own daily commits count as activity. If they don't, re-enable the workflow from the Actions tab.
- One bot commit a day lands on `main`. If that gets noisy, the workflow can write to a separate `daily-data` branch instead.
- If `main` gets branch protection later, the bot needs an exception to push.

### Free play: backend

When a free-play game is created (`POST /api/games`, mode `free`):

1. The backend picks two different random source words, and which one goes horizontal.
2. It rejects the pair if either word is one of today's (UTC) daily words, or if the solver finds fewer than N valid words in that orientation. N is set after the feasibility spike.
3. If the pair is rejected, the backend tries again, up to a capped number of attempts.

## Hosting (Fly.io)

TRUR becomes its own Fly app beside `gocalypse`, set up the same way.

**`fly.toml`** at the repo root, next to a `Dockerfile` that runs uvicorn with one worker on port 8080. It's modelled on GoCalypse's `server/fly.toml`:

- `app = "trur"` and `primary_region = "fra"`. The name `trur` must still be free on Fly; pick another if it isn't.
- `[env] DATA_DIR = "/data"`. Locally `DATA_DIR` defaults to `./var`, which `.gitignore` excludes along with `*.db`. The same code runs in both places, and the database never sits next to the committed `data/` files (S9).
- `[mounts] source = "trur_data"`, `destination = "/data"`. The SQLite file lives at `/data/trur.db`.
- `[http_service]`:
  - `auto_stop_machines = "stop"`, `auto_start_machines = true`, `min_machines_running = 0`, like GoCalypse staging
  - `force_https = true` (S12)
  - a health check on `/healthz`
  - `[http_service.concurrency]` limits (S7)

**One-time setup:**

- Install `flyctl` on this PC; it isn't installed yet.
- Run `fly apps create trur` and `fly volumes create trur_data --region fra --size 1 -a trur`.

**Deploying:** `fly deploy` by hand, as GoCalypse does. The backend serves the frontend too, so it's one app at `https://trur.fly.dev` with no CORS setup.

**Backups:** Fly snapshots the volume daily and keeps snapshots for 5 days by default. Raise retention to 14 days, so tampering that's noticed late can still be rolled back (S11). Test one restore before launch.

**Accounts:** turn on 2FA for GitHub and Fly. Store no Fly API token anywhere while deploys are manual (S11).

**Accepted trade-offs:**

- **One machine, one volume.** Fly recommends two, but SQLite on a volume can't be shared between machines. If that server's disk fails, up to a day of data is lost; a restore comes from the latest snapshot. GoCalypse accepts the same risk.
- **Each deploy restarts the only machine**, so the game is down for a few seconds.
- **Stop when idle:** the first visitor after a quiet spell waits a second or two while the machine starts.
- **If losing a day of scores becomes unacceptable:**
  - add Litestream, which backs up every write continuously to object storage, or
  - move to Fly Managed Postgres, from $38/month.

## Security audit

This audit of the plan was done on 2026-10-11, before any code exists. Each finding's **Plan status** describes the plan before the fixes; the fixes have since been applied (see [Changes made to the rest of the plan](#changes-made-to-the-rest-of-the-plan)). It focuses on three things:

- SQL injection
- skewing the rankings
- damaging the database from outside

**What's at stake:**

- whether the rankings can be trusted (the most likely target)
- whether the database stays up and intact
- player data: nicknames, player IDs and the gameplay log

**Ways in from outside:**

1. **The public API.** Every request body, query parameter, header and cookie is under the attacker's control.
2. **The daily-pairs JSON** that the backend fetches from GitHub.
3. **The GitHub repo** and its Actions workflow.
4. **The Fly.io account and machine**, through SSH and deploys.

**Severity:**

- **High:** directly skews the rankings or destroys data.
- **Medium:** abuse with limited impact, or abuse that needs extra conditions.
- **Low:** general hardening.

### Summary

| ID | Severity | Risk | Lands in step |
| --- | --- | --- | --- |
| S1 | High | SQL injection, including dropping tables | 3 |
| S2 | High | Playing as someone else, or tampering with their games | 3–4 |
| S3 | High | Getting the full daily answer list before playing | 5 |
| S4 | High | Bots solving every grid perfectly | 6 |
| S5 | High | Double scoring and playing after the game has ended | 3 |
| S6 | Medium | Rerolling free play until a grid is easy | 6 |
| S7 | Medium | Filling the disk or running up costs | 3–4, 7 |
| S8 | Medium | Nicknames that inject scripts or copy another player | 4 |
| S9 | Medium | The database file being published or downloaded | 3 |
| S10 | Medium | Tampering with the daily words | 5 |
| S11 | Medium | Your own accounts as the real way to delete everything | 7 |
| S12 | Low | Standard web hardening | 3, 7 |
| S13 | Low | Personal data | 7 |

### S1 (High): SQL injection, including dropping tables

**Plan status:** not addressed yet.

**Attack:** someone picks a nickname like `x'); DROP TABLE submissions;--`, or puts SQL in `mode`, `month` or `date`. This works if any of those values is pasted into an SQL string.

**Fix:** all of these are mandatory.

- **Placeholders everywhere.** Every query uses `?` placeholders (Python `sqlite3`). SQL is never assembled with f-strings, `format()` or `+`.
  - SQL names that can't be placeholders, such as the sort column, come from a fixed list in the code.
- **No `executescript()` on input.** Never call `executescript()` with anything that comes from a request.
  - Python's `execute()` refuses to run a second statement, which blocks the classic `; DROP TABLE`. It does not block injection inside a single statement, so placeholders are still required.
- **Check every input at the API boundary** with Pydantic models:
  - `mode` is `free` or `daily`
  - `month` matches `YYYY-MM`
  - `date` is a real ISO date
  - `tiles` is a list of `[row, col]` integer pairs inside the grid, at least 2 and no more than the longest dictionary word needs, with no tile used twice
- **A second line of defence in SQLite:**
  - The connection that handles requests installs a SQLite authorizer (`Connection.set_authorizer`). It refuses DROP, ALTER, CREATE, ATTACH/DETACH and PRAGMA writes.
  - Schema setup runs once at startup, on a separate connection.
  - The ranking endpoints use a read-only connection (`file:…?mode=ro`).
- **A test** sends common injection strings to every endpoint, then checks that the schema and row counts haven't changed.

### S2 (High): playing as someone else, or tampering with their games

**Plan status:** the API sketch sends `user_id` in request bodies, the ID sits in browser storage, and the game ID format isn't set.

**Attacks:**

- If game IDs count up (41, 42, …), anyone can call `POST /api/games/41/end` to end a stranger's game, or send junk to `/submit` to use up their 3 tries.
- If a ranking response includes `user_id`, anyone can copy a top player's ID. They can then play under that name, or play that player's daily game first, which locks the real player out for the day.

**Fix:**

- **A secret player token replaces `user_id` in request bodies.**
  - `POST /api/users` sets a random 256-bit token in an `HttpOnly; Secure; SameSite=Lax` cookie, and the server finds the player from that cookie.
  - The database stores only a SHA-256 hash of the token.
  - Because the cookie is HttpOnly, injected scripts can't read it.
- **Ownership checks.** Every game endpoint checks that the game belongs to the cookie's player. If it doesn't, the response is 404.
- **Random game IDs** (UUID4), not counting numbers.
- **Rankings return only nickname, score and rank**, never IDs or tokens.
- **No cross-site requests** (CSRF). The API accepts only `application/json` and checks the `Origin` header on POST. Together with `SameSite=Lax` and no CORS, other websites can't make a visitor's browser send requests with the player's cookie.

### S3 (High): getting the full daily answer list before playing

**Plan status:** missed words are shown when a game ends, and creating a new anonymous player costs nothing.

**Attack:** this takes under a minute.

1. Open a private browser window, which counts as a new player.
2. Start the daily game and press **End game**. The full answer list appears.
3. Play the daily game as your real player and submit every word.

The result is the best possible daily score and first place on the daily board.

**Fix:**

- **Daily mode:** missed words are shown only after the day ends (midnight UTC). Free play keeps the instant list.
- **Daily games close at midnight UTC.** Submits to a daily game after its day are rejected, so a game left open can't be finished once the next day's list is out.
- **The live counter stays.** "4 of 23 found" gives away no words.

**Decided:** missed words are shown after the day ends. This replaces the earlier "missed words at game end" decision for daily mode.

### S4 (High): bots solving every grid perfectly

**Plan status:** not addressed.

**Attack:** the dictionary, the source list and the solver are all public in the repo. A script reads word1 and word2 from `POST /api/games`, runs the solver and submits every word, scoring the maximum on every game.

**Limits:** this can't be fully stopped without real accounts and human checks. Every online word game has the same problem.

**Fix:** these raise the bar and catch obvious cases.

- **Minimum gap between submits** in one game, e.g. 1 second, enforced by the server.
- **A plausibility check when a game ends.** Examples of what trips it:
  - 90% or more of all words found
  - more than X valid words per minute
  - every word found with no mistakes

  A game that trips it is marked `flagged` and stays off the boards until you review it.

  - **Avoiding false alarms:** every complete game finds 100%, so the word-count rules apply only together with the timing rule, and only to grids above a minimum size.
  - **Tuning:** X and the minimum size are set after the feasibility spike.
  - **Review:** you clear a flag by hand with `python -m scripts.admin unflag`, which also rebuilds the affected rankings.
- **A `users.hidden` flag** removes a player from every board.
  - You set it with `python -m scripts.admin hide` inside `fly ssh console`.
  - The Fly image has no `sqlite3` program; use `python -m sqlite3` for anything else.
  - There is no admin endpoint on the internet.
- **Server-side timing only.** Tile-click events come from the browser and can be faked, so they can't prove a human played.

### S5 (High): double scoring and playing after the game has ended

**Plan status:** the rules are written down, but nothing yet says how they hold when requests arrive at the same moment.

**Attacks:**

- Send the same valid submit 20 times in parallel. If the "already found?" check and the save aren't one atomic step, the word scores 20 times.
- Keep submitting after the 3rd wrong word or after **End game**.
- Send several wrong submits at once to slip past the 3-try limit.

**Fix:**

- **One transaction per submit** (`BEGIN IMMEDIATE`). Inside it the server:
  1. loads the game
  2. checks the game isn't over, fewer than 3 tries are used, and (daily) the day isn't over
  3. checks the word
  4. saves the submission and updates the score
  5. commits
- **A `UNIQUE(game_id, word)` constraint** on valid submissions stops duplicates even if the code has a bug.
- **The browser never sends score, points or the won flag**, and no endpoint accepts them. Rankings are written only from the server's own data when a game ends.
- **The server sets the daily date** (today, UTC) when the game is created. The client can't choose it.

### S6 (Medium): rerolling free play until a grid is easy

**Plan status:** free games are unlimited, and `total_words` is shown as soon as a game starts.

**Attack:** start free games until one has many words, and drop the rest. The free-play board, which ranks by best single game, then rewards whoever rerolls most, or scripts the rerolling.

**Decided: count every game, with a limit.**

- **At most 20 free games per player per day.**
- **Only one free game can be open at a time.** Starting a new one ends the open one with its current score.
- **Ranking:** best single game stays, but a player can try at most 20 grids a day.

**Not chosen:** ranking by the percentage of the grid's possible points.

### S7 (Medium): filling the disk or running up costs

**Plan status:** `/api/events` accepts any batch, and there are no limits.

**Attack:** a script posts huge event batches or creates millions of players and games. The 1 GB volume fills up, SQLite can't write, and the game goes down. Constant traffic also keeps the Fly machine running, which costs money.

**Fix:**

- **Size limits:**
  - a 16 KB request-body cap on every endpoint
  - events: at most 50 per batch, only known event types, and a payload schema
- **Rate limits** per IP (Fly passes the visitor's IP in `Fly-Client-IP`) and per player. Examples:
  - new players: 5 per hour per IP
  - new games: 30 per hour
  - submits: 1 per second per game, the same as the minimum gap in S4
  - events: 1 request per second

  With one machine, keeping the counters in memory is enough.
- **Retention:** delete events older than 90 days.
- **Fly settings:** set concurrency limits in `fly.toml` (`[http_service.concurrency]`) and watch volume usage in Fly's metrics.

### S8 (Medium): nicknames that inject scripts or copy another player

**Attacks:**

- **Script injection:** a nickname like `<img src=x onerror=…>` runs in every visitor's browser when the leaderboard shows it. The script can't read the HttpOnly token, but it can still submit words as the visitor.
- **Lookalikes:** `FIoMo` (capital I) passes for `FloMo`, and Cyrillic letters can copy Latin ones.
- **Offensive names.**

**Fix:**

- **Safe rendering:** the frontend shows all server text with `textContent`, never `innerHTML`.
- **Server-side nickname rules:**
  - 3–16 ASCII characters: `A–Z`, `a–z`, `0–9`, space, `_` and `-`
  - unique after folding lookalikes: lowercase, then I/l/1 → `l` and O/0 → `o`, so `FIoMo` and `FloMo` collide
  - checked against a list of offensive words
- **A second line of defence:** a `Content-Security-Policy: default-src 'self'` header, which blocks inline scripts.

### S9 (Medium): the database file being published or downloaded

**Plan status:** locally the database lives in `./data`, the same folder as the committed `data/daily_challenges.json`. The backend also serves static files.

**Risks:**

- A `git add .` commits `trur.db`, with every player and token hash, to the public repo.
- A static-file route set up at the repo root would let anyone download the database.

**Fix:**

- **Keep the database out of `data/`.** Locally, `DATA_DIR` defaults to `./var`, and `.gitignore` lists `var/` and `*.db`.
- **Serve static files only from the `web/` folder.**

### S10 (Medium): tampering with the daily words

**Plan status:** the backend imports JSON from GitHub, and the workflow can write to the repo.

**Risks:** anyone who can push to the repo can set the daily words or break the JSON. So can a compromised third-party Action used in the workflow. A broken import could also crash daily mode.

**Fix:**

- **The backend treats the JSON as untrusted.** An entry is used only if:
  - the date is valid
  - both words are in the source list
  - the pair passes the solver's 10+-letter check

  Anything else is skipped and logged.
- **A safe fetch:** the URL is fixed in config (nothing a visitor sends can change it), uses HTTPS, and has a timeout and a size cap.
- **Workflow hardening:**
  - pin each Action to a commit SHA
  - grant `permissions: contents: write` only to the job that needs it
  - set the repo's default workflow permissions to read-only
  - no `pull_request_target` triggers

### S11 (Medium): your own accounts as the real way to delete everything

**Risk:** whoever controls your GitHub or Fly account can change the code, read or delete the database, or delete the volume. That is far easier than any SQL injection.

**Fix:**

- **2FA** on both GitHub and Fly.
- **Deploy tokens:** while deploys are manual, store no Fly API token anywhere. If CI deploys later, use a deploy-only token (`fly tokens create deploy`) stored as a GitHub secret.
- **No admin or debug endpoints** on the public API. Maintenance goes through `fly ssh console`.
- **Backups:** raise snapshot retention to about 14 days, so tampering that's noticed late can still be rolled back. Test one restore before launch.

### S12 (Low): standard web hardening

- **HTTPS only.** `force_https` is already planned; add an HSTS header.
- **Security headers:** CSP with `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **Errors:** production runs without debug mode. Errors return a generic message, and the details go to the log.
- **Answers stay on the server until they're earned.** `POST /api/games` returns `total_words` but never the answer list.
- **Dependencies:** pin Python dependencies in a lockfile and run `pip-audit` before each deploy.

### S13 (Low): personal data

Nicknames, player tokens, IP addresses (used for rate limiting) and the gameplay log count as personal data wherever GDPR applies.

- **IP addresses:** don't store them. Keep them in memory for rate limiting only.
- **Retention and deletion:** events are kept 90 days (S7), and there's a way to delete a player together with all their rows.
- **A short privacy note** on the page.
- **No consent banner for now.** At first the game is played with friends only. The player cookie is needed for the game to work, so it most likely needs no banner anyway. Check again before opening the game to the public.

### Risks accepted for now

- **Clearing cookies creates a new player,** which means extra daily games. After S3 the game no longer shows the answers early, but S3 only stops casual cheating. The word lists, the solver and tomorrow's pair are all public, so a determined player can compute the answers. The S4 checks are the real defence.
- **Bots** can't be stopped completely (S4).
- **Daily words** are visible in the repo about 3 hours before they go live.
- **A disk failure** can lose up to a day of data.

### Changes made to the rest of the plan

Applied on 2026-10-11:

- **Users:** a player token in an HttpOnly cookie replaces the ID in browser storage (S2).
- **API:**
  - no `user_id` in request bodies, and game IDs are random (S2)
  - no score fields from the client (S5)
  - a new `/api/daily/{date}/answers` endpoint for past days (S3)
- **Daily challenge:** missed words are shown after the day ends, and daily games close at midnight UTC (S3).
- **Free play:** 20 games per player per day, one open at a time (S6).
- **Local database location:** `./var` instead of `./data` (S9).
- **New columns and constraints:**
  - `users.token_hash` (S2)
  - `users.hidden` and `games.flagged` (S4)
  - a unique index on valid submissions (S5)
- **Picking words, Hosting and Build order:** the import checks, workflow hardening, backups, 2FA and per-step security tasks.

## Build order

Each step ends with something you can run and check. The test cases for each step are listed in [test-strategy.md §3](test-strategy.md#3-test-cases-per-build-step) (multiplayer: [§6](test-strategy.md#6-multiplayer-after-go-live)), and a step is only done when they pass.

1. **Feasibility spike (do first).** Build the word lists and the solver, then measure over the source pairs: how many solutions per pair, and how many pairs have a word of 10+ letters.
   - *Why first:* the alternating-letter rule is strict. If most pairs give only 0–3 words, the rules need adjusting before any UI work.
   - *Scripts:* `scripts/build_wordlists.py` and `scripts/spike_stats.py`.
   - *Also set up:* the project skeleton from [architecture.md](architecture.md), and the `tests.yml` CI workflow, so tests run on every push from the start.
   - *Done when:*
     - there is a short stats report
     - the solver tests from [test-strategy.md](test-strategy.md) step 1 pass in CI
     - the thresholds that depend on the spike are set (N, plausibility)
2. **Layout in Claude Design, then the static frontend.**
   - First, design the screens and the logo in Claude Design (see [Designed in Claude Design](#designed-in-claude-design)). This can start alongside step 1.
   - Then build the grid, clicking, undo (click again / Clear), solution area, submit with a mocked check, reject animation and logo, matching the design.
   - *Done when:*
     - the design is approved
     - the browser version matches it at 640×640 and plays
3. **Backend + SQLite.** New game with a random free-play pair, submit, the 3-try rule, all three ways a game ends, the live counter and the missed-words reveal, with the frontend wired to it.
   - *Security:*
     - placeholders, Pydantic input checks, the SQLite authorizer and a read-only ranking connection (S1)
     - the submit transaction and the unique index (S5)
     - UUID4 game IDs (S2)
     - the database in `./var`, with static files from `web/` only (S9)
     - the body-size cap (S7)
     - security headers and generic error messages (S12)
   - *Done when:*
     - a full game runs end to end, with tests for the solver and the submit rules
     - the injection test leaves the schema unchanged
     - 20 parallel submits of the same word score once
4. **Users + logging.** Nickname prompt, player cookie, submissions and events written to the database.
   - *Security:*
     - the HttpOnly token cookie, ownership checks and Origin/JSON-only checks (S2)
     - nickname rules and `textContent` rendering (S8)
     - rate limits (including the 1-second submit gap from S4), event limits and 90-day event retention run at startup (S7)
   - *Done when:*
     - another player's game ID returns 404
     - a nickname containing `<script>` is rejected
5. **Daily challenge.** The GitHub Actions workflow and `pick_daily.py`, the backend import and fallback, keeping today's daily words out of free play, the one-game-per-day rule, the won flag and the daily mode in the UI.
   - *Security:*
     - missed words after the day ends, and daily games closing at midnight UTC (S3)
     - import checks and workflow hardening (S10)
   - *Done when:*
     - a manual workflow run commits tomorrow's pair
     - a second run changes nothing
     - after midnight UTC the backend serves that pair
     - today's answers can't be fetched by any route
6. **Rankings.** Monthly boards per mode and a board per daily challenge, all ranked by best single game.
   - *Security:*
     - the free-play limit (S6)
     - the plausibility flag and the `hidden` flag (S4). The submit gap was already built in step 4.
     - closing abandoned games, and rank computed when a board is requested
   - *Done when:* flagged games and hidden players are missing from every board.
7. **Go live on Fly.io** (see [Hosting](#hosting-flyio)). This means `fly.toml`, the `/healthz` endpoint, the volume and the first deploy.
   - *Security:*
     - 2FA on GitHub and Fly, 14-day snapshot retention and a test restore (S11)
     - concurrency limits (S7)
     - `force_https`, HSTS and `pip-audit` (S12)
     - a privacy note, and a delete-player script run through `fly ssh console` (S13)
   - *Done when:*
     - a full game and a daily game work at `https://trur.fly.dev`
     - scores are still there after `fly machine restart`
     - a snapshot has been restored once
8. **Multiplayer** (see [multiplayer.md](multiplayer.md)). It runs as its own steps M1–M8, from the rules engine to release; power-ups come last.
   - *Needs:* steps 1–5 (solver, grid UI, security stack, players, and today's daily pair to keep off match boards), and the live game from step 7.
   - *Done when:* the steps in [multiplayer.md §10](multiplayer.md#10-implementation-plan) are done.
9. **Later:**
   - time limit
   - Litestream or Postgres, if a day of possible data loss becomes too much
   - before opening the game to the public, beyond friends: check again whether a consent banner is needed (S13)

## Assumptions (correct me if wrong)

1. The **minimum word length is 4 letters** (2 tiles), so single-tile words like "AS" don't count.
2. Re-submitting an already-found word gives no points and **doesn't cost a try**. The response says `duplicate`, and no submission row is stored.
3. "640×640 canvas" means a fixed-size game area built from HTML elements, not an HTML `<canvas>` drawing. HTML elements are easier for clicks and accessibility.
4. Four extra tables (users, games, daily_challenges, meta) are added beyond the four in the requirements.
5. Backend framework: FastAPI, chosen as a conventional default. Flask would also work.
6. Free-play pairs are otherwise fully random. The same player can get the same pair twice; the only rule is that today's daily words are excluded.

## Open questions

None for the solo game. Multiplayer has its own list in [multiplayer.md §13](multiplayer.md#13-open-questions).
