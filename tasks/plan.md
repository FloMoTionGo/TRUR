# Implementation Plan: TRUR

## Overview

This plan turns the design docs into an ordered list of tasks: [docs/plan.md](../docs/plan.md) (what the game does), [docs/architecture.md](../docs/architecture.md) (how it's built), [docs/test-strategy.md](../docs/test-strategy.md) (how it's tested) and [docs/multiplayer.md](../docs/multiplayer.md) (after go-live). It follows the docs' build order, steps 1–7 for the solo game and M1–M8 for multiplayer. Each step is split into tasks of size S or M that each leave the system working. The docs remain the specification; each task names the test-strategy cases that must pass. Task details are in [todo.md](todo.md).

## Architecture decisions (taken from the docs, not re-decided here)

- FastAPI on one uvicorn worker with SQLite (STRICT tables, WAL). Hand-written SQL with `?` placeholders, an authorizer on request connections, and a read-only connection for boards.
- `trur/solver.py`, `match.py` and `lobby.py` use the standard library only, so `scripts/pick_daily.py` installs nothing in Actions.
- Time comes only from the injected `Clock`, and randomness only from an injected `random.Random`. AST guards enforce both.
- The server is authoritative: the client sends tile coordinates and never a score.
- The frontend is vanilla JS in `web/`, the only static root. No inline script or style (CSP), and text is set with `textContent` only.

## Decision this plan adds (confirmed by the owner, 2026-10-11)

**The minimal player cookie moves from step 4 into step 3.** Every game endpoint identifies the player by cookie. If step 3 built the game API without players, it would need a stub `get_player` that step 4 then throws away, and all step-3 tests would need rewriting. Step 3 therefore gets `create_user`, `user_for_token`, `POST /api/users`, `GET /api/users/me` and the JSON/Origin check (T11). Step 4 keeps nickname rules, rate limits, the event log and the S2 test suite.

## Phases

| Phase | Doc step | Tasks | Ends with |
| --- | --- | --- | --- |
| 1 Foundation and spike | 1 | T1–T5 | Spike report, thresholds set, **go/no-go on the rules** |
| 2 Design and static frontend | 2 | T6–T8 | Approved Claude Design; mocked game playable at 640×640 |
| 3 Free play end to end | 3 | T9–T15 | A full free game in the browser against the real backend |
| 4 Players and logging | 4 | T16–T19 | Nicknames, rate limits, gameplay log, S2 suite |
| 5 Daily challenge | 5 | T20–T24 | Workflow commits pairs; daily mode playable |
| 6 Rankings | 6 | T25–T28 | Boards with flags and hidden players respected |
| 7 Go live | 7 | T29–T30 | `https://trur.fly.dev` live, restore drilled |
| 8 Multiplayer | 8 (M1–M8) | M1–M8 | See multiplayer.md §10; M1–M6 are built before go-live (owner, 2026-10-12) |

**Parallel work:** T6 (design) can run alongside phase 1. T7–T8 need only the approved design. T23 (`pick_daily` + workflow) depends only on T4, so it can start any time after phase 1. Everything in phases 3–6 is sequential, because it shares `games.py`, `api.py` and the schema.

## Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| The alternating-letter rule gives too few words per pair | High: the game concept fails | T5 spike runs first; checkpoint A is an explicit go/no-go before any UI or backend work |
| SCOWL `en_CA` licence, or `wordfreq` frequency quality, doesn't fit | Med | T2 checks the licence first and records provenance in `data/README.md`; fallback: another permissively licensed en_CA list |
| Palette contrast: black on olive (about 3.6:1) and white on orange (about 2.9:1) fail WCAG | Med: expensive after the design is approved | **Resolved:** a fixed text colour for each background, recorded in docs/plan.md before Claude Design starts |
| Midnight-UTC and race logic is subtle | High: ranking integrity | FakeClock, CI with `TZ=Pacific/Kiritimati`, the live-server race tests in T13 and T21 |
| GitHub disables scheduled workflows after 60 days without activity | Med: falls back silently | The fallback covers it; `picked_by='fallback'` rows are the signal; check after the first 60 days |
| Lexicon RAM on a 256 MB Fly VM | Low | Measure in T5 and record it; raise the VM size in T29 if needed |

## Open questions

1. **Spike thresholds:** `MIN_FREE_WORDS`, `PLAUS_*` and `MIN_MATCH_WORDS` are placeholders until T5 and M2.
