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
3. **Re-check on the UD Lab:** the per-pitcher verdict (Bet OVER/UNDER) is
   computed against UD's *actual* price. If UD's numbers moved during the day,
   update them; if the verdict still reads Bet, the edge survived the move.
   (The Lean tier was removed 2026-06-06 — it sat below the bet bar.)
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
  board). Columns: **Opp** (vs/@ + abbreviated team), **Time** (first pitch CT
  + countdown → live inning/K → Final; same cell as the Pitchers tab, ticks
  every minute, row dims once first pitch passes), Proj, Bk ln, Live
  (sportsbook verdict), **UD ln** (editable), **UD mult** (Hi/Lo inputs),
  Model P, **Mkt P**, **Edge**, **UD verdict**.
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
- **Suggested parlays:** 2- & 3-leg, EV-ranked, per-section pitcher cap (one
  leg per game, games within 3 hrs). Payout = base × ∏(chosen-side
  multipliers); **boosted base = standard + 0.5** (2-leg 3.5, 3-leg 6.5 —
  confirmed from real entries).
  **△ unpriced caution** (replaced ⏰ place-early 2026-06-04): flags cards
  with legs UD hasn't priced (symmetric) — the edge rests on the model alone
  and early tracking has those legs underperforming priced ones (3/14 vs
  22/33, tiny n). The old ⏰ badge never fired: its premise (symmetric UD
  lines lagging the sharp consensus) occurs ~never — UD attached multipliers
  46/48 times its line diverged across 364 captured board rows.
- **Tap a parlay card** (lineups green) → pre-fills the Bets form AND carries
  a **tap-time card snapshot** (EV, win prob, payout, per-leg multipliers)
  into the saved bet — see Bet provenance below.
- **📊 My record** button → loads `GET /api/bet-record` into a panel.

## Bet provenance + bet-time capture (2026-06-04)

Every NEW bet records where it came from and the prices on screen when placed:

- **`source`** on the bet: `"ud_lab"` / `"pitchers"` (tapped that suggester's
  card) / `"manual"` (hand-entered). `null` = bet predates the feature.
  If a tapped card's legs are tweaked before saving, the source sticks and
  the snapshot gains `edited: true`; if the legs are fully replaced, the bet
  saves as `manual` and the stale snapshot is dropped.
- **`suggested_card`** on the bet: the tapped card exactly as displayed —
  the claim the suggester made at tap time, immune to later churn.
- **Per-leg UD board stamp** (`ud_line_at_bet`, `ud_hi_at_bet`,
  `ud_lo_at_bet`, `ud_captured_at`): the saved board entry at save time,
  stamped server-side in `wagers.add_bet` (mirrors the `slate_*` sportsbook
  stamp; idempotent, survives edits). hi/lo = 1.0 means symmetric. Legs with
  no board entry stay unstamped ("no UD data" ≠ "symmetric"). Caveat: this
  is the last-SAVED board — Chad's flow (re-import board → save → bet)
  keeps it honest; the tap-time card snapshot covers any residual gap.
- **Suggested-parlay journal** (`bets/ud_parlay.py`): the suggester's cards
  are appended to `data/ud_parlay_snaps_<date>.jsonl` (`{ts, trigger, two,
  three}`) on every pipeline **refresh** and every board **Save all** — NOT
  on single-cell edits. Kills the "suggestions churned, what did I actually
  see?" reconstruction problem; `grade_ud.py`-style analysis can grade the
  exact decision states.

## Storage (`data/`, gitignored, Air-canonical)

| File | What |
|---|---|
| `ud_lines_<date>.json` | Live entry / bet-time board. `{pid: {line, hi, lo}}`. |
| `ud_lines_<date>_morning.json` | Auto-frozen morning baseline (first save; never overwritten). |
| `ud_lines_close_<date>.json` | Closing board — **optional/deprecated** (closing-capture workflow dropped). |
| `ud_parlay_snaps_<date>.jsonl` | Append-only journal of the suggested parlays (one line per refresh / Save all). |

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
- **`ud_parlay.py`** — the ONE Python source of the UD suggester logic
  (udCompare/udVerdict/combos/selection ports) + the suggested-parlay
  journal writer. `grade_ud.py` imports from here. Same dual-implementation
  caveat as `parlay_suggest.py`: the JS in `web.py` implements the same
  logic — change both when tuning either.
- **`ud_vision.py`** — Claude vision (`claude-sonnet-4-6`, raw `requests`)
  extracts line + Hi/Lo per pitcher and matches names → slate `pitcher_id`.
  Requires `ANTHROPIC_API_KEY` (Air `.env`). Never auto-saves — review first.
- **`bet_record.py`** — computes the betting record + edge-band report; shared
  by the CLI and the endpoint so they never disagree. (By-source and
  by-priced-status splits become meaningful once tagged bets accumulate —
  add them here when there's data to show.)

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

- **Primary model edge = v2bc** (`cal_edge_v2bc`, promoted 2026-06-22;
  graded surfaces completed 2026-07-02) — falls back to `cal_edge_v2` for
  rows predating the v2bc shadow (pre-06-09), then raw edge (pre-05-11).
  `pickEdge()` (JS) / `_pick_edge()` (Python) are the single routing points.
- Focus edge band **[0.10, 0.15]** (floor raised from 0.065 on 2026-06-06);
  investigate ≥ 0.20; `MIN_LINE_FOR_FOCUS` 3.0.
- Bet criterion (Path C): per-leg calibrated edge in [0.10, 0.15], ≥ 2 legs
  (was [0.065, 0.15] until 2026-06-06).
- **UD verdict tiers:** edge in **[0.10, 0.15] → Bet**; **> 0.15 → ⚠ Investigate
  (NOT bettable, excluded from the parlay leg pool)** — the phantom-edge cap;
  **[0.08, 0.10) → Watch (NOT bettable, dashed grey tag, excluded from the
  leg pool)** — visibility-only tier added 2026-06-07 (see Decisions);
  below 0.08 → "—". The old Bet ≥ 0.05 / Lean ≥ 0.02 tiers were removed
  2026-06-06 (see Decisions).
- Shadow suggester floor stays **0.065** (`SHADOW_EDGE_MIN`) so the dropped
  0.065–0.10 band keeps accumulating graded evidence.
- UD payouts `{2:3, 3:6, 4:10, 5:20}`; boosted `{2:3.5, 3:6.5}` (4/5-leg boosted
  estimated as standard + 0.5, **unconfirmed** — verify if ever built).
- **Breakeven:** 2-leg @3× = **57.7%**/leg; 3-leg @6× = **55.0%**/leg.

## Decisions (and why)

- **One-bet-per-day boost hero (2026-07-02, $100 restart).** The
  2026-07-02 critical review (`CRITICAL_REVIEW_2026-07-02.md`) reconfirmed
  on 137 paid bets that unboosted standard-payout cash grades ≈ −7% while
  boosted bets ran +16% — the payout structure (boost + multipliers + free
  credits), not model-vs-market edge, is the profit source (CLV ≈ 0 on 27
  matched legs). Chad restarted with a $100 bankroll and a one-bet-per-day
  rule: **the boost-target card, $5, with the 30% boost — or no bet.** The
  UD Lab's boost banner became a tappable hero (`udlab-hero`): names the
  card, payout → boosted payout, win %, boosted EV, and the two no-bet
  states ("boost spent → pass" when not cash-eligible; "no card clears the
  bar → passing costs nothing"). Tapping the hero (or any card) prefills
  the Bets form with stake $5 (`BOOST_STAKE`) and pre-types "30%" in the
  boost field for the boost-target card. Cash/EDGE cards remain rendered
  (the lanes still self-measure) but the hero is the headline decision.
- **Three-bucket policy — boost floor 0.08 + FUN longshot bucket (2026-06-15).**
  Diagnosis (`finding_betting_drift`): post-6/06 tightening collapsed volume ~3×
  and killed the fun, while the ledger showed the cash grind netted ≈0 — ALL
  realized profit came from the boost (+35%) and free credits (+$116). Fix is
  three lanes, not one bar (`THREE_BUCKET_POLICY_2026-06-15.md`):
  - **EDGE (cash):** [0.10, 0.15], unchanged — the honest core, `stake_reason=focus`.
  - **BOOST:** suggester leg pool floor lowered **0.10 → 0.08** (`WATCH_MIN`).
    Legs tag `band=core` [0.10,0.15] (cash-eligible) vs `watch` [0.08,0.10)
    (boost/free only — ~43% hit < ~51% boosted breakeven, a deliberate
    volume/fun trade Chad chose). A card with any watch leg is boost/free-only
    regardless of EV sign (`cashEligible = ev>0 && !hasWatch`). 0.15 phantom cap
    still holds. `stake_reason=boost`.
  - **FUN:** a NEW separate pool (`udBuildFunLegs`/`ud_fun_combos`) that DROPS
    the 0.08 floor AND the 0.15 phantom cap (any model-favored leg, edge>0),
    ranked by expected payout (p×payout), not edge. Rendered as the third
    "🎁 Free-credit / fun-budget longshots" section. House money (free credits)
    or the ~$15/wk real-money fun budget (`stake_reason=fun`), walled off from
    the edge read. Journaled as `fun_two`/`fun_three` in the snapshot.
  Measurement loop: tapping a card auto-tags its `stake_reason`; `bet_record`
  splits ROI by bucket and reports the fun lane separately (excluded from paid
  ROI / CI / edge-bands). Also fixed a latent twin gap — `ud_parlay.py` had
  never modeled the boost (no `ev_boost`/`boost_target`); now at parity.
- **Watch tier [0.08, 0.10) — visibility, not bettability (2026-06-07).**
  One day after the floor raise, Chad asked to lower the bar to 0.08. A fresh
  band split over all 38 settled slates showed 0.08–0.10 hitting **43.1%
  directional / 38.7% overs-only** (UD breakeven ~58%) — the *weaker* half of
  the dropped band, with zero new data since the raise. Compromise chosen
  (over "lower anyway" and "keep as-is"): UD verdicts in [0.08, 0.10) read
  **"Watch OVER/UNDER"** — dashed grey tag, `bettable=False`, excluded from
  both parlay leg pools — so Chad sees what the bar is filtering without
  re-opening it. The floor itself only comes down per the standing rule:
  shadow band recovers above breakeven on real data, monthly review.
  Implemented in both twins (`web.py` udVerdict + `ud_parlay.py` ud_verdict,
  `WATCH_EDGE_MIN = 0.08`).
- **Bet-bar floor raised 0.065 → 0.10; UD Lean tier removed (2026-06-06).**
  Same session as the phantom-edge cap, at Chad's direction. The
  `grade_my_bets` edge-band report showed the 0.065–0.10 band hitting
  36–44% all-time (below UD breakeven ~57.7%) while 0.10–0.15 hit 62% —
  the only band clearing breakeven. The UD verdict's old Bet ≥ 0.05 /
  Lean ≥ 0.02 tiers and the LEG_EDGE_MIN 0.02 pool floor all sat *below*
  even the old bar, so they went too: the bettable band is now [0.10, 0.15]
  everywhere (sportsbook focus, UD verdict, both parlay pools). The shadow
  suggester keeps its 0.065 floor (`SHADOW_EDGE_MIN`) so the dropped band
  stays graded and the decision is reversible on data. Note: graded
  surfaces (Track Record, report card) reclassify history under the new
  band — past-period focus counts shrink accordingly (by design, per the
  graded-surface-parity rule).
- **Phantom-edge cap on UD verdicts + leg pool (2026-06-06).** The original
  udVerdict had no upper bound — any edge ≥ 0.05 vs UD's price read "Bet", and
  the leg pool ranked by *descending* edge, so the biggest model-vs-market
  disagreements dominated the suggested cards. The 2-of-24 losing streak
  (May 31 – Jun 6) traced to exactly this: post-UD-switch, 56% of bet legs had
  claimed edge > 0.15 (vs 25% before) and those hit **15/40 = 37%**, while the
  model's own accuracy (weekly RMSE/bias) was unchanged. Classic phantom-edge:
  when the model disagrees with the market by that much, the market usually
  knows something (scratch risk, bullpen game, news). Fix: udVerdict caps at
  the focus band's 0.15 ceiling (`> 0.15` → "⚠ Investigate", not bettable) and
  udBuildLegs excludes those legs — restoring the pre-committed Path C band
  [0.065, 0.15] that `parlay_suggest.py` always enforced on the sportsbook side.
- **Drop intraday closing screenshots / CLV.** UD CLV needs 2–3 daily
  screenshots and is methodologically messy (line moves, symmetric→priced,
  staggered locks, live-line poisoning). Burden not worth it. Replaced by the
  per-leg-hit-rate-by-edge-band signal (`grade_my_bets`) + the daily calibration
  check — both automatic, lower-variance than parlay ROI.
- **⏰ place-early → △ unpriced caution (2026-06-04).** The place-early badge
  never fired (0 qualifying legs in 364 board rows) because UD prices its
  divergent lines ~always; meanwhile unpriced legs were the worst performers.
  Chad picked "repurpose as caution" over "remove" — the by-priced-status
  tracking will confirm or kill the pattern.
- **Bet placement flow (2026-06-04):** wait for lineups (green) → **full
  board screenshot re-import** (not selective leg edits — mixing fresh and
  stale prices makes the suggester chase staleness) → Save all → take the
  top suggestion or pass, ONCE → place immediately (UD locks the multiplier
  at entry; later moves can't hurt a placed ticket). Pitcher-tab ideas get
  verified on the UD Lab before placing — never bet a price you didn't check.
- **Suggesters re-aimed at UD economics + 30% boost allocator (2026-06-09).**
  Both suggesters (sportsbook-side in `web.py`/`parlay_suggest.py`, UD-aware in
  the Lab) previously ranked by EV at *book* odds with *raw* `p_over` — raw
  legs rated >70% hit ~44–57%, and book-odds ranking favors longshots while UD
  pays flat. Graded at real UD payouts the old suggester ran −9% (2-leg) /
  −57% (3-leg); ledger agreed (paid 3-legs −48%, unboosted −8%, boosted +44%).
  Changes: hit probs from `cal_p_over_v2` (fallback raw pre-5/11), rank by
  `ev_ud` (= win prob at flat payout), keep cards only if `ev_ud_boost > 0`
  (UD_BOOST = 1.3 — Chad's recurring promo, ledger-confirmed ~1.3× multiplier;
  only the 30% tier is modeled), combo pool seeded by hitProb not |edge|.
  UI: 🎯 boost-target chip + banner names the one card for today's boost
  (boost scales payout, so the best boost card = best card, all modes);
  "boost/free only" label when cash EV ≤ 0; snapshot CSV gains `ud_payout`,
  `ev_ud`, `ev_ud_boost` (empty pre-6/09); `suggested_card` meta gains
  `ev_boost`/`ev_book`/`boost_target`. Free credits: same top card (house
  money — value = p × payout, same ordering).
- **Morning baseline auto-frozen** so intraday updates can't destroy it.
- **Outcome ROI can't confirm an edge** (#3): over ~95 parlays the CI spans
  zero. Don't let win/loss results trigger scaling — use per-leg + calibration.

## Findings to date (small samples — DO NOT act yet)

- **5/29 (first real read):** model's UD-aware picks went **9/19 (47%)** on the
  post-lineup lines — below UD breakeven. One slate.
- **`grade_my_bets` over 108 bets:** paid ROI **+19.8%** but 95% CI
  **[−16%, +57%]** (not distinguishable from zero); per-leg **58.5%** (≈breakeven).
- **Edge-band signal:** legs at **0.10–0.15 hit 67%** (n=52) but **0.065–0.10
  hit 47%** (n=30) → the bet bar's low end may be too low. Watch as samples grow;
  bring to the monthly bar review.
- **Multiplier-side analysis (2026-06-04, slates 5/29–6/03):** 77% of
  UD-suggested legs take the >1-multiplier side — and those hit **22/33 = 67%**
  (above their multiplier-lowered breakeven). The drag is **unpriced
  (symmetric) legs: 3/14 = 21%** combined — origin of the △ caution flag.
  Model Brier 0.253 vs UD's implied 0.275 on priced legs (model closer 65%).
  Reconstructed suggested cards went 5–14 but **+0.22u** (avg pay ~3.9×,
  realized ≈ modeled win prob); Chad's actual tickets same span went 3–21,
  −$39.10 — the gap between those two is what the provenance capture
  (source / suggested_card / ud_*_at_bet) now measures directly.
- **Model-side context:** the v0 flip case collapsed as the season matured —
  see `MODEL_CHECK_2026-06-04.md` (expected June 7 verdict: HOLD, v2 stays).

## Open items / future refinement

- **Line-fixed CLV** (hold the entry line fixed; infer the close's implied K
  distribution and evaluate P at the bet line) — not built; CLV de-prioritized.
- ~~Tune the bet bar: raise the floor from 0.065 toward 0.10~~ — **done
  2026-06-06** (with the phantom-edge cap; see Decisions). The monthly
  bar review continues via the shadow band + `grade_my_bets` edge report.
- **4/5-leg boosted bases** unconfirmed (suggester only builds 2/3-leg).
- ~~Per-leg UD price not stored in the ledger~~ — **done 2026-06-04** (the
  `ud_*_at_bet` leg stamps; see Bet provenance above).
- **By-source / by-priced-status splits in `bet_record.py`** once tagged bets
  accumulate (~2 weeks) — the direct answer to "are UD Lab suggestions worse
  than Pitcher-tab ones?" and "are unpriced legs really worse?".
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
