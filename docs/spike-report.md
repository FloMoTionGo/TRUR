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
