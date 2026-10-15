# TRUR – Feasibility spike

Step 1 of the [build order](plan.md#build-order), run on 2026-10-11. It checks whether the alternating-letter rule leaves enough words per grid, before any UI work.

- **Data:** the committed `data/` lists at the time (79,227 words, 500 source words; see [data/README.md](../data/README.md)). Removing 196 abbreviations afterwards (mostly 2–4 letters; the source words are unchanged) doesn't change the conclusions.
- **Sample:** 20,000 random ordered pairs (`--seed 1`) out of all 249,500. A 3,000-pair sample gave the same picture, to within a percentage point.
- **Reproduce:** `python -m scripts.spike_stats --sample 20000 --seed 1`.

## Results

**Words per grid:**

| min | p10 | p25 | median | p75 | p90 | max |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 7 | 13 | 23 | 43 | 74 | 716 |

| Grids with fewer than N words | N=1 | N=4 | N=8 | N=12 | N=16 | N=20 | N=30 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| share | 0.2% | 3.2% | 10.9% | 21.4% | 32.2% | 42.0% | 60.1% |

**Points per grid,** if every word were found: median 129, p90 514, max 7,976.

**Daily supply:**
- 13.7% of ordered pairs have a word of 10 or more letters.
- Projected over all pairs, that's about 34,000 ordered pairs and roughly 33,000 distinct unordered ones.
- With one pair a day and no repeats, the supply lasts about 90 years.

**Performance** (dev PC; expect a Fly `shared-cpu-1x` to be slower):
- The lexicon loads in 1.0 s and takes about 33 MB, which fits a 256 MB VM.
- Solving one grid takes 8.6 ms median and 37 ms max.
- The real 10×10 timing test (under 100 ms) also passes in CI.

## Verdict: go

The rule is not too strict. Only 3% of grids have fewer than 4 words, and the median grid has 23. The rules stay as they are.

## Thresholds set from the spike

| Setting | Value | Why |
| --- | --- | --- |
| `MIN_FREE_WORDS` (N) | **12** | It rejects the thinnest 21% of draws. A free grid then has at least 12 words, enough to play a full game. A redraw costs one ~9 ms solve, so on average about 1.3 solves per new game |
| `PLAUS_MAX_WPM` / `PLAUS_SOFT_WPM` / `PLAUS_MIN_CELLS` | **20 / 8 / 49**, kept as **provisional** | The spike measures grids, not how fast people play. These values stay until real play data exists. Revisit them after the first weeks of play, from the `word_accepted` events in the gameplay log |

## Findings for the owner

1. **Grids vary a lot.** The richest grids have over 100 times as many words and points as the thinnest ones that are still accepted (p90 514 points, max 7,976). Free-play monthly boards rank by best single game. Even with the 20-games-a-day limit, a lucky draw can therefore matter more than skill. The plan already rejected "percentage of the grid's possible points" as the ranking. Two options if this becomes a problem:
   - add a `MAX_FREE_WORDS` cap, or
   - rank free play by percentage after all.

   **Decision (owner, 2026-10-12):** add the `MAX_FREE_WORDS` cap at 74 words (p90), for now; revisit with real board data. Tracked as T25a.
2. **"All words found" will rarely end a game.** Median grids have 23 words, and big ones have hundreds. Most games will end by 3 wrong words or **End game**, as the plan expects.

---

# Matches (step M2), 2026-10-13

Step M2 of [multiplayer.md §10](multiplayer.md#10-implementation-plan). It plays seeded matches through `trur/match.py` to set `MIN_MATCH_WORDS` and to confirm the vote bonus.

- **Reproduce:** `python -m scripts.spike_stats --match --sample 5000 --seed 1` runs the decided rules (a word every 12 s, `MIN_MATCH_WORDS` 60, `BONUS_SCALE` 1). Add `--every 6 --min-words 20 --bonus-scale 2` for the first run below.
- **Simulation:** 200 matches per player count (1,000 in all). Greedy players each submit the best word still open to them; a pair goes on its least-used free tile. At the end each player sets a two-tile showcase word (then called display word), and everyone ranks a random ballot.

**Pairs accepted by a `MIN_MATCH_WORDS` rule** (5,000 sampled ordered pairs):

| N | 12 | 20 | 30 | 40 | 60 |
| --- | --- | --- | --- | --- | --- |
| accepted | 78.4% | 57.2% | 39.0% | 27.6% | 14.5% |

**As specified: a word every 6 s, `MIN_MATCH_WORDS` 20, `BONUS_SCALE` 2.**

| Players | Median word score | Everyone still has a word at the end | All word scores within 2 points | Max vote bonus | Winner changed by random votes |
| --- | --- | --- | --- | --- | --- |
| 2 | 49 | 13.0% | 37.0% | 10 | 0.0% |
| 3 | 39 | 9.0% | 17.5% | 10 | 24.0% |
| 4 | 31 | 8.5% | 8.0% | 10 | 39.0% |
| 5 | 29 | 11.0% | 3.0% | 10 | 43.5% |
| 6 | 25 | 9.0% | 1.5% | 10 | 35.5% |

Two of the M2 targets fail:

- **Players run out of words** (9% of 6-player matches end with a word left for everyone; the target is 95%). It isn't about the number of players: 2-player matches are at 13%. Ten words in 60 s lock most of a player's **own** tiles. A larger `MIN_MATCH_WORDS` barely helps: 100 words gave 37%, 150 words gave 54%, and picks then took up to 1 s.
- **Random votes change the winner too often** (up to 43.5%; the limit is 30%). `BONUS_SCALE` 1 alone gave 37%.

The close-scores target holds (13.4% of matches within 2 points; the limit is 20%), and picks take at most 23 ms on the dev PC.

**With a word every 12 s (5 words each, as the §3.7 assessment assumed), `MIN_MATCH_WORDS` 60 and `BONUS_SCALE` 1, every target holds:**

| Players | Median word score | Everyone still has a word at the end | All word scores within 2 points | Max vote bonus | Winner changed by random votes |
| --- | --- | --- | --- | --- | --- |
| 2 | 56 | 98.0% | 26.0% | 5 | 0.0% |
| 3 | 47.5 | 96.5% | 8.0% | 5 | 16.0% |
| 4 | 40 | 96.5% | 4.5% | 5 | 24.0% |
| 5 | 36.5 | 97.5% | 0.0% | 5 | 21.5% |
| 6 | 32 | 96.5% | 0.0% | 5 | 21.5% |

7.7% of matches within 2 points, and the slowest pick took 93 ms. At 12 s per word, `MIN_MATCH_WORDS` 40 reaches 91%, and 60 reaches 96.5%.

**With a word every 12 s and `MIN_MATCH_WORDS` 60, but `BONUS_SCALE` still 2:** random votes changed the winner in 24.5 / 30.5 / 35.0 / 30.0 % of 3/4/5/6-player matches. So the slower rate alone doesn't bring the vote under the 30 % limit; the scale has to drop too.

## Decisions (owner, 2026-10-13)

1. **Assumed play rate:** a word every 12 s, as the §3.7 assessment assumed. At 6 s no board size meets the 95 % target. Revisit with real play data after release: if players are faster, raise `MIN_MATCH_WORDS` or lengthen the match.
2. **`MIN_MATCH_WORDS` = 60** (14.5 % of ordered pairs qualify, still tens of thousands; 40 would reach only 91 %).
3. **`BONUS_SCALE` = 1** (bonus 0–5 instead of 0–10), as §10 asks. [multiplayer.md §3.7](multiplayer.md#37-creativity-vote) and the worked results example are updated; in that example B now wins on word score, where A's 10-point bonus used to decide it.

## Source-word lengths on a bell curve (owner, 2026-10-14)

`pick_pair` now draws each source word's length on a bell curve around 6 letters (standard deviation 1 letter), then a word of that length ([plan.md](plan.md), *Source-word lengths*). The match spike was rerun with 600 matches per player count, once with the new draw and once with the old uniform one, both with `--sample 5000 --seed 1`, a word every 12 s, `MIN_MATCH_WORDS` 60 and `BONUS_SCALE` 1:

| Check | Uniform draw | Bell curve | Target |
| --- | --- | --- | --- |
| 6-player matches where everyone still has a word at the end | 96.3% | 95.0% | at least 95 % |
| Matches with all word scores within 2 points | 7.5% | 8.1% | under 20 % |
| Highest share of matches whose winner random votes changed | 20.5% | 21.3% | at most 30 % |
| Median word score, 6 players | 33 | 30 | – |
| Slowest pick on the dev PC | 205 ms | 305 ms | under 1 s |

Every target still holds, but the first one has no margin left: smaller boards leave 6 players fewer free tiles. With 200 matches per count it read 94.0 %, within that sample's noise of about ±1.5 points.

## 3-minute matches (owner, 2026-10-15)

The owner raised `DURATION_S` from 60 to 180 s. The match spike was rerun with the new length, `--sample 5000 --seed 1`, 200 matches per player count, `MIN_MATCH_WORDS` 60 and `BONUS_SCALE` 1, at three play rates:

| Check | A word every 12 s (15 each) | Every 24 s (7 each) | Every 36 s (5 each) | Target |
| --- | --- | --- | --- | --- |
| 6-player matches where everyone still has a word at the end | 0.0% | 68.0% | 94.0% | at least 95 % |
| Matches with all word scores within 2 points | 7.5% | 6.7% | 8.3% | under 20 % |
| Highest share of matches whose winner random votes changed | 23.0% | 25.5% | 28.5% | at most 30 % |
| Median word score, 6 players | 35 | 34 | 30 | – |

The vote and close-scores targets hold at every rate. Whether players run out of words depends only on how many words each player submits: at the assumed rate of a word every 12 s, the first target fails in every match, because 15 words lock most of a player's own tiles. At a word every 36 s (5 words each, as with 60 s at 12 s) it is back at the old level. So the longer match fits slower players. If real matches show players running dry before the end, raise `MIN_MATCH_WORDS` or shorten the match again.
