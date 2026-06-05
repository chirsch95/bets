# Model Check — 2026-06-04

**Data:** 850 graded lines / 35 of 40 required slate days (2026-04-30 → 06-03).
**Source:** `python -m bets.analyze` (all tables reproduce from it). Same
sportsbook-graded, selection-optimistic caveats as every harness run — this is
model-vs-sharp-line, NOT the UD market actually bet.

## Headline: the v0 flip case has collapsed

On 5/24 (PROJECT_REPORT §2.6), three of the four pre-registered flip criteria
passed and only the 40-day gate held the v2→v0 flip. As of tonight:

| Criterion | 2026-05-24 | 2026-06-04 (35/40 days) |
|---|---|---|
| (1) ≥40 slate days | 24/40 pending | 35/40 pending |
| (2) v0 RMSE < v2 RMSE | PASS | PASS (2.368 vs 2.571, DM t=+2.37) |
| (3) v0 ROI > v2 ROI @edge≥0.06 | PASS | **FAIL — v2 leads +0.477 vs +0.390** |
| (4) v0 leads every ≥20-bet month | PASS | **FAIL — v2 leads May (+0.466 vs +0.409) and June (+0.708 vs +0.219, n=17/21 — tiny)** |

Bet-selection ROI head-to-head (the money metric):

| min edge | v2 n | v2 ROI | v0 n | v0 ROI |
|---|---|---|---|---|
| ≥0.00 | 346 | **+0.401** | 417 | +0.287 |
| ≥0.02 | 304 | **+0.413** | 361 | +0.324 |
| ≥0.04 | 266 | **+0.434** | 321 | +0.338 |
| ≥0.06 | 238 | **+0.477** | 284 | +0.390 |
| ≥0.10 | 179 | +0.471 | 198 | +0.489 (≈tied) |

v2's betting ROI recovered across the board as the season matured. On 5/24 v0
led the high-conviction end +0.533 vs +0.433; that gap is now a virtual tie,
and v2 leads everywhere below it. v0 remains the better *point estimator*
(criterion 2 still passes) — but the flip rule keys on the money metric, and
the money now says v2.

**This is the pre-registered wait doing its job.** The documented rationale —
"v2's matchup/park/lineup thesis should be weakest early-season, so 'v0 wins
in spring' is the expected look even if v2 is the better full-season model" —
is what the data now shows. A 5/24 flip would have demoted v2 right as its
adjustment layers started earning their keep.

**Expected June 7 verdict: HOLD — no flip, v2 stays.** Criteria 3 and 4 both
fail. The harness auto-evaluates; nothing to do manually. (This also further
deprecates the form-divergence hybrid — see
`FORM_DIVERGENCE_HYBRID_BRIEF_2026-05-24.md`, already NO-GO.)

## ML shadow: still no promotion

- Diebold-Mariano t = **+0.50** (bar ≥2.0) — FAIL
- Hit-rate gap at |edge|≥0.05: ML 45.0% vs v2 58.4% = **−13.4pp** (bar ±2pp) — FAIL
- Verdict: **HOLD, ML stays a shadow.**
- Persistent bright spot unchanged: ML beats v2 on form-change starts
  (cooling RMSE 3.62 vs 4.66; heating 3.44 vs 4.42). Already investigated —
  routing hurts betting ROI; no action.

## Capture-system status (shipped tonight, `ae582ff`)

- 8 suggested-parlay snapshots journaled on day one
  (`data/ud_parlay_snaps_2026-06-04.jsonl`, Air)
- First fully-provenanced bet recorded (source tag + per-leg UD price stamp)
- By-source / by-priced-status splits become meaningful after ~1–2 weeks of
  tagged bets — then `bet_record.py` grows the splits (see UD_LAB.md open items)

## Standing discipline

Nothing here changes stake size, the bet bar, or bet placement. June bucket is
tiny; backtests are selection-optimistic; the UD-aware read is a separate
question that the new capture system starts answering in ~2 weeks.
