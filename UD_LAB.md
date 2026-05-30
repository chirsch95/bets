# UD Lab — Underdog-aware analysis & betting-record tooling

Living reference for the **UD Lab** dashboard tab and its supporting tools.
Keep this current as the feature evolves — it's the entry point for any future
session refining the Underdog workflow.

## Why it exists

The projection pipeline computes edge against the **sportsbook** no-vig line
(DK/FanDuel via The Odds API). But since 2026-05-25 all bets are on
**Underdog** pick'em — fixed 3×/6× multipliers on *UD's* lines, which are set
independently and are often softer/slower than the sharp consensus. So every
validated edge number was "model vs sportsbook," not "model vs the market we
actually bet." The UD Lab closes that gap: it recomputes the model against
UD's **actual line + Higher/Lower multipliers**, and it tracks how the bets we
place actually perform. (Origin: blind-spots #1/#2 — see
`BLIND_SPOTS_REVIEW_2026-05-28.md`.)

This is an **integration-test tool under Path C** (building is primary; bets
keep the model honest), but Chad *would scale real money on a confirmed edge*,
so the integrity of these measurements matters.

## The daily workflow (what Chad does)

1. **Morning:** open UD Lab → **📷 Import screenshot** of the UD board → review
   the blue-marked rows → **Save all UD lines**. The first save of the day
   **auto-freezes** the morning baseline (`ud_lines_<date>_morning.json`).
2. **Wait for confirmed lineups**, then **Re-run pipeline** (green Refresh on
   the Air). Lineups are "the biggest single accuracy bump" — a re-run flips
   each pitcher's `opp_k_source` from team-average to the real lineup and
   re-derives the edge — and it confirms the starter isn't scratched.
3. **Re-check on the UD Lab:** the per-pitcher verdict (Bet/Lean OVER/UNDER) is
   computed against UD's *actual* price. If UD's numbers moved during the day,
   update them; if the verdict still reads Bet/Lean, the edge survived the move.
   - Green/red **lineup dot** per row (green = opp lineup posted).
   - Suggested **parlay cards** get a green border when every leg's lineup is
     posted, red while any is still TBD (TBD chip on the pending leg).
4. **Bet** (after lineups). Bets are recorded on the **Bets tab**, which stores
   a full bet-time snapshot per leg (`slate_*` fields incl. `slate_edge`).
5. **📊 My record** button anytime → your results + where the bet bar should be.

**No intraday closing screenshots** — that workflow was dropped (see Decisions).
The morning import is the only manual capture, and it's part of the normal flow.

## The UD Lab tab (`bets/web.py`)

- **Comparison table**, sorted by sportsbook line high→low (mirrors the UD
  board). Columns: Proj, Bk ln, Live (sportsbook verdict), **UD ln** (editable),
  **UD mult** (Hi/Lo inputs), Model P, **Mkt P**, **Edge**, **UD verdict**.
  Each UD line prefills with the sportsbook line; edit the few that differ.
- **De-vig:** `udImpliedOver(hi, lo) = (1/hi) / (1/hi + 1/lo)` → UD's own implied
  P(over) when it prices the pick. Symmetric (1.00/1.00) → fall back to the
  **sharp consensus** at the UD line (the soft-line ★ targets). `Edge` = model's
  calibrated P − the price you actually get.
- **Reliever/opener gate:** UD line < `MIN_LINE_FOR_FOCUS` (3.0) → "RP — skip",
  greyed, excluded from parlays (model over-projects sub-floor arms as full
  starts → phantom edge).
- **Lineup indicators:** per-row green/red dot + parlay-card border, driven by
  `lineupPending(r)` (opp_lineup_json presence). Mirrors the sportsbook
  suggester's gating so you don't toggle to the Pitchers tab.
- **Suggested parlays:** 2- & 3-leg, EV-ranked, deduped (no pitcher twice; one
  leg per game). Payout = base × ∏(chosen-side multipliers); **boosted base =
  standard + 0.5** (2-leg 3.5, 3-leg 6.5 — confirmed from real entries).
  ⏰ **place-early** badge when EV ≥ 0.10 *and* the edge leans on a soft
  (symmetric, stale) UD line.
- **📊 My record** button → loads `GET /api/bet-record` into a panel.

## Storage (`data/`, gitignored, Air-canonical)

| File | What |
|---|---|
| `ud_lines_<date>.json` | Live entry / bet-time board. `{pid: {line, hi, lo}}`. |
| `ud_lines_<date>_morning.json` | Auto-frozen morning baseline (first save; never overwritten). |
| `ud_lines_close_<date>.json` | Closing board — **optional/deprecated** (closing-capture workflow dropped). |

These are hand-entered; the pipeline never overwrites them, and they don't pass
through the slate-pin overlay. `data/` lives only on the Air (not the laptop).

## Server endpoints (`bets/server.py`)

| Endpoint | Purpose |
|---|---|
| `GET /api/ud-lines?date=` | Load the saved board. |
| `POST /api/ud-line` | PATCH one pitcher's `{line?, hi?, lo?}`. |
| `POST /api/ud-lines` | Bulk "Save all". |
| `POST /api/ud-screenshot` | Vision import (`ud_vision`). 503 no key / 400 bad image / 502 API fail. |
| `GET /api/bet-record` | Login-gated betting record for the current user (`bet_record`). |

## Python modules (`bets/`)

- **`ud_lines.py`** — the slate-level pricing store + the **morning auto-freeze**
  (in `_write`: first non-empty save of the day copies to the `_morning` file).
- **`ud_vision.py`** — Claude vision (`claude-sonnet-4-6`, raw `requests`)
  extracts line + Hi/Lo per pitcher and matches names → slate `pitcher_id`.
  Requires `ANTHROPIC_API_KEY` (Air `.env`). Never auto-saves — review first.
- **`bet_record.py`** — computes the betting record + edge-band report; shared
  by the CLI and the endpoint so they never disagree.

## CLI tools (repo root; run with the Air's `.venv/bin/python`)

- **`grade_ud.py [date]`** — post-settle read: model UD-aware picks vs
  sportsbook focus picks, graded vs actuals, **morning vs post-lineup side by
  side**, "lineup post changed the call" diff, SB-vs-UD disagreements, parlay
  W/L, and CLV (if a closing board exists). Pre-settle = safe dry-run.
- **`grade_my_bets.py [user]`** — your actual betting record (= the "My record"
  button): overall + promo-stripped ROI with a bootstrap CI, per-leg hit rate
  vs UD breakeven, and per-leg hit rate **bucketed by the model's edge at bet
  time**. No new capture; sharpens as bets accumulate.
- **`capture_ud_close.py <shots…>`** — closing-board capture (**de-emphasized**).
  Started-game guard: skips any pitcher whose game began before the screenshot
  (`--as-of ISO`, else file mtime) so live in-game lines can't poison the data.

## Key constants / formulas

- Focus edge band **[0.065, 0.15]**; investigate ≥ 0.20; `MIN_LINE_FOR_FOCUS` 3.0.
- Bet criterion (Path C): per-leg calibrated edge in [0.065, 0.15], ≥ 2 legs.
- UD payouts `{2:3, 3:6, 4:10, 5:20}`; boosted `{2:3.5, 3:6.5}` (4/5-leg boosted
  estimated as standard + 0.5, **unconfirmed** — verify if ever built).
- **Breakeven:** 2-leg @3× = **57.7%**/leg; 3-leg @6× = **55.0%**/leg.

## Decisions (and why)

- **Drop intraday closing screenshots / CLV.** UD CLV needs 2–3 daily
  screenshots and is methodologically messy (line moves, symmetric→priced,
  staggered locks, live-line poisoning). Burden not worth it. Replaced by the
  per-leg-hit-rate-by-edge-band signal (`grade_my_bets`) + the daily calibration
  check — both automatic, lower-variance than parlay ROI.
- **Bet after lineups + a re-run** — not off the morning board.
- **Morning baseline auto-frozen** so intraday updates can't destroy it.
- **Outcome ROI can't confirm an edge** (#3): over ~95 parlays the CI spans
  zero. Don't let win/loss results trigger scaling — use per-leg + calibration.

## Findings to date (2026-05-29 → 30; small samples — DO NOT act yet)

- **5/29 (first real read):** model's UD-aware picks went **9/19 (47%)** on the
  post-lineup lines — below UD breakeven. One slate.
- **`grade_my_bets` over 108 bets:** paid ROI **+19.8%** but 95% CI
  **[−16%, +57%]** (not distinguishable from zero); per-leg **58.5%** (≈breakeven).
- **Edge-band signal:** legs at **0.10–0.15 hit 67%** (n=52) but **0.065–0.10
  hit 47%** (n=30) → the bet bar's low end may be too low. Watch as samples grow;
  bring to the monthly bar review.

## Open items / future refinement

- **Line-fixed CLV** (hold the entry line fixed; infer the close's implied K
  distribution and evaluate P at the bet line) — not built; CLV de-prioritized.
- **Tune the bet bar** from the edge bands as samples grow (monthly, per Path C).
  Candidate: raise the floor from 0.065 toward 0.10.
- **4/5-leg boosted bases** unconfirmed (suggester only builds 2/3-leg).
- **Per-leg UD price not stored** in the ledger (only parlay odds) — add it if
  UD-vs-UD CLV is ever wanted.
- **Phase 2** (prior handoff): switch the live suggester to a UD objective,
  Flex-vs-Power analysis, leg-correlation adjustment (same-game / umpire /
  weather).

## How to refine in future sessions

- **`bets/web.py` is the source of truth for the dashboard** — edit it, never
  `output/index.html`; regenerate after each change (`python -m bets.web`).
- **Deploy:** commit + push; the Air git-pulls within 60s. For **UI** changes,
  regenerate `index.html` on the Air (`.venv/bin/python -m bets.web`) so it's
  live immediately (the gitpull cron does *not* regen). For **server modules**
  (`server.py`, `ud_lines.py`, `bet_record.py`, `ud_vision.py`), restart Flask:
  `launchctl kickstart -k gui/$(id -u)/com.bets.flask`.
- Memory pointer: `[[project-ud-read-workflow]]` in the agent memory index.
