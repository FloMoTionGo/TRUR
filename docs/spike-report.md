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

- **Reproduce:** `python -m scripts.spike_stats --match --sample 5000 --seed 1 --min-words 20` (add `--every 12` and `--bonus-scale 1` for the second run below).
- **Simulation:** 200 matches per player count (1,000 in all). Greedy players each submit the best word still open to them; a pair goes on its least-used free tile. At the end each player sets a two-tile display word, and everyone ranks a random ballot.

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

## Open: decisions for the owner

M2 stays open until these are settled. Nothing in `trur/match.py` or the settings has changed.

1. **How fast do real players find words?** A word every 6 s (the M2 text) can't meet the 95% target with any board size. A word every 12 s can. Real play data would settle it, but there is none before go-live.
2. **`MIN_MATCH_WORDS`:** 60 if the 12 s rate is accepted (14.5% of pairs qualify, still tens of thousands).
3. **`BONUS_SCALE`:** lower it from 2 to 1 (bonus 0–5 instead of 0–10), as §10 asks when random votes change the winner in more than 30% of matches. This changes the decided rule in §3.7 and the worked results example in `test_match.py`.
