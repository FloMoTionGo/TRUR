# TRUR

A game unlike Wordle, but like Wordle.

```text
        T     U
  R   *TR*  [UR]
  R   [TR]  *UR*
```

The logo is the smallest TRUR grid: TU across the top, RR down the side. The marked tiles read TR + UR = **TRUR**.

> **Status: live at https://trur.fly.dev (since 2026-10-15).** Free play, the daily challenge, the rankings and multiplayer (press **Multi**) are deployed. The multiplayer screens are built ahead of their design pass, and phone layouts are in progress. See [tasks/todo.md](tasks/todo.md).

## How to play

Two source words make a grid of letter pairs. The first word runs across the top and the second down the side. Each cell is the column's letter followed by the row's letter:

```text
        G    A    R    D    E    N
  S   [GS] [AS] [RS] [DS] [ES] [NS]
  U   [GU] [AU] [RU] [DU] [EU] [NU]
  M   [GM] [AM] [RM] [DM] [EM] [NM]
  M   [GM] [AM] [RM] [DM] [EM] [NM]
  E   [GE] [AE] [RE] [DE] [EE] [NE]
  R   [GR] [AR] [RR] [DR] [ER] [NR]
```

Click tiles to build a word, then press **Submit**: GE + AR = GEAR, NE + AR + ER = NEARER, RE + RU + NS = RERUNS.

- A word needs at least 2 tiles (4 letters) and must be in the Canadian English dictionary. Plurals and other inflected forms count.
- Each tile can be used once per word. The letter order is fixed: GS exists, SG doesn't.
- A word scores **tiles²**: 4 letters = 4 points, 6 = 9, 8 = 16, 10 = 25.
- The game ends after the **3rd rejected word**, when every word has been found, or when you press **End game**.

The full rules are in [plan.md: Game rules](docs/plan.md#game-rules).

## Modes

| Mode | What it is |
| --- | --- |
| **Free play** | A random word pair each game, up to 20 games a day. Missed words are listed when the game ends |
| **Daily challenge** | Everyone gets the same pair, one game per day. Find a word of 10 or more letters to win. The answers appear after midnight UTC |
| **Multiplayer** | 2–6 players race on one board for 60 seconds. Each used tile drops to half its value for everyone, and scores stay hidden until the end. See [docs/multiplayer.md](docs/multiplayer.md) |

- **Rankings:** a monthly board for each solo mode and a board for each daily challenge, all ranked by best single game.
- **Players** are anonymous. You pick a nickname, and the browser keeps a secret token in a cookie. There's no login.

## How it's built

```text
                                    ┌──────── Fly.io app "trur" (fra) ────────┐
Browser (HTML/CSS/vanilla JS)  ──>  │  Python backend (FastAPI)  ──>  SQLite  │
                                    │                 on /data volume (1 GB)  │
                                    └────────────▲────────────────────────────┘
                                                 │ fetches new daily pairs
GitHub Actions (daily)  ──commits──>  data/daily_challenges.json (in the repo)
```

- **Frontend:** plain HTML, CSS and JavaScript in `web/`, with a fixed 640×640 game area, desktop only. The layout and logo are designed in Claude Design first.
- **Backend:** Python 3.12, FastAPI on uvicorn with one worker, and SQLite. The server is authoritative: the browser sends tile coordinates, and the server rebuilds the word, checks it and keeps the score.
- **Word data:** a filtered Canadian English dictionary, plus the 500 most common dictionary words of 5–10 letters as source words. Both are committed in `data/`.
- **Daily pairs:** a GitHub Actions workflow picks the next day's pair at 21:17 UTC and commits it to `data/daily_challenges.json`. The backend imports it, and picks a pair itself if the workflow misses a day.
- **Hosting:** the Fly.io app `trur` in Frankfurt, at `https://trur.fly.dev` once live. It stops when idle.
- **Security:** the plan includes an audit (S1–S13) covering SQL injection, ranking manipulation and damage to the database. Every fix is part of the build steps.

## Documentation

| File | What's in it |
| --- | --- |
| [docs/plan.md](docs/plan.md) | What the game does: rules, decisions, layout brief, hosting, the security audit and the build order. If the docs disagree, the plan wins |
| [docs/architecture.md](docs/architecture.md) | How it's built: repo layout, modules, API contract and error codes, SQLite schema, configuration, dependencies and local dev commands |
| [docs/test-strategy.md](docs/test-strategy.md) | Test levels, fixtures, test cases for each build step and security finding, end-to-end flows and CI |
| [docs/multiplayer.md](docs/multiplayer.md) | The multiplayer match: rules, scoring, the quick-match queue, the live-update design, schema, security and its own build plan |

## Roadmap

From [plan.md: Build order](docs/plan.md#build-order). Each step ends with something you can run, and is done only when its tests pass.

1. **Feasibility spike:** word lists, the solver and a stats report. It sets the thresholds the later steps depend on.
2. **Layout in Claude Design**, then the static frontend.
3. **Backend + SQLite:** a full free-play game, end to end.
4. **Players + logging:** nickname, player cookie and the gameplay log.
5. **Daily challenge:** the workflow, the import and the fallback.
6. **Rankings:** monthly and daily boards.
7. **Go live on Fly.io.**

Multiplayer follows, in steps M1–M8 of [docs/multiplayer.md](docs/multiplayer.md#10-implementation-plan).

## Development

> These commands come from [docs/architecture.md §7](docs/architecture.md#7-local-dev-workflow-powershell-from-the-repo-root). They work once step 1 has set up the project.

Requires Python 3.12. From the repo root, in PowerShell:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-dev.txt
uvicorn trur.app:create_app --factory --reload --port 8000   # http://localhost:8000
pytest -q -m "not e2e and not smoke"                         # fast suite; -m e2e for the browser flows
```

- **Scripts** run as `python -m scripts.<name>`: `build_wordlists` (word lists), `spike_stats` (feasibility report), `pick_daily --dry-run` (shows the next daily pair), `match_sandbox` (a multiplayer match on http://127.0.0.1:8765 with hot-seat players and no clock) and `admin` (hide players, clear flags, delete a player).
- **The local database** is `var/trur.db`. Git ignores `var/`. Never put the database in `data/`, which is committed and public.
- **Deploying** is manual: run `pip-audit -r requirements.txt`, then `fly deploy`. Afterwards, run the smoke tests with `pytest tests/smoke --base-url https://trur.fly.dev`.

## Design

The approved design is the **[TRUR screen mocks](https://claude.ai/artifact/4cJYXZSdvg5hD8mLkxaQSU)** canvas in Claude Design. It's private: ask the owner for access.

- **Direction:** "Olive board": olive tiles with white letters, orange for selected tiles, in light and dark with a toggle.
- **Screens:** free play, the daily challenge, game over, leaderboards, the nickname prompt, past answers, and the logo with the tile states.
- **Built from it:** `web/` copies its colours, sizes and fonts. The full list of decisions is in [docs/plan.md: Layout & style](docs/plan.md#layout--style).
