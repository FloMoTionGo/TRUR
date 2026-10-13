# TRUR – Multiplayer

Rules, design and build plan for the **multiplayer match**, from the `multiplayer games` section of `requirements` and the owner's answers on 2026-10-11. The **display word and creativity vote** ([§3.6](#36-display-word), [§3.7](#37-creativity-vote)) come from the `multiplayer mode` notes in `requirements`, added on 2026-10-12.

- [plan.md](plan.md) still owns the solo rules (free play, daily challenge). Nothing here changes them.
- Module, setting and endpoint names follow [architecture.md](architecture.md). Test conventions follow [test-strategy.md](test-strategy.md).
- If this file and the plan disagree about something both cover, the plan wins and this file gets fixed.
- **Reviewed on 2026-10-11** by an architecture review and a test review. Their findings and the owner's decisions on them are applied here. The resulting changes to the other docs are listed in [§11](#11-changes-made-to-the-other-docs).

## 1. Goal

2–6 players play **the same board at the same time for 60 seconds**. Every tile starts at 100 %. Each time anyone uses a tile in an accepted word, that tile drops to half its value for everyone. Scores stay hidden until the end, so players race for fresh tiles while only seeing which tiles others used and how long their words were.

Each player can also set one **display word**: any string of tiles, not checked against the dictionary. After play, everyone votes on the other players' display words for creativity, and the votes add a bonus to the final score.

## 2. Decisions

| Topic | Decision |
| --- | --- |
| Board | **One shared grid per match**, built exactly like solo: word 1 across, word 2 down, every cell its own tile |
| Multiplier | Shown and calculated as a **whole percentage**. Every tile starts at **100 %**. Each accepted word, by any player, **halves** the value of every tile it used: 100 % → 50 % → 25 % → 13 % → 6 % …, never below 1 %. Word length doesn't change the cut (owner chose this over a length-dependent cut) |
| Word points | `tiles × (sum of the tiles' percentages) ÷ 100` (= tiles² × average percentage), taken **before** the halving, rounded half up, **at least 1**. With fresh tiles this is the solo score |
| Tile lock | A tile used in one of **your** accepted words is **locked for you** for the rest of the match. Other players can still use it. Same-text tiles (two GM) are separate, so the second one stays free |
| Same word, other players | **Several players can score the same word.** Later finders get less, because the first finder already halved those tiles |
| Same word, same player | **"Already found", no points**, as in solo. That holds even when the word is rebuilt from other copies of the same letter pairs. Nothing locks and nothing is halved |
| Wrong word | **Nothing happens except the red reject note.** No tries, no lockout, no penalty |
| Length | **60 s of play** after a **3-2-1 countdown**, with a **10-second warning** on every screen. A match always runs the full 60 s |
| Players in a match | **Fixed when the match forms.** Leaving or losing the connection never removes anyone, and the match **always plays to the end**, even if only one player is still connected |
| Hint popup | When another player's word is accepted, everyone else sees **"FloMo found a 6-letter word"** |
| Multipliers | **Visible live on every tile**, as a percentage. That's the strategy, even though it shows which tiles others used |
| Scores | **Hidden in the game until the end**, including your own. You see your own words, without points. A modified browser could work them out from the live updates; that's accepted ([§7](#7-security) M2) |
| Joining | **Public quick-match queue.** A match starts when enough players are waiting ([§4](#4-quick-match-and-match-states)) |
| Server full | **Refused at once** with "Server busy, try again later". Nobody waits for a slot |
| Results | **Stay viewable** through a results link. A snapshot is stored with each match, and only the match's own players can open it |
| Rankings | **None for now.** No board means no plausibility check or flagging for matches |
| Display word | A **Display word** button next to Submit. It sends the current selection **without a dictionary check** and **no word points**, but otherwise acts like an accepted word: the tiles lock for you, halve for everyone, and others get the usual popup. It can be pressed again; **only the last one counts** ([§3.6](#36-display-word)) |
| Creativity vote | After play, a **vote of up to 60 s**, which **finishes at once when every connected player has voted** (owner, 2026-10-12). A player whose stream has been gone for `MATCH_STREAM_GONE_S` (10 s) counts as done. Each player ranks up to three **other** players' display words, worth **5, 3 and 1** points. Names and scores stay hidden while voting ([§3.7](#37-creativity-vote)) |
| Vote bonus | **Decided (owner, 2026-10-12):** the bonus is the **average** points a word got per ballot, **× 2**, so a unanimous first place gives **10** in any match size. Chosen over the literal rule (sum of all votes, up to 5 × (players − 1) = 25), which decides too many matches; see the assessment in [§3.7](#37-creativity-vote) |
| Power-ups | **Later phase.** Specified in [§5](#5-power-ups-later-phase), built after the core match is tuned |
| Players | Same anonymous player + nickname + cookie as solo. A nickname is needed before joining |
| Transport | **POST for actions, Server-Sent Events (SSE) for live updates.** No WebSockets ([§6.2](#62-why-sse--post-and-not-websockets)) |
| State | **In memory while the match runs**, written to SQLite **once, when it ends**. One uvicorn worker, as solo already requires |

## 3. Match rules

### 3.1 Timeline

```text
 queue ──► match found ──► 3 · 2 · 1 ──► GO ──── 50 s ──► 10 s warning ──► END ──► VOTE (≤ 60 s) ─► results
           (nicknames)     (grid hidden)  grid revealed,                           anonymous      scores, bonus
                                          60 s timer starts                        display words  and all words shown
```

1. **Queue.** The player presses **Multiplayer** and waits ([§4](#4-quick-match-and-match-states)).
2. **Match found.** Everyone sees the nicknames in the match. From now on the players are fixed.
3. **Countdown 3-2-1.** The grid is **not sent yet**, so nobody gets a head start.
4. **Play, 60 s.** The words and the grid arrive with "GO". The server's clock decides when the match ends.
5. **Warning at 10 s left.** A banner appears and the timer changes colour on every screen at the same moment.
6. **End.** Submits stop at 60 s.
   - A submit that reaches the server up to **300 ms** later still counts, to absorb network delay on a last-second click.
   - Play is closed only **after** those 300 ms. A late word is therefore always in the results.
7. **Vote, up to 60 s.** Every player sees the other players' display words, anonymous and shuffled, and ranks up to three ([§3.7](#37-creativity-vote)). The vote **finishes automatically as soon as every connected player has voted**. A player whose stream has been gone for 10 s counts as done, so nobody waits for someone who left. The 60 s are only the limit for connected players who don't vote. If fewer than two players set a display word, there's nothing to compare and the vote is skipped: no bonus for anyone.
8. **Results.** Places, every player's words with points, the words nobody found, and the final board. The results screen has its own link (`#match/<id>`), which keeps working later.

**Exact boundaries**, all on the server clock. T is the moment the board has been picked.

- `starts_at` is T + 3 s.
- `ends_at` is `starts_at` + 60 s.
- A submit is accepted when `starts_at ≤ now ≤ ends_at + 300 ms`.
- The warning goes out at the first tick with `now ≥ ends_at − 10 s`. Play closes at the first tick with `now > ends_at + 300 ms`; that tick's time is `closed_at`.
- `vote_ends_at` is `closed_at` + 60 s. A ballot is accepted when `closed_at < now ≤ vote_ends_at + 300 ms`. The match ends at the first tick after that, or earlier when every player is **done**: has voted, or has had no open stream for `MATCH_STREAM_GONE_S` (`Hub.gone_since`). A ballot that makes everyone done ends the vote at once; a player who goes away is noticed by the next tick after their 10 s.
- A player who left and reconnects before the vote ends can still vote. One who comes back after it ended sees the results.
- Times sent to the browser are clamped to 0 or more.

### 3.2 A turn

The same steps as solo ([plan.md: A turn](plan.md#a-turn)), minus tries:

1. Click tiles. They appear in the solution area in click order and stay highlighted. Click a selected tile again to remove it, or press **Clear**.
2. Tiles **locked for you** can't be clicked.
3. Press **Submit**.
   - **Accepted:** the word joins your list (no points shown), its tiles lock for you, and every player's board shows those tiles at half their percentage. Everyone else gets the popup.
   - **Already found:** you scored this word before. You get a short note and nothing else changes.
   - **Rejected:** the red reject animation plays. That's all.
4. **Or press Display word** ([§3.6](#36-display-word)). The selection becomes your display word, shown above your word list, with no check and no points. The tiles lock and halve as for an accepted word.
5. Either way, the selection clears.

### 3.3 Scoring

```text
points(word) = max(1, round_half_up( tiles × (pct(tile 1) + pct(tile 2) + …) ÷ 100 ))
pct(tile)    = max(1, round_half_up( 100 ÷ 2^uses ))
```

| `uses` | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **pct** | 100 % | 50 % | 25 % | 13 % | 6 % | 3 % | 2 % | 1 % |

- `uses` is how many accepted words, by anyone, have used that tile before this one.
- `pct` is worked out from `uses` each time, not by halving the rounded value again, so rounding errors don't build up.
- Everything is whole-number maths. Python's `round()` rounds halves to even (4.5 → 4, 2.5 → 2), so use `(tiles × Σpct + 50) // 100` for the points.
- In the core game each player can use a tile once, so before the last of 6 players a tile is at worst 3 %. The 1 % floor matters only once power-ups exist. The 1-point minimum can be reached with 5 players.
- **A match score** is the sum of a player's word points **plus the vote bonus** ([§3.7](#37-creativity-vote)). **Places** use `RANK()` (1, 1, 3), with ties ordered by `nickname_key`, as on the boards.

**Worked example.** GARDEN × SUMMER, three players, in this order:

| # | Player | Word (tiles) | Tile values before | Points | Uses after |
| --- | --- | --- | --- | --- | --- |
| 1 | A | GEAR (GE, AR) | 100 %, 100 % | 2 × 200 ÷ 100 = **4** | GE 1, AR 1 |
| 2 | B | NEARER (NE, AR, ER) | 100 %, 50 %, 100 % | 3 × 250 ÷ 100 = 7.5 → **8** | NE 1, AR 2, ER 1 |
| 3 | A | DEARER (DE, AR, ER) | – | **locked:** A already used AR | – |
| 4 | B | GEAR | – | **locked:** B already used AR | – |
| 5 | C | GEAR (GE, AR) | 50 %, 25 % | 2 × 75 ÷ 100 = 1.5 → **2** | GE 2, AR 3 |
| 6 | C | DEER (DE, ER) | 100 %, 50 % | 2 × 150 ÷ 100 = **3** | DE 1, ER 2 |

Result: B 8, C 5, A 4. Final board: GE 25 %, AR 13 %, NE 50 %, ER 25 %, DE 50 %, all other tiles 100 %. What this shows:

- **Your own words never share a tile**, so every tile you use is a choice. A long word spends more tiles but scores more.
- **Copying is allowed but cheap.** The popup plus the faded tiles let others guess your word, and they get it at a discount.

### 3.4 What each player sees

| Information | During the match | At the end and through the results link |
| --- | --- | --- |
| Grid and every tile's percentage | everyone, live | everyone |
| Your own locked tiles | you | – |
| Your own words | you, without points | with points |
| Other players' words | only "*nickname* found an *n*-letter word" | everyone's, with points |
| Any score, including yours | nobody, in the game | everyone |
| Words nobody found | nobody | everyone |
| Total number of words on the board | nobody | everyone |
| Your own display word | you | everyone, with its votes and bonus |
| Other players' display words | only the usual popup, so a display word looks like a found word | during the vote: the words, without names; at the end: with names |
| Who voted for whom | – | nobody, only each word's total and bonus |

There's no "x of y found" counter: it would only matter as a solo progress bar, and the race doesn't need it.

### 3.5 Picking the board

- Same picker as free play (`solver.pick_pair`) with its own `accept` rule:
  - neither word is one of **today's or tomorrow's daily words** (tomorrow's is public about 3 hours early), because results list every word and would leak the daily answers. Today's pair comes from `ensure_daily_pair`. Tomorrow's comes from the DB, and if it isn't there yet, `import_daily` runs first. If tomorrow hasn't been picked at all, there's nothing to exclude
  - at least **`MIN_MATCH_WORDS`** answers in that orientation. This is set by the spike in step M2, and is expected to be well above free play's N, because up to 6 players share the board and can't reuse their own tiles
- The pair is picked in a worker thread when the match forms, so the event loop never blocks.
- **If no pair qualifies** (`LookupError`), the match is cancelled and its players are told so ([§4](#4-quick-match-and-match-states)).

### 3.6 Display word

The **Display word** button sends the current selection as your display word.

- **No dictionary check and no word points.** Any string of at least `MIN_TILES` tiles counts, valid word or not. A real word sent this way scores nothing either; to score it, use Submit.
- **Everything else is as for an accepted word** ("triggers all other events"):
  - the tiles **lock for you** for the rest of the match
  - each tile's `uses` goes up by 1, so it **halves for everyone**
  - the others get the popup "*FloMo* found an *n*-letter word", the same as for a real word, so they can't tell it apart
  - the submit gap (`MATCH_SUBMIT_MIN_GAP_S`) and the 300-ms grace apply
- **Only the last one counts.** You can press it again with other tiles; the new word replaces the old one. The tiles of the old one **stay locked and halved**, so every change costs you tiles.
- **Already found, locked tiles:** a locked tile can't be selected, so a display word never reuses your own tiles. The display word may match a word you found; that has no effect.
- **Not setting one** is allowed. You still vote, but get no bonus.

**Why the tile cost matters.** Without it, the button would be a free way to spoil tiles for others. With it, a player who spams display words locks their own board and can't score, and the submit gap slows them down. That's accepted for friends-only play ([§7](#7-security) M10).

### 3.7 Creativity vote

**Ballot.** After play, each player gets the display words of the **other** players, anonymous and shuffled per voter.

- Rank up to three of them: 1st gets **5**, 2nd **3**, 3rd **1** points. With fewer candidates, use the top values only (two candidates: 5 and 3).
- Voting for your own word is impossible; it isn't on your ballot.
- One ballot per player, final once sent. A partial ballot (only a 1st place) is allowed. Not voting within 60 s is allowed too: that player's ballot is empty.
- Players who didn't set a display word still vote.

**Bonus (decided).** `bonus = round_half_up(2 × received ÷ ballots)`, where `received` is the sum of points the word got and `ballots` is the number of non-empty ballots from **other** players. In integers: `(4 × received + ballots) // (2 × ballots)`. No ballots, or no display word: 0. The range is 0–10 in every match size.

**Assessment of the literal rule** (bonus = sum of votes received). Simulated on 300 matches per player count with the solver, `MIN_MATCH_WORDS` 20, boards capped at 74 words, greedy players finding 5 words each in 60 s, everyone setting a display word and voting at random:

| Players | Median word score | 1st and 2nd within 4 points | Max bonus, sum | Winner changed by votes, sum | Max bonus, average × 2 | Winner changed, average × 2 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 36 | 59 % | 5 | – (both get 5) | 10 | – (both get 10) |
| 3 | 29 | 71 % | 10 | 15 % | 10 | 15 % |
| 4 | 26 | 70 % | 15 | 35 % | 10 | 26 % |
| 5 | 24 | 72 % | 20 | 45 % | 10 | 27 % |
| 6 | 21 | 72 % | 25 | 53 % | 10 | 25 % |

What this shows:

- **15 is too much, and the sum gets worse with more players.** Word scores shrink as players are added (more halving), while the sum's maximum grows by 5 per player. At 4 players a unanimous 15 is more than half the median score, and even random votes change the winner in a third of matches. At 6 players the vote decides more matches than the words.
- **Matches are close.** 1st and 2nd are within 4 points in about 70 % of matches, so any bonus matters. That's intended; it shouldn't dominate.
- **The averaged bonus is flat.** It caps at 10 (about one fresh 3-tile word) and changes the winner in about a quarter of matches at every size. Real votes cluster more than random ones, so expect somewhat more.
- **With 2 players the vote does nothing** under either rule: each gets the other's only vote. It still rewards setting a display word at all.
- **Tactical voting:** if names or scores were visible, a rational player would give 5 to whoever can't overtake them. So the ballot shows neither, and the results never show who voted for whom.

The owner chose the averaged bonus on 2026-10-12. Step M2 re-runs this with the real `MIN_MATCH_WORDS` and confirms the cap ([§10](#10-implementation-plan)).

## 4. Quick-match and match states

**Queue rules:**

- A player can be in **one queue or one match at a time**. Solo games are separate and unaffected.
- When the **2nd** player joins, a **10-second fill window** starts. Anyone who joins during it is in the same match.
- The match forms when the window ends, or immediately when the **6th** player joins. Anyone who joins after that starts a new queue.
- If leaves drop the queue below 2 players during the window, the window is cancelled. Each remaining player's 2-minute timeout still counts from their own join time.
- **Queue timeout:** after **2 minutes** without a match, the player leaves the queue with "No other players right now". The timeout isn't checked while a fill window runs, because a match forms within 10 s anyway. It also stops a forgotten tab from keeping the Fly machine awake.
- **A closed stream:** a queued player whose live connection has been closed for **10 s** is dropped from the queue.
  - Joining without opening a stream counts as a closed stream.
  - A closed stream may only be noticed at the next keepalive (every 5 s), so in practice this takes 10–15 s.
- **Server full:** when the queue already holds `MATCH_MAX_QUEUE` players, joining gets 503 `server_busy`. When a group is ready to form but `MATCH_MAX_ACTIVE` matches are already active (forming, countdown, running or voting), that group gets a `server_busy` event and goes back to idle. Nobody waits for a slot.

**States of one player:**

| From | When | To | What the player gets |
| --- | --- | --- | --- |
| idle | `POST /queue` | queued | MatchView; 503 `server_busy` if the queue is full |
| queued | `POST /queue/leave`, or stream closed for 10 s | idle | – (the stream closes) |
| queued | 2 minutes, no fill window running | idle | `queue_timeout` |
| queued | fill window ends, or 6th player | forming | – (the players are now fixed) |
| queued | ready to form, but `MATCH_MAX_ACTIVE` reached | idle | `server_busy` |
| forming | pair picked (time T) | countdown | `match_found`, `starts_in_ms` = 3000 from T |
| forming | no pair qualifies | idle | `match_cancelled` |
| countdown | `now ≥ starts_at` | running | `start` (words, grid) |
| running | `now ≥ ends_at − 10 s` | running | `warning` (once) |
| running | `now > ends_at + 300 ms`, at least two display words | voting | `voting` (that player's ballot) |
| running | `now > ends_at + 300 ms`, fewer than two display words | idle | `end` (Results), then the stream closes |
| voting | `now > vote_ends_at + 300 ms`, or every player is done (voted, or stream gone for 10 s) | idle | `end` (Results), then the stream closes |

- **Leaving** during forming, the countdown, the match or the vote changes nothing: the player stays in the match, their words still count, and they can reload and rejoin until the end. `POST /queue/leave` does nothing in these states. A player who never comes back simply doesn't vote.
- **After `end`** the player is idle immediately, so they can queue again at once. Their results stay available through the results link.
- **One tick can catch up several steps.** If the ticker was held up, one tick goes countdown → running → voting → ended and publishes `start`, `warning`, `voting` and `end` in that order. It never publishes the same event twice. `vote_ends_at` counts from `closed_at`, the time of that tick, so a late tick never skips the vote.
- **Hidden players** (`users.hidden`) can still play matches, because there are no match boards. Banning someone from matches is out of scope.

**Trade-off (accepted):** a public queue only starts a match when two people wait within the same 2 minutes. While the game is played by friends only, that means agreeing on a time. Private rooms with a code can be added later on top of the same lobby code ([§13](#13-open-questions)).

## 5. Power-ups (later phase)

These are specified now so the core design leaves room for them; they're built in step M8. Every interpretation below needs the owner's confirmation ([§13](#13-open-questions)).

| Power-up (requirements) | Interpretation | Effect | Who sees it |
| --- | --- | --- | --- |
| Double word score, 1× per user | Arm it, and your **next accepted word scores ×2**. If that word is rejected, it stays armed. A display word doesn't use it up, since it has no points | doubles one word's points | only you, until the end |
| Reduce letter pair multiplier to half | Pick **one tile** and halve its percentage for everyone, without using or locking it. That spoils a tile others want | `uses += 1` on that tile | everyone (the tile fades) |
| Reduce visibility of pairs that only appear in the lowest-scoring words | For you, **dim every tile that appears only in 2-tile words**, or in no word at all, for 15 s | personal hint | only you |
| Enhance pairs that appear in many words | For you, **shade tiles by how many answer words use them** (heat map), for 10 s | personal hint | only you |

**Design hooks already in the core:**

- Halving is just "`uses += 1`", so the second power-up needs no new scoring rule. It is also how a tile can get more than 6 uses and reach the 1 % floor.
- Power-ups 3 and 4 are computed by the server from the answer list **when activated**, and sent only to that player. The answer list never reaches the browser otherwise.
- Planned endpoint: `POST /api/match/{id}/powerup` with `{"type": …, "tile": [r,c]?}`. Each power-up can be used once per player per match.
- Results show who used which power-up.

## 6. Architecture

### 6.1 Overview

```text
Browser A ──POST /api/match/…──►┌──────────────── one uvicorn worker ──────────────────┐
Browser B ◄──SSE /api/match/stream┤ api.py (async match routes)                          │
   …                              │   └► lobby.py: queue · matches · hub · ticker        │
                                  │         └► match.py: rules engine (pure, stdlib)     │
                                  │   pick_pair / save_match ──to_thread──► SQLite       │
                                  └──────────────────────────────────────────────────────┘
```

- **The server is authoritative**, as in solo. The browser sends tile coordinates; the server rebuilds the word, checks the duplicate rule, locks and time, scores it and broadcasts the result.
- **All match state lives on the event loop.** Match routes are `async def`, and they change state without awaiting in between.
  - Submits are therefore handled one at a time, in the order the event loop runs them, with no locks. The first accepted word gets the tiles at full value.
  - The submit time is read with `clock.now()` **inside the handler**, not in a dependency. Dependencies run in the threadpool, so a time read there can be stale.
- **The `async def` exception.** architecture.md says "all routes are plain `def`". The match routes are the exception, because they never call `sqlite3` themselves:
  - they resolve the player with a short-lived connection in the threadpool, which is closed before the handler or stream starts (not a `yield` dependency, which could stay open for the whole stream)
  - other DB work goes through `asyncio.to_thread`
- **SQLite is touched only for the player lookup on each request** (a short read), **when a match forms** (pair pick) and **when it ends** (one transaction). Match state itself causes no disk I/O while a match runs.

### 6.2 Why SSE + POST and not WebSockets

| | SSE + POST | WebSockets |
| --- | --- | --- |
| New dependency | none; `StreamingResponse` + `EventSource` | plain `uvicorn` has no WebSocket support, so `websockets` or `wsproto` must be added |
| Security stack (Origin/JSON check, body cap, rate limits, Pydantic, error envelope) | reused unchanged for every action | must be rebuilt for messages, including an Origin check against cross-site hijacking |
| Reconnect | built into `EventSource` | hand-written |
| Latency | ample for a 60-s match with at most two submits per second | lower, but not needed |

**SSE details:**

- `GET /api/match/stream` sends `text/event-stream` with `Cache-Control: no-store` and `X-Accel-Buffering: no`. The CSP `default-src 'self'` already allows a same-origin `EventSource`.
- A `: ping` comment goes out every 5 s. It keeps proxies from closing the stream and shows within 5 s that a stream is gone.
- **Opening the stream:**
  - The route subscribes to the `Hub` and puts the `snapshot` first in the stream's queue, with no `await` in between, so no event can be lost or doubled.
  - When the player is idle, the route returns **204**, which tells `EventSource` not to reconnect.
- **Closing the stream:** the server closes it after `end`, `queue_timeout`, `server_busy`, `match_cancelled` and `replaced`. The browser also calls `close()` on each of these, so a forgotten tab never reconnects.
- **Middleware:** every middleware must be pure ASGI, as `BodySizeLimit` already is. `BaseHTTPMiddleware` passes streaming responses through an extra task and memory stream, which gets in the way of disconnect detection and cancellation. This applies to `SecurityHeaders`.

### 6.3 Modules

**New: `trur/match.py`.** The rules engine, stdlib only, with no I/O and no clock reads (times are passed in):

```python
COUNTDOWN_S = 3; DURATION_S = 60; WARNING_S = 10; VOTE_S = 60; GRACE = timedelta(milliseconds=300)   # decided rules
VOTE_POINTS = (5, 3, 1); BONUS_SCALE = 2                                                 # decided rules (§3.7)
class PlayerState:
    nickname: str; locked: set[Tile]; found: dict[str, FoundWord]   # FoundWord: word, tiles, points, at
    display: DisplayWord | None                                     # word, tiles, at; the last one only
    ballot: tuple[str, ...] | None                                  # candidate ids, best first; None = not voted
class Match:
    id: str; word1: str; word2: str; answers: frozenset[str]
    players: dict[str, PlayerState]          # user_id → state; fixed when the match forms
    uses: dict[Tile, int]                    # missing = 0
    starts_at: datetime; ends_at: datetime
    closed_at: datetime | None               # set once when play closes; submit() and display() check it first
    candidates: dict[str, str]               # candidate id → user_id, random ids, made when play closes
    log: list[MatchEvent]                    # every submit, display word and ballot, for the DB at the end
def pct(uses: int) -> int                               # max(1, round_half_up(100 / 2**uses)): 100, 50, 25, 13, 6, 3, 2, 1
def word_points(tile_uses: Sequence[int]) -> int        # max(1, (n × Σ pct(u) + 50) // 100)
def submit(m: Match, user_id: str, tiles: Sequence[Tile], now: datetime) -> SubmitOutcome
    # order: closed or outside [starts_at, ends_at + GRACE] (→ NotRunning) · word_from_tiles (→ ValueError)
    #        · already in found (→ Duplicate) · any tile locked (→ TileLocked)
    #        · word in answers? Accepted: points, lock tiles, uses += 1 : Rejected
def display(m: Match, user_id: str, tiles: Sequence[Tile], now: datetime) -> DisplayOutcome
    # the same order up to the lock check, with no duplicate or dictionary check;
    # then replace display, lock tiles, uses += 1
def close_play(m: Match, now: datetime, rng) -> bool    # sets closed_at, draws candidate ids; False = skip the vote
def ballot_for(m: Match, user_id: str, rng) -> list[Candidate]   # the others' display words, shuffled per voter
                                                        # (rng is passed in, like times, so tests are seeded)
def vote(m: Match, user_id: str, ranking: Sequence[str], now: datetime) -> None
    # outside (closed_at, vote_ends_at + GRACE] → NotVoting · already voted → AlreadyVoted
    # unknown id, own word, repeat, or more than len(VOTE_POINTS) → ValueError
def bonus(received: int, ballots: int) -> int           # (4 × received + ballots) // (2 × ballots); 0 if no ballots
def finish(m: Match, now: datetime) -> MatchRecord      # returns a frozen copy (tuples only) for saving
def results(rec: MatchRecord) -> ResultsData            # place (RANK), user_id, word score, display word, votes, bonus,
                                                        # score, words; missed; total; final pct
```

**New: `trur/lobby.py`.** The queue, running matches, live streams and ticker. It uses stdlib plus `asyncio`:

```python
class Hub:                                    # one asyncio.Queue per open stream, keyed by user_id
    def subscribe(self, user_id, now) -> asyncio.Queue    # an older stream of the same player gets `replaced`
    def unsubscribe(self, user_id, q, now) -> None        # only removes q if it is still the current one
    def gone_since(self, user_id) -> datetime | None      # when the last stream closed; None while connected
                                                          # (a player who never connected: the lobby uses the join time)
    def publish(self, user_ids, event: str, data: dict) -> None
class PickJob(NamedTuple): match_id: str
class SaveJob(NamedTuple): record: MatchRecord
class Lobby:
    def __init__(self, lex, settings, hub)
    def join(self, user_id, nickname, now) -> MatchView     # 409 in_match, 503 server_busy
    def leave(self, user_id, now) -> None                   # idempotent; no-op outside the queue
    def view(self, user_id, now) -> MatchView
    def submit(self, user_id, match_id, tiles, now) -> SubmitOutcome   # 404 if not a player of that match
    def display(self, user_id, match_id, tiles, now) -> DisplayOutcome # 404 if not a player of that match
    def vote(self, user_id, match_id, ranking, now) -> None # 404 likewise; ends the vote if everyone is now done
    def tick(self, now) -> list[PickJob | SaveJob]          # synchronous; changes state, publishes, returns jobs
    def pair_picked(self, match_id, pair, now) -> None      # forming → countdown
    def pick_failed(self, match_id, now) -> None            # forming → idle, `match_cancelled`
    def saved(self, match_id, ok: bool, now) -> None        # drop from memory; on failure keep results 10 min
    def results(self, match_id) -> ResultsData | None       # in memory until saved, else None (→ DB)
async def run(lobby, clock, rng, pick, save, stop: asyncio.Event, tick_s: float) -> None
    # every tick_s: jobs = lobby.tick(clock.now()) inside try/except (log, never die);
    # each job runs as asyncio.to_thread(pick|save) in a task kept in a set (strong reference),
    # and its result is fed back through pair_picked / pick_failed / saved.
    # On stop: wait up to 2 s for pending saves (2 s stream cut + 2 s < Fly's default kill_timeout of 5 s).
```

- **`tick()` is fully synchronous** and does all the waiting-for-a-thread work through the jobs it returns.
  - Lobby unit tests call `tick()`, `pair_picked()` and `saved()` directly with a `FakeClock`. That needs no event loop, no sleeps and no threads.
  - `asyncio.Queue.put_nowait` works without a running loop.
- **The results path:** results are served from memory until the save has finished, then from the DB snapshot. If the save fails, they stay in memory for 10 minutes and the error is logged.

**Changed:**

- **`db.py`:**
  - migration 2 ([§6.6](#66-database-migration-2))
  - `save_match(conn, record, now)`, one `tx()`
  - `match_results(conn, match_id, user_id)`, which reads the snapshot and returns 404 unless the caller was a player
- **`api.py`:** the match routes, the SSE route and Pydantic models.
- **`app.py`:** the lifespan creates the `Hub` and `Lobby` and starts `run()`, unless `MATCH_TICK_MS = 0`. On shutdown it sets `stop`.
- **`config.py`:** the settings in [§6.7](#67-configuration).
- **`scripts/admin.py`:** `delete-player` needs no change. The new tables cascade from `users`, and the snapshot holds user IDs, not nicknames ([§6.6](#66-database-migration-2)).

**Dependency rule:** `match.py` and `lobby.py` join the stdlib-only side. The existing AST guard is extended to cover them.

### 6.4 API contract

The conventions in [architecture.md §3](architecture.md#3-api-contract) apply: cookie identifies the player, JSON-only POSTs with an Origin check, 16 KB cap, error envelope, `extra="forbid"`. Match IDs are checked to be UUIDs before any lookup; anything else gives 404.

| Endpoint | Request | Success | Endpoint-specific errors |
| --- | --- | --- | --- |
| `POST /api/match/queue` | `{}` | 200 MatchView, `state:"queued"`. Already queued: the same view | 401; 409 `in_match`; 429 (`RL_QUEUE_JOINS_PER_PLAYER_HOUR`); 503 `server_busy` (queue full) |
| `POST /api/match/queue/leave` | `{}` | 204, also when not queued | 401 |
| `GET /api/match/current` | – | 200 MatchView; `state:"idle"` when not queued or playing | 401 |
| `GET /api/match/stream` | – | 200 `text/event-stream`, starting with `snapshot`; **204** when idle | 401 |
| `POST /api/match/{id}/submit` | `{"tiles":[[row,col],…]}`, the same shape and limits as solo | 200 `{"status":"accepted"\|"duplicate"\|"rejected","word","changes":[{"row","col","pct"}]}`. **No points**; `changes` is empty unless accepted | 401; 404 (unknown, not a player, or already saved and dropped from memory); 409 `match_not_running` (countdown, or over but not yet saved); 409 `tile_locked`; 422 `invalid_tiles`; 429 under `MATCH_SUBMIT_MIN_GAP_S` since your last submit |
| `POST /api/match/{id}/display` | `{"tiles":[[row,col],…]}`, as for submit | 200 `{"status":"displayed","word","changes":[{"row","col","pct"}]}` | the same as submit; the submit gap is shared between the two |
| `POST /api/match/{id}/vote` | `{"ranking":["c3","c1"]}`, candidate ids from the ballot, best first, 1–3 entries | 204 | 401; 404; 409 `match_not_voting`; 409 `already_voted`; 422 `invalid_ballot` (unknown or repeated id, your own word, too many) |
| `GET /api/match/{id}/results` | – | 200 Results, from memory until saved, then from the DB snapshot | 401; 404 (unknown, not a player, or not over yet, including during the vote) |

New error codes: 409 `in_match`, `match_not_running`, `tile_locked`, `match_not_voting`, `already_voted`; 422 `invalid_ballot`; 503 `server_busy`.

```jsonc
// MatchView: GET current, POST queue, and the "snapshot" stream event
{"state":"idle|queued|forming|countdown|running|voting",
 "queue":{"waiting":3,"fill_ends_in_ms":7000,"timeout_in_ms":95000} | null,
 "match":{"match_id":"uuid4","players":["FloMo","Ana"],
          "starts_in_ms":2100 | null, "ends_in_ms":41000 | null,  // clamped to ≥ 0
          "word1":"GARDEN" | null, "word2":"SUMMER" | null,      // null until running
          "pct":[[100,100,100,…],…] | null,                      // [row][col], null until running
          "locked":[[4,0],[5,1]], "found":["GEAR"],               // the caller's own, no points
          "display":"NEGE" | null,                               // the caller's own display word
          "vote":{"ends_in_ms":44000,                            // only while voting
                  "candidates":[{"id":"c3","word":"ARDE"},…],    // the others' display words, shuffled, no names
                  "voted":false} | null} | null}
// Results: the "end" event and GET results
{"match_id":"uuid4","word1":"GARDEN","word2":"SUMMER","total_words":9,
 "players":[{"place":1,"nickname":"FloMo","score":17,"word_score":8,
             "display":{"word":"NEGE","votes":13,"bonus":9} | null,   // votes = points received; 3 ballots: 2 × 13 ÷ 3 → 9
             "words":[{"word":"NEARER","points":8}]}],
 "missed_words":["DEARER","RERUNS"],
 "pct":[[…]]}                                                  // the final board, in %
```

- **Times are relative** (`…_in_ms`). The browser adds them to `performance.now()` when the message arrives, so the player's clock setting doesn't matter.
- **Results never contain user IDs.** A player deleted since the match is shown as "deleted player".
- **Results never contain ballots.** Only each display word's received points and bonus, so nobody learns who voted for whom.
- **Candidate ids** (`c1`…`c6`) are drawn at random per match when play closes, so they reveal neither the player order nor user IDs.

### 6.5 Stream events

| Event | Sent to | Data |
| --- | --- | --- |
| `snapshot` | the new stream, always first | MatchView |
| `queue` | everyone in the queue | `{waiting, fill_ends_in_ms}` |
| `queue_timeout` | that player; the stream then closes | `{}` |
| `server_busy` | a group that was ready but found no free match slot; the streams then close | `{}` |
| `match_found` | the match's players | `{match_id, players, starts_in_ms}` |
| `match_cancelled` | the match's players, when no board qualifies; the streams then close | `{}` |
| `start` | the match's players | `{word1, word2, ends_in_ms}`; every tile at 100 % |
| `tiles` | the match's players, the finder included | `{changes:[{row, col, pct}]}`, absolute values, so a repeat does no harm |
| `found` | the match's players **except the finder** | `{nickname, letters}`. Sent for display words too, identical in shape |
| `warning` | the match's players, once | `{ends_in_ms}` |
| `voting` | each player, with their own ballot | `{ends_in_ms, candidates:[{id, word}]}` |
| `voted` | the match's players | `{voted, of}` (counts only, e.g. 3 of 4), so waiting players see progress |
| `end` | the match's players; the streams then close | Results |
| `replaced` | an older stream of a player who opened a new one; it then closes | `{}`. The tab shows "Multiplayer is open in another tab" and does not reconnect |

- **Never sent while a match runs:** other players' words or display words, any score, `total_words`, or answers.
- **Never sent during the vote:** names next to display words, any score, or anyone's ballot.
- **Rejected and duplicate submits publish nothing.** Display words publish `tiles` and `found`, like accepted words.
- **Events reach each stream in order**, because only the event loop publishes.
- **A server restart** ends every stream without an event (see [§6.8](#68-hosting-impact)). During a deploy the reconnect may get 204, 502 or 503; any of these closes the `EventSource` for good (`readyState` CLOSED). So whenever the stream ends without a terminal event, `stream.js` calls `GET /api/match/current`: if the player is idle while the page believed it was in a match, it shows "Match cancelled".

### 6.6 Database (migration 2)

Multiplayer is built after go-live ([§10](#10-implementation-plan)), so this is **migration 2**.

Matches get **their own tables**. The existing `games` and `submissions` tables stay unchanged:

- Their invariants don't fit matches: `wrong_count ≤ 3`, `mode IN ('free','daily')`, one open free game, rankings recompute.
- Changing their CHECK constraints would need a table rebuild in SQLite.

`match_submissions` has the columns the requirements ask for (user id, timestamp, month). The game mode is implied by the table.

```sql
CREATE TABLE matches (
  id           TEXT PRIMARY KEY,                    -- uuid4
  word1        TEXT NOT NULL,
  word2        TEXT NOT NULL,
  total_words  INTEGER NOT NULL,
  started_at   TEXT NOT NULL,                       -- "GO", not the countdown
  ended_at     TEXT NOT NULL,
  month        TEXT NOT NULL,                       -- of started_at
  player_count INTEGER NOT NULL CHECK (player_count BETWEEN 2 AND 6),   -- fixed when the match forms
  results      TEXT NOT NULL                        -- JSON snapshot: places, user_ids, scores, words, display words,
                                                    -- votes received, bonuses, missed, final pct (no ballots)
) STRICT;
CREATE INDEX matches_started ON matches(started_at);
CREATE TABLE match_players (
  match_id     TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  score        INTEGER NOT NULL,                    -- word_score + vote_bonus
  word_score   INTEGER NOT NULL,
  vote_bonus   INTEGER NOT NULL DEFAULT 0,
  display_word TEXT,                                -- the last one; NULL if never set
  place        INTEGER NOT NULL,
  words_found  INTEGER NOT NULL,
  PRIMARY KEY (match_id, user_id)
) STRICT;
CREATE TABLE match_votes (                          -- for audit and tuning; never shown per voter
  match_id  TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  voter_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  points    INTEGER NOT NULL CHECK (points IN (5,3,1)),
  PRIMARY KEY (match_id, voter_id, target_id),
  CHECK (voter_id <> target_id)
) STRICT;
CREATE INDEX match_players_user ON match_players(user_id);
CREATE TABLE match_submissions (
  id           INTEGER PRIMARY KEY,
  match_id     TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  word         TEXT NOT NULL,
  tiles        TEXT NOT NULL,                       -- JSON [[row,col],…]
  kind         TEXT NOT NULL DEFAULT 'word' CHECK (kind IN ('word','display')),
  is_valid     INTEGER NOT NULL CHECK (is_valid IN (0,1)),   -- always 0 for 'display': not checked
  points       INTEGER NOT NULL DEFAULT 0,
  submitted_at TEXT NOT NULL,
  month        TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX match_submissions_valid_once ON match_submissions(match_id, user_id, word) WHERE is_valid = 1;
CREATE INDEX match_submissions_match ON match_submissions(match_id, submitted_at);
ALTER TABLE events ADD COLUMN match_id TEXT REFERENCES matches(id) ON DELETE CASCADE;
```

- **`save_match`** writes everything in one transaction when the match ends:
  - `matches` with its `results` snapshot, a `match_players` row per player, and every accepted or rejected submission. Duplicates store no row, as in solo, so the unique index never fires on a correct engine.
  - **every** display word a player set, not only the last, as `kind = 'display'` rows, since each one halved tiles; and one `match_votes` row per ranked candidate
  - the server-only events `match_started`, `match_word_accepted`, `match_word_rejected`, `match_word_duplicate`, `match_display_word`, `match_vote_cast` and `match_ended`, with their real timestamps
- **The save waits for the vote.** It runs when the match ends, after the vote, so up to 60 s add to the window in which a restart loses the match.
- **A player deleted during the match.** `scripts.admin` runs in another process and can't see in-memory matches. `save_match` therefore inserts player, submission, vote and event rows only for users that still exist (`INSERT … SELECT … WHERE EXISTS`; a vote needs both its voter and its target). The snapshot keeps their words and score but no nickname.
- **Reading the snapshot.** It stores user IDs, never nicknames. `match_results` looks the nicknames up in `users` on every read. A deleted player becomes "deleted player", so no personal data stays behind (S13). `place` and `player_count` keep their historical values.
- **A restart mid-match** loses that match. Nothing was written, so nothing is half-saved.

### 6.7 Configuration

Added to `Settings` ([architecture.md §5](architecture.md#5-configuration)). The constants in `match.py` (3-s countdown, 60-s match, 10-s warning, 60-s vote limit, 300-ms grace, 5/3/1 vote points, bonus scale 2) are rules, not settings. Tests drive time through `FakeClock`, so they don't need shorter values.

| Variable | Default | Test | Note |
| --- | --- | --- | --- |
| `MATCH_MIN_PLAYERS` / `MATCH_MAX_PLAYERS` | 2 / 6 | same | |
| `MATCH_FILL_S` | 10 | same | fill window after the 2nd player joins |
| `MATCH_QUEUE_TIMEOUT_S` | 120 | same | |
| `MATCH_STREAM_GONE_S` | 10 | same | a queued player without a stream is dropped after this; a voting player without one counts as done |
| `MATCH_SUBMIT_MIN_GAP_S` | 0.5 | 0, except in the gap test | lower than solo's 1 s, because it's a race |
| `MATCH_MAX_ACTIVE` / `MATCH_MAX_QUEUE` | 8 / 24 | small values | sized to Fly's connection limit ([§6.8](#68-hosting-impact)) |
| `MIN_MATCH_WORDS` | set after the spike (placeholder 20) | 1 | |
| `RL_QUEUE_JOINS_PER_PLAYER_HOUR` | 30 | 0 (off), except in the rate-limit test | |
| `MATCH_TICK_MS` | 200 | **0 = ticker off** in-process, where tests call `tick()` themselves; 10 on `live_match_server` | |
| `SSE_KEEPALIVE_S` | 5 | 0.2 | |

### 6.8 Hosting impact

- **One worker, one machine**, as already decided. Matches, queue and streams live in that process.
- **Fly concurrency.** Each open stream holds one slot of the `requests` limit for its whole life. The limits must leave room for normal requests:
  - the rule: `MATCH_MAX_ACTIVE × MATCH_MAX_PLAYERS + MATCH_MAX_QUEUE + 25 ≤ hard_limit`
  - with the defaults that's 8 × 6 + 24 + 25 = 97 ≤ 100
  - raise `hard_limit` before raising the caps
- **The vote** keeps each match's streams open up to 60 s longer, so a match can hold its slots for about 2 minutes (3 s + 60 s + 60 s). The concurrency rule doesn't change, but a full server stays full longer. In practice the vote finishes as soon as every connected player has voted, usually well before 60 s.
- **Auto-stop:** open streams keep the machine running while players queue, play or vote. The queue timeout and closing streams after `end` let it stop again.
- **Deploys and restarts:**
  - uvicorn runs the lifespan shutdown only after open connections have closed, so open streams would hold up a deploy until Fly kills the machine.
  - The Dockerfile CMD gets **`--timeout-graceful-shutdown 2`**: streams are cut after 2 s, and pending saves get up to 2 s more in the lifespan shutdown. Together that stays under Fly's default `kill_timeout` of 5 s, after which Fly kills the machine.
  - Running matches are lost, and the browsers show "Match cancelled" on reconnect ([§6.5](#65-stream-events)). Deploy at quiet times.

## 7. Security

The audit rules from [plan.md: Security audit](plan.md#security-audit) apply to every new endpoint. The match-specific points:

| ID | Risk | Fix |
| --- | --- | --- |
| M1 (S2) | Submitting into, watching or reading the results of someone else's match | The player comes from the cookie. Every match route, including results, checks that the caller was a player in that match and returns 404 otherwise. Match IDs are UUID4 |
| M2 (S3, S12) | Reading answers or rivals' words early | Answers never leave the server before `end`. The snapshot and stream filter per player: only your own words and display word, never scores. Others' display words appear only at the vote, without names. The grid itself is sent only at "GO". Tests check every payload against an allow-list of keys. **Accepted limit:** the `tiles` and `found` events together let a modified browser work out every score live. Scores are hidden in the game, not secret |
| M3 (S5) | Racing submits to score twice or after the end | Single-threaded state changes on the event loop, with no `await` between check and update. The per-player duplicate rule stops a second score of the same word, even from other same-text tiles. `closed_at` and the per-submit time check stop late submits and display words. The unique index backs this up in the DB. **Ballots:** one per player, checked and stored without an `await` in between, so a raced second ballot gets `already_voted`; the primary key on `match_votes` backs this up |
| M4 (S1) | Injection through the new inputs | The same Pydantic tile model as solo, UUID check on match IDs, and `?` placeholders in `save_match` and `match_results` |
| M5 (S7) | Holding connections open or flooding the queue | One stream per player (a new one replaces the old), the queue timeout, streams closed when idle (204), `MATCH_MAX_ACTIVE` and `MATCH_MAX_QUEUE` sized to Fly's limit, the queue-join rate limit, the submit gap, and the existing body cap |
| M6 (S8) | Nicknames in popups and results | `textContent` only, as everywhere else |
| M7 | Cross-site use of the stream | The stream is a GET with no side effects. The `SameSite=Lax` cookie isn't sent on cross-site subresource requests, and there's no CORS, so another site can neither open it as the player nor read it |
| M8 (S4) | Bots or one person on two accounts in the same match | **Accepted for now:** there's no board to climb, so a cheater only spoils single matches. The submit gap slows bots. Revisit if match boards come |
| M9 (S13) | Stored results keeping a deleted player's nickname | The snapshot holds user IDs, and nicknames are looked up on read ([§6.6](#66-database-migration-2)) |
| M10 | Display words used to spoil tiles, or to show offensive text | **Spoiling:** each display word locks the sender's own tiles, and the submit gap applies, so spoiling the board means giving up your own score; accepted for now. **Text:** a display word is built only from the board's letter pairs, at most `max_tiles` long, and shown with `textContent`. It can still spell something rude; accepted for friends-only play, with the blocklist check as an option ([§13](#13-open-questions)) |
| M11 | Rigging the vote | Ballots come only from the cookie's player, once, during the vote. Your own word isn't on your ballot and the server rejects it too. Names and scores are hidden while voting, against tactical voting. Ballots are never shown or returned, so nobody can be pressured over a vote. **Accepted limit:** one person on two accounts (M8) controls two ballots |

## 8. Layout (Claude Design)

These screens are added to the brief in [plan.md: Designed in Claude Design](plan.md#designed-in-claude-design), at 640×640 and in the phone sizes:

7. **Quick-match queue:** players waiting, the fill countdown, **Cancel**.
8. **Match found + 3-2-1:** the nicknames, with a large countdown and no grid.
9. **Match screen:**
   - the grid, where every tile shows its percentage (100 %, 50 %, 25 %, 13 % …), and its colour fades with it
   - tiles locked for you, which look clearly different from faded ones
   - your words without points, the 60-s timer and the popup stack, which never covers the grid
   - the **Display word** button next to Submit, and your current display word above your word list
   - the "already found" note
   - **the 10-s warning state**
10. **Results** (also opened from its link): places and scores, split into word score and vote bonus; each player's display word with its votes; each player's words with points; missed words; and the final board.
11. **Creativity vote:** the others' display words without names, three rank slots (5, 3, 1), **Send vote**, the 60-s timer, and "waiting for others (3 of 4 voted)" after sending. Also the state "no vote this time" when fewer than two display words exist.

**Also design:**

- the header mode switch with a third entry (**Free | Daily | Multi**)
- percentage and lock states at 5×5 and 10×10
- these notes: "no other players right now", "server busy, try again later", "match cancelled" and "Multiplayer is open in another tab"

**For tests:** each tile carries `data-pct` and `data-locked` attributes, and each ballot entry a `data-candidate` attribute, so the E2E tests don't depend on colours or order.

**Phones** (added 2026-10-13, see [mobile.md](mobile.md)).

Screens 7–11 are also designed at the solo game's phone sizes: 360×780, 375×553, 440×956 and 874×402 landscape. They follow the solo phone rules (mobile.md §8):

- the portrait breakpoint `max-width: 639px`
- the grid sized from its measured area (`web/js/grid.js`), with full-pitch tile buttons
- secondary lists in a bottom sheet (`web/js/sheet.js`)
- Submit in the thumb zone above the home indicator

Also design for phones:

- **Header:** Free | Daily | Multi doesn't fit at 320–360 px with the icons, so use the overflow solution chosen in solo task P9.
- **Tile percentage at 29-px tiles (10×10 on a 360-px phone):**
  - show it as a fill or opacity driven by `data-pct`, with numerals only at 36 px or more
  - the accessible name includes it, for example "GS, 50 percent"
- **Locked tiles:**
  - `aria-disabled="true"`, not `disabled`, so screen readers still announce them
  - a lock cue that doesn't rely on colour
- **Timer, 10-s warning and popup ticker:** in the status strip. The popup stack docks over the action area and never covers the grid. Popups are announced politely and collapsed in bursts ("2 players found words").
- **Your words during play:** a count or a one-line strip, not a sheet. 60 s is too short to keep opening one.
- **Display word:**
  - on the opposite edge from Submit, and visually distinct, because it can't be undone
  - no confirm step, because of the time pressure
- **Vote:** tap a word, then tap a rank slot. No drag and drop.
- **Results:** one scrolling column.

## 9. Testing

The test plan is in [test-strategy.md §6](test-strategy.md#6-multiplayer-after-go-live). It covers fixtures and helpers, test settings, the cases for each step M1–M8, E2E flow 10 and the coverage gates.

What the design does to stay testable:

- **`tick()` is synchronous** and returns jobs, so lobby tests need no event loop, threads or sleeps ([§6.3](#63-modules)).
- **`MATCH_TICK_MS = 0`** turns the ticker off for in-process tests ([§6.7](#67-configuration)).
- **Stream, concurrency and E2E tests** run on `live_match_server`, a server in a thread of the test process with a shared `FakeClock`. Starlette's `TestClient` can't consume an endless stream.
- **Tiles carry `data-pct` and `data-locked`** ([§8](#8-layout-claude-design)), so E2E tests don't depend on colours.
- **`match.py` takes the rng as an argument**, so candidate ids and ballot order are seeded in tests.
- **Coverage:** 100 % branch for `match.py`, and for `lobby.py` except `run()`.

## 10. Implementation plan

Multiplayer was planned **after plan.md step 7 (go-live)**. **Owner, 2026-10-12:** M1–M6 run locally and are built before go-live, since go-live waits on Fly access; only M7 needs the deploy. It needs:

- the solver (step 1) and the grid UI (step 2)
- the security stack (step 3) and players (step 4)
- `ensure_daily_pair`, from step 5, to keep the daily words off match boards

Each step ends with something runnable, and is done only when its tests in [test-strategy.md §6](test-strategy.md#6-multiplayer-after-go-live) pass.

The display word and vote are **built inside M1–M6**, not as a later step. They add a state to the lobby and columns and a table to migration 2, and adding them after M3 would need a migration 3 and a second pass over the state machine.

1. **M1 – Rules engine.** `trur/match.py` with the rounding, rules and time-boundary tests, including display words, ballots and the bonus.
   - *Done when:* there's 100 % branch coverage, and the worked example and the 1-point case pass as tests.
2. **M2 – Spike for matches.** Add `--match` to `scripts/spike_stats.py`, using `match.py`.
   - It reports the answers per pair, `pick_pair`'s acceptance rate and time per pick, and runs a seeded simulation.
   - The simulation: 1,000 matches, 2–6 greedy players, each submitting the best word still open to them every 6 s, for 60 s.
   - It gets a fixture-data test of its own.
   - *Done when:*
     - `MIN_MATCH_WORDS` is set
     - in at least 95 % of 6-player matches every player still has a word available at the end
     - fewer than 20 % of matches have all scores within 2 points
     - one pick takes under 1 s on a `shared-cpu-1x`
     - with random votes, the vote bonus changes the winner in **at most 30 %** of matches at every player count; otherwise lower `BONUS_SCALE` and say so in [§3.7](#37-creativity-vote)
3. **M3 – Schema and saving.** Migration 2, `save_match`, `match_results`, and the new server-only event types.
   - *Done when:* migration 2 applies on a fixture DB at `user_version` 1 (never a copy of production, which holds personal data); the results and saving tests pass.
4. **M4 – Lobby.** `Hub`, `Lobby`, `run()` and the state table from [§4](#4-quick-match-and-match-states).
   - *Security:* stream and queue limits, `server_busy` (M5).
   - *Done when:* the step M4 tests pass, with no real sleep anywhere in them.
5. **M5 – API and stream.** The routes, the SSE endpoint, the lifespan start and stop, the settings, and `--timeout-graceful-shutdown 2`.
   - *Security:* player checks (M1), payload allow-lists (M2), submit gap and rate limit (M3, M5), injection test extended (M4).
   - *Done when:* the step M5 tests pass, including a full match between two `httpx` clients on `live_match_server`.
6. **M6 – Design, then frontend.** Starts after checkpoint I of the phone tasks (tasks/todo.md P1–P13, owner 2026-10-13), so the match screens are phone-ready from the start.
   - First design the screens in [§8](#8-layout-claude-design) in Claude Design, at 640×640 and in the phone sizes.
   - Then build `web/js/match.js` (queue, countdown, match with the Display word button, vote, results, results link) and `web/js/stream.js` (EventSource wrapper: snapshot handling, `close()` on terminal events, "match cancelled" detection through `GET /api/match/current` when the stream closes without a terminal event). Share the grid rendering with `game.js` instead of copying it: use `web/js/grid.js` (render, measured sizing) and `web/js/sheet.js`.
   - **Phones:** the stream stops when the phone locks or the tab goes to the background. On `visibilitychange` back to visible, reopen it if it's closed and rebuild from the snapshot ([§6.5](#65-stream-events)). With `MATCH_STREAM_GONE_S` at 10 s, a player who glances at a notification during the vote counts as "done"; see [§13](#13-open-questions) question 7.
   - *Done when:* the design is approved, and E2E flow 10 passes.
7. **M7 – Release.**
   - Check the concurrency rule from [§6.8](#68-hosting-impact) against `fly.toml`.
   - Run `fly deploy`, then a two-browser smoke check on `https://trur.fly.dev`.
   - *Done when:* a real match between two devices works on production, a redeploy during a test match finishes within seconds and both browsers show "Match cancelled", and the machine auto-stops afterwards.
8. **M8 – Power-ups (later).**
   - First answer the power-up questions in [§13](#13-open-questions). Then build them one at a time, simplest first (double word score), behind `POST …/powerup`.
   - *Done when:* each power-up has unit tests for its effect, and personal hints are sent only to their owner.

## 11. Changes made to the other docs

Applied on 2026-10-11, from the reviews:

- **plan.md:**
  - *Goal* and *Decisions* mention multiplayer.
  - *Designed in Claude Design* points to screens 7–10.
  - *Build order* has multiplayer as step 8, and *Later* moves to step 9.
  - *Open questions* points to [§13](#13-open-questions).
- **architecture.md:**
  - The new modules, routes, error codes, event types, settings and migration 2 are listed, with links here.
  - The `async def` exception for match routes is recorded (§8 item 24).
  - `connect()` and `connect_ro()` use `check_same_thread=False` (§8 item 21). **This fixes solo too:** a sync dependency and its route can run on different threadpool threads.
  - `SecurityHeaders` is pure ASGI (§8 item 22).
  - The Dockerfile CMD has `--timeout-graceful-shutdown 2` (§8 item 23).
- **test-strategy.md:**
  - The new §6 *Multiplayer* holds the fixtures, the test settings, the cases per step M1–M8, E2E flow 10 and the coverage gate. CI and *Resolved gaps* moved to §7 and §8.
  - The route inventory includes the six match routes, and CI gets the per-file coverage step.

Applied on 2026-10-12, for the display word and creativity vote:

- **plan.md:** the *Decisions* row *Multiplayer* mentions the display word and vote; *Designed in Claude Design* points to screens 7–11.
- **architecture.md:** the routes `POST …/display` and `POST …/vote`, the error codes `match_not_voting`, `already_voted` and `invalid_ballot`, the event types `match_display_word` and `match_vote_cast`, and the migration 2 changes (`match_players` columns, `match_submissions.kind`, `match_votes`).
- **test-strategy.md §6:** cases for display words, ballots, the bonus, absent voters, payload leaks and E2E flow 10; the route inventory grows to eight match routes.

Applied on 2026-10-13, for phones ([mobile.md](mobile.md)):

- **§8:** the phone sizes and the phone design notes.
- **§10:** M6 waits for the phone checkpoint I, shares `grid.js` and `sheet.js`, and handles the paused stream on phones.
- **test-strategy.md:** E2E flow 10 also runs in a phone context. The phone section became §7, and CI and *Resolved gaps* moved to §8 and §9.

## 12. Assumptions (correct me if wrong)

1. **Your own score is hidden too** during the match. You see your words without points.
2. **The popup names the finder** ("FloMo found a 6-letter word"). The finder doesn't get the popup.
3. **2–6 players** per match, with a 10-s fill window and a 2-min queue timeout.
4. **A match always runs the full 60 s.** It doesn't end early, even if nobody can find more words.
5. **Results list the words nobody found** right away, as in free play. Match boards never repeat on purpose, and today's and tomorrow's daily words are excluded.
6. **A tile never drops below 1 %**, and every accepted word scores at least 1 point.
7. **The grid is revealed only at "GO",** not during the 3-2-1.
8. **Matches don't count toward** the 20-free-games limit or any board.
9. **Only a match's own players can open its results link.**

**Confirmed by the owner on 2026-10-12** (display word and vote; [§3.6](#36-display-word), [§3.7](#37-creativity-vote)):

- **"Display word" means the button's own submissions.** The notes say the button "keeps the last word that was submitted by this"; read as: the last word sent with this button, not your last normal submit.
- **A display word scores no word points**, even if it happens to be a real word. "Not checked against correctness" is read as "not scored".
- **"All other events"** means: lock for you, halve for everyone, and the popup to others. So a display word costs tiles, and can be used as a bluff.
- **Ballots are anonymous and scores are hidden while voting,** against tactical voting.
- **The vote is skipped** when fewer than two players set a display word.
- **The bonus is the average vote × 2,** capped at 10, not the sum of votes ([§3.7](#37-creativity-vote)).
- **The vote finishes once every connected player has voted.** A player whose stream has been gone for 10 s counts as done.

## 13. Open questions

1. ~~**Display word limits**~~ **Decided (owner, 2026-10-12):** no press limit and no blocklist. The tile cost and the submit gap are enough for friends-only play ([§7](#7-security) M10).
2. **Power-ups** (step M8):
   - Does every player get each power-up once per match, or one random power-up per match?
   - Does "reduce letter pair multiplier to half" mean spoiling a tile for everyone (as assumed), or halving the cut your own words cause?
   - Should "reduce visibility" also dim tiles that appear in no word at all?
   - How long should the two personal hints last?
3. **Private rooms:** add "play with a code" next to the queue once friends have tried quick-match?
4. **Sharing results:** should a results link also open for people who weren't in the match?
5. **Match boards:** if they come later, they need the S4 plausibility work for matches, and a fair measure, since scores depend on the number of players.
6. **Match history:** a screen listing your past matches would use the stored snapshots.
7. **Phones and the stream** (2026-10-13, [mobile.md](mobile.md)): with `MATCH_STREAM_GONE_S` at 10 s, a player whose phone locks, or who glances at a notification, counts as gone: "done" in the vote, and dropped from the queue. Raise the limit for phones, or accept it?
