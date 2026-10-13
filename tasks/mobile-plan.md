# TRUR – Smartphone plan (proposal)

Status: **proposal, nothing built yet.** Today the game is built for desktop only: `docs/plan.md` says "Desktop only (fixed 640×640)". `.app` is a fixed 640×640 px box, and `game.js` sizes tiles from hard-coded `GRID_WIDTH = 410` / `GRID_HEIGHT = 470`. On every phone the page scrolls sideways and the found list (190 px side panel) takes most of the screen width.

Goal: a portrait-first phone layout that keeps the approved "Olive board" look. The desktop 640×640 layout stays pixel-identical, and the existing e2e test at 1000×800 keeps passing.

---

## 1. Target devices (CSS px, portrait)

| Device | Viewport | Ratio | DPR | Notes |
|---|---|---|---|---|
| iPhone SE (2nd/3rd gen) | 375 × 667 | 16:9 | 2 | smallest supported iPhone; Safari leaves about **375 × 553** visible |
| iPhone 16e | 390 × 844 | 19.5:9 | 3 | notch |
| iPhone 16 / 17 / 17 Pro | 393–402 × 852–874 | 19.5:9 | 3 | Dynamic Island: top inset ≈ 59–62, home bar 34 |
| iPhone Air | 420 × 912 | 19.5:9 | 3 | |
| iPhone 16 Plus / 17 Pro Max | 430–440 × 932–956 | 19.5:9 | 3 | widest iPhone |
| Galaxy S25 / S25+ | 360–384 × 780–832 | 19.5:9 | 3 / 2.8 | **narrowest current Android: 360** |
| Pixel 10 | 412 × 923 | 20:9 | 2.625 | |
| Pixel 10 Pro / Pro XL | 410–448 × 914–997 | 20:9 | 3.1 / 3 | |
| Landscape (any of the above) | ~667–956 × 360–440 | | | must not break; see §4.7 |

**Design envelope:** width **360–448**, usable height **≥ 550** (the smallest case is iPhone SE with Safari's bars showing). Rules that follow from it:
- Never use a fixed px width wider than 360 − 2 × 12 (gutter) = **336**.
- Use `100dvh` (with `100vh` as the fallback), never a fixed height: the browser bars on iOS Safari and Chrome Android change the visible height while the page is open.
- Respect `env(safe-area-inset-*)` for the notch, the Dynamic Island and the home indicator.

## 2. Grid math (the hard constraint)

Source words have 5–10 letters, so the grid ranges from 5×5 to **10×10**. With a 12 px gutter, a 16 px head column and 3 px gaps:

| Width | 10 cols | 8 cols | 6 cols |
|---|---|---|---|
| 360 (Galaxy S25) | 29 px | 37 px | 48 px |
| 375 (SE) | 30 px | 39 px | 48 px |
| 402 (iPhone 17) | 33 px | 42 px | 48 px |
| 412 (Pixel 10) | 34 px | 43 px | 48 px |
| 440 (Pro Max) | 37 px | 46 px | 48 px |

A 10-column grid falls below the 44 pt (Apple) / 48 dp (Material) touch target sizes. Mitigations:
1. **Every point of the grid selects a cell.** Map the pointer position to the nearest cell, so gaps count too. The hit pitch becomes tile + gap = 32–40 px.
2. Tile font: `max(12px, tile × .35)`, with `letter-spacing: 0` below 36 px.
3. Clear visual feedback on tap: the selected state is already strong (orange). Optionally add `navigator.vibrate(10)` on Android; iOS ignores it.
4. Raise `MAX_TILE` to 56 on phones so that small grids (5–7 columns) use the room they have.

Vertical check on the worst case, **375 × 553, 10×10**:

| Part | Height |
|---|---|
| Header | 48 |
| Status strip | 32 |
| Grid (16 + 10 × 30 + 9 × 3), plus padding | 343 + 16 |
| Action area (preview row + buttons) | 100 |
| **Total** | **539 ✓** |

It fits, but only if the found list is **not** inline. Tile size stays `min(width-fit, height-fit)`, as `buildGrid` already computes it.

## 3. Approach

- **One codebase, CSS-first.** Add a single breakpoint:
  `@media (max-width: 639px), (max-height: 639px) { … }`.
  Above it, nothing changes. Below it, `.app` becomes `width: 100%; height: 100dvh`.
- **Measure, don't hard-code.** Replace `GRID_WIDTH`, `GRID_HEIGHT` and `WON_HEIGHT` in `game.js` with the measured size of `.grid-area`, read through a `ResizeObserver`. Re-run the tile sizing whenever that size changes: rotation, URL bar collapsing, the won banner appearing, the keyboard. On desktop the measured box is still 410 × 470, so the tiles come out the same.
- **CSP-safe.** Keep the existing rule: JS sets only custom properties and `data-*` attributes. `style.setProperty` through the CSSOM is allowed under the current CSP.
- **No framework, no build step** (as today).

## 4. Work items

### M1: Viewport and page shell (S)
- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`. Do **not** disable zoom, for accessibility.
- `<meta name="theme-color">` for light and dark (using `media=`), so the browser bars match the page.
- `body`: `min-height: 100dvh`, with no centring on phones. `.app`: full bleed, with padding of `env(safe-area-inset-*)`.
- `touch-action: manipulation` on `html`. This removes the double-tap-zoom delay while keeping pinch zoom.
- `-webkit-tap-highlight-color: transparent`, and `user-select: none` on the grid and tiles.
- `overscroll-behavior: none` on `.app`, so that a scroll inside a list doesn't trigger pull-to-refresh.

### M2: Header and status strip (S)
- Row 1 (48 px): compact logo (no wordmark below 380 px), the **Free | Daily** switch and the theme toggle (44 × 44 hit area).
- Row 2 (32 px, game screen only): **Score · Tries · Found n/m**. "Found n/m" is a button that opens the found sheet (M4).
- The answers and boards screens keep "Back to game" in row 1, with the title moving to row 2.

### M3: Game screen layout (M)
- From top to bottom: optional won banner (compact, one line, dismissible), grid (`flex: 1`, centred), action area.
- Action area in the thumb zone, with padding `env(safe-area-inset-bottom)`:
  - row A: chips and the word preview; scrolls sideways if it gets too long
  - row B: **Clear** (secondary) and **Submit** (primary, wide), both ≥ 48 px high
  - **End** moves out of the main row (into a small "⋯" menu, or behind a confirm step). One wrong tap shouldn't end a game.
- Change the preview text from "Click tiles…" to "Tap tiles…", using `(pointer: coarse)` to pick the wording.
- `game.js`: measure-based `buildGrid()` (§3) and pointer-to-cell hit mapping (§2.1). Keep `click` as the event, so keyboard use and the e2e tests still work.

### M4: Found words as a bottom sheet (M)
- Below the breakpoint, `.found` becomes a bottom sheet: a `<dialog>`, or a panel with `aria-modal`. It opens from "Found n/m", is about 60% of the height, scrolls, and closes on its close button, on Esc and on a tap outside.
- After an accepted word, briefly highlight the "Found n/m" counter (+points), so that players get feedback without opening the sheet.
- The daily answers note and the "Past answers" link move into the sheet's footer.

### M5: Toast (S)
- `width: min(380px, 100% − 24px)`, docked just above the action area (bottom + safe-area inset), so it never covers the grid's last row on short screens.

### M6: Game over, answers, leaderboards, nickname (M)
- **Game over:** a single scrolling column. Score block, then "Your words" at full width, then the missed-words grid with **2** columns instead of 3. The action bar stays fixed at the bottom.
- **Past answers:** the list has **2** columns instead of 4. Remove `min-width: 210px`, wrap the "you" badge onto its own line, and let the legend wrap.
- **Leaderboards:** three equal-width tabs (`flex: 1`), with shorter labels below 400 px ("Free", "Daily", "Today"). The hint goes on its own line, and the table scrolls with its header kept visible.
- **Nickname:** form and privacy text at `width: 100%` (max 360/420), the button below the input when < 380 px, and the logo scaled to 48 px tiles. The input stays ≥ 16 px font, so iOS doesn't zoom in on focus. Add `enterkeyhint="go"` and `autocapitalize="off"`.

### M7: Landscape on phones (S)
- At `(max-height: 500px) and (orientation: landscape)`: reuse the desktop side-by-side idea. The grid on the left gets the full height, and the found list and actions go in the right column. Keep the left and right safe-area insets (the notch side).

### M8: Home-screen install, optional (S)
- `manifest.webmanifest` (name, icons, `display: standalone`, theme colours) and a PNG `apple-touch-icon` (180 × 180), because iOS ignores SVG touch icons.
- No service worker, so no offline mode: the game needs the API anyway.
- Make sure the CSP and `trur/security.py` serve the manifest (`default-src 'self'` already covers it).

### M9: Tests and docs (M)
- Add a mobile e2e test that runs for each device in `tests/e2e/`, using Playwright `is_mobile=True` and `has_touch=True` with the viewports from §1: 360×780, 375×667, 402×874, 412×923, 440×956, and landscape 874×402.
- Assertions on every screen:
  - no sideways scroll (`scrollWidth <= clientWidth`)
  - every tile is inside the grid area and the viewport
  - tile pitch ≥ 32 px at 10 columns
  - Submit is visible without scrolling
  - the toast is inside the viewport and doesn't overlap the action bar
  - the found sheet opens and closes
- Play with `page.tap()` rather than `click()`.
- Keep the existing desktop test (`test_flows.py:138`), unchanged: it proves the 640×640 layout didn't move.
- Optional helper `scripts/mobile_shots.sh`: a bash wrapper with full `--help` / usage, `--device`, `--screen`, `--theme` and `--out` arguments. It runs Playwright and saves screenshots for every device and screen in light and dark, for design review.
- Update `docs/plan.md` ("Devices: Desktop only") and the screen specs.
- Manual check on real devices: iOS Safari (SE-size and a Pro Max) and Chrome on Android (Pixel), in light and dark mode, with the browser bars both collapsed and expanded.

## 5. Order and size

| Phase | Items | Size | Result |
|---|---|---|---|
| 1 | M1, M2, M3, M5 | M | The game is playable on every phone in §1 |
| 2 | M4, M6 | M | All screens work on phones |
| 3 | M7, M9 | M | Landscape works; covered by tests |
| 4 | M8 | S | Can be added to the home screen |

## 6. Risks and open questions
- **10×10 tap accuracy at 360 px:** test it on a real Galaxy S25 early. If it's too fiddly, a fallback is a pinch-free "zoom grid" toggle, or capping phone games at 8-letter words (a game-design change, so the owner decides).
- **The `100dvh` jump on iOS** when the URL bar collapses: the ResizeObserver re-sizes the tiles. Check that this doesn't cause a visible jitter. If it does, size the tiles from `svh` instead.
- **End button placement:** the "⋯" menu versus a confirm dialog is a design choice.
- **Claude Design:** the approved design has only the 640×640 screens. The phone screens should be designed or approved there before phase 2.
