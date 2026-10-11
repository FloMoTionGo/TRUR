# Word data

This folder is committed and public. The database never goes here (S9).

| File | What it is | Made by |
| --- | --- | --- |
| `words.txt` | The game dictionary: 79,031 Canadian English words, UPPERCASE, sorted, one per line | `scripts/build_wordlists.py` |
| `source_words.txt` | The 500 source words (5–10 letters). Line *n* has `freq_rank` *n* | `scripts/build_wordlists.py` |
| `blocklist.txt` | Offensive words, kept out of the dictionary and out of nicknames | by hand |
| `extra_words.txt` | Valid words SCOWL is missing, added to the dictionary (the blocklist still applies) | by hand |
| `daily_challenges.json` | Daily word pairs | `scripts/pick_daily.py` (from step 5) |

## Rebuilding

1. Download the SCOWL `en_CA.txt` wordlist and the `scowl.txt` database export into `build/scowl/`. The `build/` folder is gitignored.
   ```powershell
   curl.exe -L -o build/scowl/en_CA.txt https://raw.githubusercontent.com/en-wl/wordlist-diff/rel-2026.02.25/en_CA.txt
   curl.exe -L -o build/scowl/scowl.txt https://raw.githubusercontent.com/en-wl/wordlist-diff/rel-2026.02.25/scowl.txt
   ```
2. Install the dev lock, which pins `wordfreq`, then run:
   ```powershell
   python -m scripts.build_wordlists --scowl build/scowl/en_CA.txt --scowl-db build/scowl/scowl.txt
   ```
   With the same inputs, the output is byte-identical.

## Sources

**Dictionary: SCOWL `en_CA.txt`**, release `rel-2026.02.25`, from [en-wl/wordlist-diff](https://github.com/en-wl/wordlist-diff/tree/rel-2026.02.25).
- This is the default Canadian English speller list (ESDB size 60), with one spelling variant per word and inflected forms included.
- SHA-256 of the downloaded file: `905b1b6b0a98fcb3701c9c59290a090a4d6ab9127809017fefacbe9c2c6f15a4`.
- Filtering keeps only entries that are all lowercase a–z. That drops proper nouns, capitalised abbreviations, possessives (`'s`) and contractions.
- Lowercase abbreviations (ACCT, GEOG, TBSP, …) are removed using the part-of-speech tags in the same release's `scowl.txt`. A word is dropped only if it never appears there except as `<abbr>`, so words like AVE that are also ordinary words stay. This removes 196 words. SHA-256 of `scowl.txt`: `3da811b80d7163069e9676537845c49161a33505f24ad6bfcead92fe2cf10e41`.
- Blocklisted words are then removed by exact match, and the words in `extra_words.txt` are added.

**Word frequency: [`wordfreq`](https://pypi.org/project/wordfreq/) 3.1.1**, English list (`iter_wordlist("en")`). It is used only to rank the source words. wordfreq has no Canadian list, so this is general English frequency.

**Blocklist seed:** [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words), the `en` list at commit `5faf2ba`.
- Licence: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Only its single-word entries were used.
- Changes made: everyday, anatomical and clinical words were removed, and offensive inflected forms were added.

## SCOWL copyright notice

`words.txt` is a word list created from SCOWL, so this notice applies:

```text
Copyright 2000-2026 by Kevin Atkinson

Permission to use, copy, modify, distribute, and sell any part of SCOWLv2, or
word lists created from it, is hereby granted without fee, provided that the
above copyright notice appears in all copies and that both the above
copyright notice and this notice appear in supporting documentation.  Kevin
Atkinson makes no representations about the suitability of this database for
any purpose.  It is provided "as is" without express or implied warranty.

SCOWL is derived from many sources, most of which are in the Public Domain.
Data from the Corpus of Contemporary American English (COCA) was also used.

All data from COCA comes from 3-gram data that is not freely available;
however, the usage is within the rights given by the NDA that was signed when
purchasing the data.  More information on COCA is available at
https://www.english-corpora.org/coca/.

The primary source of words for SCOWL comes from 12dicts and ENABLE2K.  Both
are in the Public Domain, but Alan Beale <biljir@pobox.com> deserves special
credit as he is the author of 12dicts and a major contributor to ENABLE2K.  In
addition, he gave me an incredible amount of feedback and created a number of
special lists in order to help improve the overall quality of SCOWL.
```

The SCOWL notes say that for an official speller list that isn't Australian English, and no larger than size 80, this first notice is all that's needed.
