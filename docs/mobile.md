# TRUR – Phones

How the game gets a portrait-first phone layout. The 640×640 desktop layout doesn't change.

Status: **planned, nothing built yet.** This started as a proposal (`tasks/mobile-plan.md`). On 2026-10-13 it was reviewed by three agents (architecture, test engineering, UX and accessibility) and checked against the code, and the owner's decisions were applied. The tasks are **P1–P17** in [tasks/todo.md](../tasks/todo.md#phase-9-phones), and the tests are in [test-strategy.md §7](test-strategy.md#7-phones).

**Why this is needed.** Today `.app` is a fixed 640×640 box (app.css:68–77). `game.js` sizes tiles from hard-coded constants (`GRID_WIDTH` 410, `GRID_HEIGHT` 470, `WON_HEIGHT` 70). On a 360-px phone:

- the found list (a 190 px side panel) covers half the grid
- the grid starts off-screen, at about x = −97
- the page doesn't scroll sideways, because `.app` shrinks as a flex item and hides its overflow, so nothing even looks broken to a simple scroll check

## 1. Goal and constraints

- **Goal:** every screen works on the phones in §3, in portrait and landscape, and keeps the approved "Olive board" look.
- **The desktop stays exactly as it is.** At viewports of at least 640 × 640 px, every element keeps its position, size and computed style, and E2E flow 9 stays unchanged. Task P1 records a baseline of this before any CSS change, and every later task must pass it.
- **One codebase, CSS first, no framework and no build step**, as today.
- **The CSP rules stay as they are:**
  - JS sets only custom properties (through `style.setProperty`, which is allowed), `data-*`, `aria-*` and state attributes.
  - Text is set with `textContent` only.
  - One addition is allowed: `applyTheme()` sets the `content` of the `theme-color` meta (§6.1).
- **Order (owner, 2026-10-13):**
  - Phases 1–2 (checkpoints H and I) come **before multiplayer M6**, so the lobby and match screens are built phone-ready.
  - The phone screens are approved in Claude Design (P9) before the phase-2 screen work.

## 2. Decisions

| Topic | Decision | Why |
| --- | --- | --- |
| Task IDs | **P1–P17**, checkpoints H, I, J | The proposal's M1–M9 clashed with multiplayer M1–M8 (owner, 2026-10-13) |
| Order | Phases 1–2 before multiplayer M6; M6 waits for checkpoint I | So the match screens don't need a second, phone pass (owner, 2026-10-13) |
| Design gate | Phone screens approved in Claude Design (P9) before P10–P12 | As T6 did for desktop (owner, 2026-10-13) |
| Phases | **1:** P1–P8, checkpoint H (playable). **2:** P9–P13, checkpoint I (all screens; M6 may start). **3:** P14–P16, checkpoint J (landscape, CI matrix, real devices). **4:** P17 (optional install) | Each phase ends with something a player can use |
| Breakpoints | `(max-width: 639px)` for portrait phones. `(min-width: 640px) and (max-height: 639px)` for landscape phones **and short desktop windows** (owner, 2026-10-13) | The proposal's single `(max-height: 639px)` would have given short laptop windows (1366×768 with browser chrome is about 625 px high) a stretched portrait layout. They get the side-by-side landscape layout instead, which fits them (§4) |
| Desktop tile sizes | Stay fixed: the 410 × 470 box and the 70 px banner come from CSS custom properties. **Only phones measure** `.grid-area` | The real desktop box is 434 × 488, and the banner is about 71 px tall depending on fonts. Measuring on desktop would change tile sizes (§5.2) |
| Taps between tiles | **CSS, not JS.** On phones every tile button covers its whole grid cell, including the gaps, and the visible tile is drawn on `::before` | Keyboard and screen-reader clicks carry no coordinates (`clientX = 0`, `detail = 0`), so JS coordinate mapping would pick the wrong tile. The CSS route needs no JS and doesn't change desktop behaviour. JS mapping (`cellAt`) is the fallback if CSS fails on a real device (§5.3) |
| Found list on phones | A bottom sheet: one `<dialog>`, into which the existing `<aside class="found">` is moved on open and back on close | There's no duplicate DOM, so test IDs stay unique, and the native dialog handles Esc, the inert background and the Android back gesture (§6.4) |
| End button | A **confirm dialog** on phones, with End small at the far left of row B. **Desktop keeps today's End**, with no confirm (owner, 2026-10-13) | Ending a daily by accident is final for the day, and a mis-tap is likely on a phone. Desktop keeps the approved design and its five e2e tests |
| Remove-last | **Yes** (owner, 2026-10-13): a ⌫ button at the right end of row A on phones, and the Backspace key on every viewport. No visible button on desktop | Fixes a mis-tap with one tap on a large button instead of a second precise tap on a 29-px tile (§6.3) |
| Focus ring | `outline: 3px solid var(--frame)` on **every viewport** (owner, 2026-10-13) | Orange on white is 2.85:1, below WCAG's 3:1, on desktop too. Near-black is 16.7:1 in light mode, cream 15.1:1 in dark. Mouse clicks never show it (`:focus-visible`) |
| 10×10 grids | **Kept** on every phone, with no cap and no zoom toggle (owner, 2026-10-13) | Capping would split the shared daily and multiplayer boards and make free-play rankings depend on the device |
| Toast on phones | Docked **over the action area**, and dismissed by the next tile tap | Almost every toast follows a submit, which clears the selection, so Clear and Submit are disabled anyway. The rare others (a failed mode switch or End) also go on the next tap. Docked above the action area, a 120-px toast covered the grid's bottom rows |
| Shell height | `.app` is a fixed `100dvh` shell (with `100vh` before it as the fallback) on every screen, and the document never scrolls. The game screen fits the shell; the other screens scroll inside `.screen` | With a document that doesn't scroll, the browser bars don't collapse, so there's no `dvh` jump. The other screens must scroll so that zoom and large text don't clip them |
| Hint text | "Click tiles…" or "Tap tiles…" is picked in `render()` with `matchMedia("(hover: none) and (pointer: coarse)")` | One text node, so flow 3's `to_have_text` and the D1 baseline stay unchanged. It depends on the input device, not the breakpoint. Two CSS-swapped spans were rejected: Playwright's text checks read the hidden span too |
| Shots helper | `python -m scripts.mobile_shots` (argparse, `--help`), not bash | `scripts/` holds Python modules, and local development is PowerShell (Arch §7). It shares the device list and screen recipes with the tests |
| Browsers in CI | Chromium on every push. WebKit weekly, for 2 devices | WebKit isn't installed today. Linux WebKit isn't iOS Safari, but it catches CSS differences |

## 3. Target devices and design envelope

| Device | Screen (CSS px) | Visible with bars | DPR |
| --- | --- | --- | --- |
| iPhone SE (2nd/3rd gen) | 375 × 667 | **375 × 553** (worst case) | 2 |
| iPhone 16e | 390 × 844 | – | 3 |
| iPhone 16 / 17 / 17 Pro | 393–402 × 852–874 | 402 × 681 | 3 |
| iPhone Air | 420 × 912 | – | 3 |
| iPhone 16 Plus / 17 Pro Max | 430–440 × 932–956 | 440 × 763 | 3 |
| Galaxy S25 / S25+ | 360–384 × 780–832 | – | 3 / 2.8 |
| Pixel 10 / Pro / Pro XL | 410–448 × 914–997 | 412 × 839 (Pixel 7) | 2.6–3 |
| Landscape, any of these | 667–956 × 360–440 | 756 × 308 (shortest) | |

375 × 553 is real iOS Safari with its bars showing. The other "visible" values come from Playwright 1.63's descriptors, which subtract the bars: iPhone 17 (402 × 681), iPhone 17 Pro Max (440 × 763) and Pixel 7 (412 × 839; Playwright's Pixel 10 is 360 × 732). The shortest landscape descriptor is Pixel 10 at 756 × 308. Screen sizes alone are optimistic for layout.

**Design envelope.** Width **360–448**, usable height **at least 550**. The layout must still work at **320 px wide**, the WCAG reflow floor and the CSS width at about 115 % zoom on a 375 phone. At 320 it may look tighter, but nothing may be clipped.

- No fixed width over 296 px (320 − 2 × 12). Anything wider uses `min(100%, …)`.
- Respect `env(safe-area-inset-*)` with `max()`, for example `padding-left: max(12px, env(safe-area-inset-left))`, so the gutter and the inset don't add up.
- Playwright always reports the insets as 0, so they're checked on real devices only (P16).

## 4. Breakpoints

```css
/* app.css, appended after the desktop rules so equal specificity wins by order */
@media (max-width: 639px) { … }                                   /* portrait phones, narrow windows */
@media (max-width: 399px) { … }                                   /* inside the phone range: no wordmark, short tab labels, stacked nickname button */
@media (min-width: 640px) and (max-height: 639px) { … }   /* landscape phones and short desktop windows (P14) */
```

- The portrait and landscape blocks never overlap: one needs a width under 640 px, the other a width of 640 px or more.
- **Accepted limit:** a landscape view narrower than 640 px (the 1st-gen iPhone SE at 568 × 320, or a phone zoomed past about 115 % in landscape) gets the portrait layout with very small tiles. It is outside the design envelope; the shortest landscape in §3 is 756 px wide, and pinch zoom still works.
- Every viewport of at least 640 × 640 renders exactly as today. That includes the D1 baseline sizes, 1000×800 and 640×640.
- **Short desktop windows** (at least 640 wide, under 640 high) get the landscape layout too (owner, 2026-10-13). They keep mouse behaviour: the hint still reads "Click tiles…", because it follows the pointer type, not the breakpoint. TS P14.3 (`desk-short`) covers this.
- JS has one copy of the portrait query: `PHONE_QUERY` in `web/js/grid.js`, with a comment pointing at app.css. It is used only to close the sheet when the layout flips. Two more `matchMedia` checks pick visible text, not layout: the tap/click hint (§2) and the short board-tab labels (§6.6). Sizes reach JS through CSS custom properties, never through media queries.

**Changes outside the media blocks** (the complete list, so a reviewer can check "desktop unchanged"):
1. `.grid` gets `--head: 24px; --max-tile: 48px; --gap-dense: 4px; --gap-loose: 6px; --fit-w: 410px; --fit-h: 470px; --won-h: 70px`, and its template uses `var(--head)`. These are today's values.
2. `html { touch-action: manipulation; -webkit-text-size-adjust: 100%; -webkit-tap-highlight-color: transparent }`. None of these change anything visible on desktop. Never use `text-size-adjust: none`, which blocks the user's text scaling.
3. New elements are hidden on desktop: `.found-open`, the ⌫ button and the never-opened `dialog.sheet`. The leaderboard tabs get `aria-label`s with today's full names, which changes no pixels.
4. Screen-reader live regions (§7) are visually hidden, so they add no pixels.
5. **Approved desktop changes** (owner, 2026-10-13), neither visible to mouse players:
   - `:focus-visible` uses `var(--frame)` instead of orange (§7)
   - the Backspace key removes the last selected tile (§6.3)

## 5. Grid sizing and tapping

### 5.1 Sizes on phones

On phones (portrait and landscape): gutter 12, head 16, gap 3 for grids of 8 or more columns or rows and 4 below that, and `--max-tile` 56. "Tile / pitch" means tile size / tile + gap, floored as `fitGrid` does:

| Width | 10×10 | 8×8 | 6×6 |
| --- | --- | --- | --- |
| 320 | 25 / 28 | 32 / 35 | 42 / 46 |
| 360 | 29 / 32 | 37 / 40 | 49 / 53 |
| 375 | 30 / 33 | 38 / 41 | 51 / 55 |
| 402 | 33 / 36 | 42 / 45 | 56 / 60 |
| 412 | 34 / 37 | 43 / 46 | 56 / 60 |
| 440 | 37 / 40 | 47 / 50 | 56 / 60 |

**Height on the worst case: 375 × 553 with a 10×10 grid.**

| Part | Height |
| --- | --- |
| Header row | 48 |
| Status strip | 32 |
| Grid: head 16 + 10 × (30 + 3), plus `.grid-area` padding of 8 at top and bottom (12 at the sides) | 346 + 16 |
| Action area | 100 (the design may take up to 116; the tiles then lose 1 px) |
| **Total** | **542 of 553** |

- **Won daily at 10×10 on 375 × 553:** a compact banner of about 40 px makes the grid height-bound at tile 27 / pitch 30. P9 should consider showing "won" in the status strip instead (risk R5).
- **Touch targets:**
  - **WCAG 2.5.8 (24 px)** is met wherever the pitch is at least 24. In portrait the pitch is always at least 28.
  - **Apple's 44 pt and Material's 48 dp** can't be met at 10 columns on any phone: 10 × 44 + head is about 456 px. This is a documented deviation. It is mitigated by:
    - full-pitch buttons (§5.3)
    - the preview before Submit
    - no penalty until Submit
  - The 10×10 question stays open (§11).
- **Tile text:** `font-size: max(12px, calc(var(--tile-size) * .35))`, and `letter-spacing: 0` when `data-dense="true"` (tile < 36). Both rules are **phone-only**: desktop 10×10 tiles are 34 px with 11.9 px text, and must stay that way.

### 5.2 Measured sizing (`web/js/grid.js`, shared with multiplayer M6)

- `buildGrid(el, word1, word2)` builds the DOM, as game.js:82–97 does today. It runs only for a new game.
- `fitGrid(el)` sets only `--tile-size`, `--gap` and `data-dense`, and never rebuilds the tiles, so focus and a running reject flash survive a resize.
  - **Desktop:** `--fit-w` is set. It uses the fixed box 410 × 470, minus `--won-h` when the banner shows. The tile sizes are exactly today's for all 72 grid shapes (5–10 × 5–10, won or not).
  - **Phones:** `--fit-w` is unset (`none` in the phone block, so `parseFloat` gives NaN). It measures `.grid-area`'s content box (`getBoundingClientRect` minus padding).
  - `size = min(--max-tile, floor((w - head - gap*cols)/cols), floor((h - head - gap*rows)/rows))`, as today.
  - **Integer hysteresis:** it writes only when `size/gap` changes (`data-fit` key). That avoids relayout loops and "ResizeObserver loop" errors.
  - It returns without writing when the box is 0 × 0 (the screen is hidden).
- `observeGrid(el)`: one `ResizeObserver` on `.grid-area`, calling `fitGrid`. It handles rotation, the won banner appearing and the browser bars.
- **Call sites in game.js:**
  - delete the constants at lines 17–21
  - call `fitGrid` synchronously right after `showScreen("game")`, because `buildGrid` runs while the screen is hidden; without it, Playwright could read tiles at the CSS default of 48 px
  - call `fitGrid` after toggling `won.hidden` in `render()`
  - delete line 202 (`if (!wasWon && isWon()) buildGrid()`)
- `.grid-area` on phones: `flex: 1 1 0; min-height: 0; overflow: hidden`. Its size must never depend on its content, or the ResizeObserver loops.

### 5.3 Taps between tiles: full-pitch buttons (CSS)

Inside both phone blocks, portrait and landscape (P14 repeats these rules and the §5.1 values in the landscape block):

```css
.grid { gap: 0;
        grid-template-columns: var(--head) repeat(var(--cols), calc(var(--tile-size) + var(--gap)));
        grid-template-rows: var(--head) repeat(var(--rows), calc(var(--tile-size) + var(--gap))); }
.tile { width: calc(var(--tile-size) + var(--gap)); height: calc(var(--tile-size) + var(--gap));
        position: relative; isolation: isolate; background: none; border: 0; box-shadow: none; }
.tile[data-state] { background: none; }   /* beats the desktop state rules (0,2,0) */
.tile::before { content: ""; position: absolute; inset: calc(var(--gap) / 2); z-index: -1;
        background: var(--tile); border: 2px solid var(--tile-edge); box-shadow: 2px 2px 0 var(--tile-shadow); }   /* 2 px is provisional until P9 */
.tile[data-state="selected"]::before { background: var(--orange); border-color: var(--frame); }
.tile[data-state="rejected"]::before { background: var(--red); border-color: var(--frame); }
.tile:focus-visible { outline-offset: calc(var(--gap) / -2); }
```

- Every point of the grid is a real `<button>`. `click`, Enter and Space, VoiceOver and TalkBack activation, and Chromium's touch adjustment all go through the unchanged handler (game.js:170).
- The tracks are tile + gap wide, so the grid stays head + cols × (tile + gap) wide, as the sizing formula assumes. The visible tiles are inset by half a gap, so the head letters sit half a gap from them (desktop: a full gap); P9 can add padding to the heads if that looks tight.
- Head letters remain outside the buttons, so tapping them selects nothing.
- **Press feedback moves only `::before`** (P6, 2026-10-13). Moving the button itself on `:active` let a press near its left edge end on the neighbour, and the browser then fired no click at all; TS P6.7 caught it.
- **Tap only, no drag:** the tiles of a word aren't adjacent, and drag would fight pan and pinch.
- Selection stays on `click` (the up event, WCAG 2.5.2), never on `pointerdown`.
- **Fallback** if a real device shows a problem: `cellAt(grid, x, y)` in grid.js, used only when `event.detail > 0` and the target isn't a tile. Never add fields to `tile_clicked`: the event models are `extra="forbid"` (api.py:97, 138), so an unknown field rejects the whole batch with 422.

## 6. Screens on phones

### 6.1 Page shell (P3)

- **Viewport meta:** `width=device-width, initial-scale=1, viewport-fit=cover`. Never disable zoom.
- **Theme colour:** one `<meta name="theme-color">`. `applyTheme()` (main.js:24–32) sets its `content` from the computed `--ground`, so it follows the in-app toggle and not just the system setting.
- `body` on phones: `display: block; min-height: 0; background: var(--ground)`.
- `.app` on phones: `width: 100%; height: 100vh; height: 100dvh` and `padding-top: env(safe-area-inset-top)`.
- **Scrolling:** `.screen` gets `overflow-y: auto` for every screen except the game screen.
- **Use `min-height` instead of `height`** for the bar, buttons, strip and action area.
- **Centring:** `justify-content: safe center` where content can be taller than the screen.
- **Overscroll:** `overscroll-behavior: contain` on the real scrollers: the found list, `.over-grid`, `.answers-list` and `.boards-scroll`.
  - Pull-to-refresh belongs to the root scroller, so `overscroll-behavior` on `.app` would do nothing.
  - Reloading is harmless, because game state lives on the server.
- **Grid:** `user-select: none`, `-webkit-user-select: none`, `-webkit-touch-callout: none` and `cursor: pointer`.
- **Press feedback:** the tap highlight is removed, so buttons need an `:active` style (the pressed offset, without the shadow). iOS applies `:active` only when a touch listener exists, so add a passive no-op `touchstart` listener on `document`.

### 6.2 Header and status strip (P4)

- **Row 1 (48 px):**
  - the compact logo (no wordmark below 400 px)
  - **Free | Daily**
  - Leaderboards and the theme toggle
  - all of them at least 44 × 44
- **Row 2 (32 px):** the status strip on the game screen; the title on the answers and boards screens.
  - **Score · Tries · Found 4 of 23**, with "Found" written out because screen readers read "4/23" as a fraction or a date
  - The Found button (`data-testid="found-open"`, `aria-haspopup="dialog"`) opens the sheet. Its hit area is extended to 44 px by a `::after { inset: -8px -2px }` (the `::after` is placed inside the 2 px border, so 6 px past the border needs −8) and `z-index: 1`, so the extension wins over the row above and the screen below, with no layout cost. P4 adds it as the counter, and P5 makes it open the sheet.
- **Header width:** Free | Daily | Multi plus two 44 px icons doesn't fit at 320–360 px. P9 decides where Leaderboards and the theme toggle go below 400 px, for example a "⋯" menu, together with End (§11).
- **Answers and boards screens:** "Back to game" stays in row 1, and the title moves to row 2.

### 6.3 Game screen (P6, design in P10)

- **Layout, top to bottom:**
  - the compact won banner: one line, dismissible
  - the grid: `flex: 1`, centred
  - the action area, sitting above the home indicator with `padding-bottom: env(safe-area-inset-bottom)`
- **Row A:** the word preview, then the **⌫ remove-last** button at its right end (48 × 44, `aria-label="Remove last tile"`, disabled while nothing is selected). The chips are hidden on phones, because 10 chips are about 390 px and the preview already shows the word. The preview wraps or steps its font size down; it never scrolls sideways.
- **Remove-last** (owner, 2026-10-13):
  - ⌫ and the Backspace key (on every viewport) take the last tile off the selection, as tapping that tile again would.
  - Backspace is ignored while focus is in a text input, while a dialog is open, while busy and after game over.
  - It is logged as `tile_clicked` with `selected: false` for that tile, so the event API doesn't change.
- **Row B:** **Clear** (secondary) and **Submit** (primary, `flex: 1`), both at least 48 px high. **End** sits at the far left, away from Submit, as a small button. Tapping it opens a confirm dialog (§6.4's dialog code): **Keep playing** has focus, and **End game** comes second. For a daily the text says "You can't play today's challenge again." Built in P10.
- `#screen-game .solution` and `#screen-over .solution` need the screen id for specificity, because both screens share `.solution`.

### 6.4 Found sheet (P5)

- **Markup:** `<dialog class="sheet" id="sheet" aria-labelledby="found-open">` holds a close button with `autofocus` and `aria-label="Close"`. The dialog is named by the Found button ("Found 4 of 23"), not by `.found-head`: an id on `.found-head` would rename that desktop element in the D1 baseline (P5, 2026-10-13).
- **`web/js/sheet.js`:**
  - `openSheet(content, opener)` moves the aside in and calls `showModal()`.
  - On `close`, it puts the aside back after `.grid-area` and calls `opener.focus()`. iOS Safari doesn't focus a button on tap, so the native focus return goes to `<body>`.
- **Closing:**
  - the close button
  - Esc (`cancel`), and on Android the back gesture
  - a tap on the backdrop, detected as `click` with `event.target === dialog` (the dialog has `padding: 0`, and an inner `.sheet-body` fills it); `closedby="any"` is added as progressive enhancement
  - when `PHONE_QUERY` stops matching
  - in `showScreen()` (screens.js:5); otherwise "Past answers" inside the sheet would open the answers screen under the modal
- **CSS:**
  - `max-height: 60svh`, with its own safe-area padding, since the top layer is outside `.app`
  - a 2 px `--frame` top edge and a `::backdrop` scrim; the panel colour alone is only 1.15:1 against the ground
  - `html:has(dialog[open]) { overflow: hidden }` locks the page behind the sheet
  - a slide-in via `@starting-style`, turned off under `prefers-reduced-motion`
  - no grabber unless drag-to-dismiss exists
- **Footer:** the daily answers note and "Past answers" are already in `.found-foot` (index.html:91–94), so they move into the sheet with the aside.
- **After an accepted word:**
  - The Found button flashes a `+4`: an `aria-hidden` span, ink on an orange chip, never orange text on white (2.85:1).
  - To restart the animation, `data-flash` alternates between `a` and `b`, which use two identical keyframes. This is CSP-safe.
  - Under reduced motion: a static highlight for about 1 s.

### 6.5 Toast (P8)

- `width: min(380px, calc(100% - 24px))`, docked over the action area, above the safe-area inset.
- **Compact on phones** (P8, 2026-10-13): 13 px body, tighter padding, and **no hint line**. Docked over the action area (about 102 px), the full rejected toast with its hint was about 140 px tall at 375 wide and covered the bottom tile rows at `se-bars`. Whether the hint comes back elsewhere is for the P9 design.
- It is dismissed by the next tile tap, or after `TOAST_MS`. Whether rejected toasts on phones should stay up for 6 s or more (WCAG 2.2.1) is decided in P10.

### 6.6 Game over, past answers, leaderboards, nickname (P11, P12)

- **Game over:**
  - one scrolling column: the score block, "Your words" at full width, then missed words in **2** columns
  - **New game** stays reachable
- **Past answers:**
  - **2** columns
  - remove `min-width: 210px` from `.answers-day`
  - the "you" badge goes on its own line, and the legend wraps
- **Leaderboards:**
  - three equal tabs (`flex: 1`)
  - below 400 px the visible labels are "Free", "Daily" and "Today", set by `boards.js` from a `(max-width: 399px)` `matchMedia`. Each tab keeps its full name as `aria-label` ("Free · month" …), so the desktop tests (test_flows.py:262–277) still find it, and the short label stays part of the name (WCAG 2.5.3)
  - the hint on its own line
  - the sticky table header already works (app.css:364–366)
- **Nickname:**
  - Layout: the form `width: 100%` (max 360) and the privacy text (max 420); the button goes below the input below 400 px; the logo tiles are 48 px; the form is top-aligned so the input and error stay above the keyboard.
  - Input attributes: keep `autocomplete="nickname"`, and add `enterkeyhint="go"`, `autocapitalize="off"`, `autocorrect="off"` and `spellcheck="false"`. The input is already 17 px, so iOS doesn't zoom on focus.
  - No `input.focus()` at boot on touch devices (main.js:51), because the keyboard would cover the privacy text.

### 6.7 Back gesture (P13)

The answers and boards screens push a history entry, and `popstate` calls `onBack()`. Otherwise Android Back or the iOS edge swipe leaves the site. Multiplayer's `#match/<id>` routing builds on the same handling.

### 6.8 Landscape phones (P14)

- The grid takes the full height on the left. The header, found list and actions go in the right column.
- The landscape block repeats the phone grid rules: the §5.1 values (head, gaps, `--max-tile`, tile text), `--fit-w: none` so the grid is measured (§5.2), and the full-pitch tiles (§5.3).
- The safe-area insets on both sides cover the notch.
- No sheet in landscape: the found list is inline again, and an open sheet closes when the layout flips.
- **Pitch target:** on 756 × 308 (the shortest landscape), 10×10 gives about 26 px. That passes WCAG 2.5.8 but not 32. The landscape tests assert a pitch of at least 24.

### 6.9 Home-screen install (P17, optional)

- **`web/manifest.webmanifest`:**
  - name, icons, `display`, `theme_color` and `background_color`
  - no `orientation` key (WCAG 1.3.4)
- **Icons:** PNG, committed once and made from logo.svg, with no build step:
  - `apple-touch-icon` 180 × 180
  - 192 and 512 for Android, plus a maskable one
- **Serving:** `StaticFiles` already serves `.webmanifest` as `application/manifest+json`, and the CSP `manifest-src` falls back to `default-src 'self'`. No server change is needed.
- **No service worker:** the game needs the API anyway.
- **Before building, the owner decides on R6:** a home-screen app on iOS has its own cookie jar.

## 7. Accessibility rules (all screens, applied in P7)

- **Announcements:** two persistent, visually hidden regions, `role="status"` and `role="alert"`. They are never `hidden`, and their text is set after mount.
  - **On an accepted word:** "GEAR, 4 points. 5 of 23 found." Today an accepted word is silent.
  - **Toasts** are announced through the regions.
- **Noisy regions to fix:**
  - Remove `aria-live` from `.solution-word`: `aria-pressed` already reports selection, and every render re-announces it.
  - Set `textContent` on the won banner and the counter only when the value changes.
- **Grid label:** `role="group"` with `aria-label`, for example "Letter grid: GARDEN across, SUMMER down". A name on a plain `div` is ignored.
- **Focus ring:** `outline: 3px solid var(--frame)` on every viewport (owner, 2026-10-13). Orange was 2.85:1 on white and blended into a selected tile. The frame colour is 16.7:1 in light mode and 15.1:1 in dark mode.
- **Toast roles:** the toast stops switching its own `role` (game.js:152). The regions announce it, so it isn't read twice.
- **Never use `role="grid"`:** it needs the full arrow-key model and makes screen readers verbose.

## 8. What multiplayer M6 inherits

- the portrait breakpoint and `PHONE_QUERY`
- `grid.js` (`buildGrid`, `fitGrid`, `observeGrid`) and the full-pitch tile CSS, which also covers [multiplayer.md §10](multiplayer.md#10-implementation-plan)'s "share the grid rendering with game.js"
- `sheet.js`, the live regions, the history handling, and the phone test harness (`tests/e2e/devices.py`, `layout.py`)
- the popup stack docked over the action area, never over the grid

Match-only notes are in [multiplayer.md §8](multiplayer.md#8-layout-claude-design).

## 9. Corrections to the proposal

| Proposal said | The code says |
| --- | --- |
| The desktop's measured grid box is 410 × 470 | It is **434 × 488**: 640 − 190 − 16 wide, 640 − 64 − 88 high. The won banner is about 71 px, not 70 |
| Head 16, gap 3 | Desktop uses head **24** and gap **4/6**. The phone values become CSS custom properties |
| 10×10 height: 9 gaps (343) | **10** gaps (346). The total is 542, which still fits |
| The 8-column cells at 375 and 440 | 38 and 47, not 39 and 46. With `MAX_TILE` 56, 6 columns go up to 56 |
| `scrollWidth ≤ clientWidth` proves the layout fits | It passes on today's broken layout. The tests add clipping, hit-testing and tile-fit checks (TS §7) |
| `overscroll-behavior: none` on `.app` | It does nothing there. Use `contain` on the real scrollers |
| `body min-height: 100dvh` | `100vh` on body makes the document scroll on iOS, which is what causes the bar collapse and the `dvh` jump |
| The keyboard resizes `dvh` | It doesn't, on iOS or on Chrome 108+ |
| Daily note and "Past answers" move into the sheet | They're already inside the aside, so nothing to do |
| The sticky leaderboard header | Already there |
| Tests in phase 3 | The desktop baseline comes first (P1), and the phone harness with the first phone task (P3). Each task adds its own cases |
| `test_flows.py:138` | The desktop fit test (flow 9) is at line 136 |

## 10. Risks

| # | Risk | Mitigation |
| --- | --- | --- |
| R1 | Desktop tile sizes drift | Fixed desktop box (§5.2), the P1 baseline and the 72-shape tile-size check |
| R2 | 10×10 at 360 px is too fiddly | Full-pitch buttons. Real-device test at checkpoint H. Decision in §11 |
| R3 | Sizing races when the grid is measured while hidden | A synchronous `fitGrid` after `showScreen`. Writes are skipped at 0 × 0 |
| R4 | A ResizeObserver loop | `.grid-area` never sized by its content, plus integer hysteresis. The tests listen for the loop error |
| R5 | A won daily at 10×10 on 375 × 553 shrinks the tiles to 27 px | P9 considers putting the won state in the status strip |
| R6 | An iOS home-screen app has its own cookie jar | The player becomes a new player, finds their nickname taken (409) and gets a second daily game. The owner decides before P17: document it, use `display: browser`, or skip P17 |
| R7 | Emulation can't test the insets, iOS bars, the keyboard or a real finger | Real-device check (P16) as a gate, not optional |
| R8 | Go-live (T30) during phase 1 ships a half-finished phone layout | Reach checkpoint H before T30, or accept it. Desktop stays correct either way |
| R9 | Multiplayer on phones: percentages on 29-px tiles, EventSource paused when the screen locks | Noted in multiplayer.md §8 and §10 |

## 11. Open questions (owner)

1. ~~**End button**~~ **Decided (owner, 2026-10-13): a confirm dialog on phones only** (§6.3). Desktop keeps today's End, so the five e2e tests that click End (test_flows.py:118, 193, 202, 225, 257) stay as they are.
2. ~~**10×10 on a 360-px phone**~~ **Decided (owner, 2026-10-13): keep the full 10×10**, with no cap and no zoom toggle. The checkpoint H device test still runs. If it shows too many mis-taps, these fallbacks keep the grid:
   - gutter 8 and head 14 (pitch 33 at 360)
   - only if that isn't enough: a "large grid" toggle with 2-D scrolling (a new decision for the owner)
3. ~~**Remove-last button**~~ **Decided (owner, 2026-10-13): yes.** ⌫ on phones, the Backspace key everywhere (§6.3). P9 designs the button.
4. ~~**Focus ring**~~ **Decided (owner, 2026-10-13): the frame colour on every viewport** (§7).
5. ~~**Short desktop windows**~~ **Decided (owner, 2026-10-13): the compact layout**, the same side-by-side layout as landscape phones (§4).
