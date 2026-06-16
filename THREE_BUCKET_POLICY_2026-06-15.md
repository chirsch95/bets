# Three-Bucket Betting Policy — Design

**Date:** 2026-06-15 · **Status:** design approved (two key calls locked), implementation pending · **Basis:** `finding_betting_drift` (146-bet ledger split at the 6/06 tightening)

## Why

The post-6/06 tightening chased "the one precise bet" and the result was a ~3× volume collapse (3.6 → 1.2 bets/day) with the fun gone — both Path-C goals lost. The ledger says the precision was aimed at the wrong lever: the cash grind netted **−2% over 99 bets**, while **all** realized profit came from the **30% boost (+35%)** and **free credits (+$116)**. The fix isn't to loosen the cash bar back to where it bled (the 0.065–0.10 band hit 36–47%, below even the ~51% boosted breakeven) — it's to **route volume and fun to where losing doesn't cost the bankroll**, deploy the boost lever daily, and measure each lane separately.

## The buckets

| | **1. EDGE (cash conviction)** | **2. BOOST (daily lever)** | **3. FUN** |
|---|---|---|---|
| Purpose | Honest +EV core; scale/learning bucket | Deploy the daily boost token — the +35% lever | Volume + fun + zero/low-risk upside |
| Leg rule | v2 edge **[0.10, 0.15]**, ≥2 legs, line ≥3.0 | edge **[0.08, 0.15]** *(floor lowered — see Decision 2)*, ranked by boosted EV | free: **drop cap & floor** (value = p×payout); fun-budget: big-payout longshots |
| Phantom cap ≥0.15 | stays | stays | dropped (house money / accepted −EV) |
| Money | bankroll, 1–2u ($5–10) | bankroll + boost token, 2u | free credits ($0) **+ ~$15/wk fun budget** *(Decision 1)* |
| Frequency | when an in-band pair exists | every day a boost token + ≥0.08 card exist | free: whenever free entries exist; fun: within weekly budget |
| Tagged as | `stake_reason=focus` | `stake_reason=boost` | `free_entry=true` / `stake_reason=fun` |

## Decisions locked (2026-06-15)

1. **Fun bucket = free credits + a small real-money fun budget (~$15/wk).** Free entries stay unbounded (house money, all caps dropped). On top, a fixed weekly entertainment allowance — *separate from the $300 disciplined bankroll* — funds sub-bar/longshot lottery tickets, **explicitly accepted as −EV entertainment** and **walled off from edge measurement** (excluded from ROI/CI/edge-band reports). This restores daily "watch it hit" action honestly, without pretending the legs are +EV.

2. **Real-money boost floor lowered to 0.08.** The boost bucket bets [0.08, 0.15] when boosted. Honest note: [0.08, 0.10) legs hit ~43% vs the ~51% boosted breakeven, so this slice is *marginally −EV* — a deliberate volume/fun trade on real money, capped at 2u. The [0.10, 0.15] core (62–67%) remains the profitable heart; the phantom cap at 0.15 still holds.

## Cross-bucket invariants

- **Phantom-edge cap (≥0.15) stays in the EDGE and BOOST buckets** — the 10 tagged ud_lab bets that lived ≥0.15 hit 28%/leg, −73%. Only the house-money/accepted-−EV FUN lane drops it.
- **Bet after lineups** (full-board re-import → Save all → place once) — all buckets.
- **Line ≥ 3.0** (reliever/opener gate) — all buckets.
- **Bankroll separation:** only EDGE + BOOST draw the $300. The fun budget is its own lane with its own weekly cap.

## Implementation surface (twin-synced — change both suggesters together)

- **`bets/wagers.py`** — add `"fun"` to the `stake_reason` enum (source field already cleaned, commit `b00b9f4`).
- **`bets/web.py` (JS) + `bets/ud_parlay.py` (Python) twins** — boost-pool floor 0.08 (Watch tier becomes bettable *when boosted*); emit three labeled card sets (💵 Cash/Boost · 🎯 Boost target · 🎁 Free-credit longshot). Keep `udVerdict`/`ud_verdict` in sync.
- **Boost allocator** — widen pool to [0.08, 0.15]; **verify the boost mechanics** (docs conflict: base+0.5 → 3.5× vs modeled UD_BOOST ×1.3 → 3.9×) against a real boosted UD entry before trusting boosted-EV ranking. Decision 2 holds either way (43% < both 53.5% and 51%).
- **`bets/bet_record.py`** — per-bucket (by `stake_reason`) split; **exclude `fun` from ROI/CI/edge-band** measurement; add a weekly fun-budget tally.
- **UI (`web.py`)** — three labeled cards + a weekly fun-budget counter ($X / $15 used); tap sets the right `stake_reason`/`free_entry` so each lane self-measures.
- **Deploy** — regen `index.html` + Flask restart on the Air; both twins shipped together.

## Measurement loop (Path-C honesty)

The source data is now clean, so each lane self-measures going forward. Review after enough bets per lane: is BOOST still +? is EDGE still ~0 (and is the 0.08 slice dragging it)? Fun stays walled off — it never touches the "do we have an edge" judgment. Outcome ROI still can't *confirm* an edge (`finding_roi_not_significant`); this policy deploys positive-estimate/low-downside levers and restores fun where it's free, then watches the per-lane numbers.
