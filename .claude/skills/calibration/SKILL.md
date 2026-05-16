---
name: calibration
description: Use when the user invokes /calibration or asks to "check the model", "run the calibration check", "compare v2 vs ML", "is ML beating v2 yet", or "is anything ready to promote". Runs the standard pitcher-K model comparison on all settled data since the relevant shadow window started and applies the Path-C two-part promotion bar (Diebold-Mariano t ≥ 2.0 on squared errors AND hit rate within ±2pp at |edge| ≥ 5%/10%).
---

# /calibration

Run the standard model performance recheck for the ~/bets pitcher K projections. Compare every shadow variant (ML, calibrated p_over, parlay disjoint rule, focus+gap shadow band) against its incumbent, applying the project's pre-committed promotion rules.

## What to run

Three core checks, in this order:

### 1. Model comparison + promotion verdict (v2 vs ML)
```
python3 -m bets.analyze
```
The `promotion_verdict()` section at the bottom applies the **revised 2026-05-16 bar** (the old ≥0.1 K RMSE bar was deprecated because at the available sample size it would have triggered false promotions on noise). Both checks must pass:

- **(1) Statistical:** Diebold-Mariano paired t-test on per-outing squared errors yields **t ≥ 2.0** (≈ p < 0.05). The bar adapts to sample size — small wins become detectable only with big samples, which is the right behavior.
- **(2) Operational sanity:** at each operational edge threshold (|edge| ≥ 5% and ≥ 10%), ML's simulated hit rate is within **±2 percentage points** of v2's. Catches the failure mode where ML wins on RMSE but loses on the marginal calls that flip an over/under decision.

Verdict actions:
- **Both pass → PROMOTE** ML to primary.
- **Only stat passes → HOLD.** ML likely wins on easy projections, loses on marginal calls. Consider the form-divergence hybrid (`project_ml_form_divergence.md`).
- **Only sanity passes → HOLD.** Difference is at noise level; needs more data.
- **Neither passes → HOLD.** Keep ML as shadow.

**Incumbent stability:** Any v2 modification (config tune, factor add, exp_bf cap) resets the shadow clock. If v2 has changed since the last comparison, the verdict is provisional until ~30+ new settled outings accumulate.

**Cadence:** Monthly, not weekly. Short-term variance creates false patterns. Realistic verdict timeline is late summer / early fall 2026.

Inspect the `slice_table()` "recent vs season form" slice for the form-divergence hybrid hypothesis — preliminary 2026-05-07 data suggested ML wins on heating/cooling pitchers, v2 wins on stable. Re-check at each monthly run.

### 2. Probability calibration check
```
python3 -m bets.calibration
```
This refits the Platt scaler and reports calibration quality. From blind-spot #1 (resolved 2026-05-11): all raw models are overconfident at the high end (raw 80% → actual 65%). Brier scores were all within rounding of constant-50%.

Apply the promotion rule:
- **Stay on raw v2** until calibrator beats raw v2 in OOS Brier on the active sample.
- **Isotonic upgrade** considered at ~500+ sample size (~30 more days from 2026-05-11 baseline, so roughly 2026-06-10).

### 3. Parlay-suggester PnL by band + rule
Compare the production focus-band suggester vs the focus+gap shadow band:
```
python3 -c "
import csv, glob
for label, pattern in [('production', 'output/parlay_suggestions_2026-*_settled.csv'),
                       ('shadow (focus+gap)', 'output/parlay_suggestions_2026-*_shadow_settled.csv')]:
    files = [f for f in sorted(glob.glob(pattern)) if '_shadow' in f or label == 'production']
    # ... aggregate parlay_hit + realized_pnl across files
"
```
Also re-check the disjoint-3-leg rule shipped 2026-05-15 (commit ea69ad2): the 3-leg section should now be filtered to exclude any pitcher in the top-1 2-leg card. Compare 3-leg ROI against the historical 2026-05-01..05-14 sim baseline (5-8, +37.8u).

## Standard report format

After running the checks, produce a one-screen summary:

```
=== Model Comparison (since shadow window start) ===
Sample: n=<count> settled rows, dates <start> to <end>

v2  RMSE: <x>   (incumbent)
ML  RMSE: <y>   (delta: <z>)
  by form bucket:
    cooling  (n=<n>): v2 <x> / ML <y> / delta <z>
    stable   (n=<n>): v2 <x> / ML <y> / delta <z>
    heating  (n=<n>): v2 <x> / ML <y> / delta <z>

Promotion verdict: <KEEP V2 | HYBRID | PROMOTE ML>
Reason: <one line>

=== Probability Calibration ===
Brier — v2: <x>   v2_cal: <y>   ml: <z>   ml_cal: <w>   const-50%: 0.250
Verdict: <KEEP RAW | PROMOTE CAL>

=== Parlay Suggester (last 30 days) ===
2-leg production:  n=<n>  hit% <x>  ROI <y>%
3-leg production:  n=<n>  hit% <x>  ROI <y>%   (disjoint rule active since 2026-05-15)
Shadow focus+gap:  n=<n>  hit% <x>  ROI <y>%

Verdict: <STATUS QUO | CONSIDER PROMOTING SHADOW>
```

## Important

- **Verify dates before asserting.** Today's date may have moved past scheduled checkpoints — e.g., the disjoint 3-leg re-check was scheduled for 2026-05-29, ML phase 1 for 2026-05-24.
- **Never promote without OOS evidence.** Training-time holdout doesn't count. Use everything settled *after* the variant's deploy date.
- **n matters more than effect size.** A 0.15-K-RMSE win on n=30 is noise. The same delta on n=200 is signal.
- **Don't add features speculatively.** If both variants underperform constant-50%, the answer is more data or better-existing-feature tuning, not v3+. (See `feedback_model_discipline.md`.)
- **Report the negative result honestly.** "Keep v2" is a valid outcome and shouldn't be hedged.
- This skill is *advisory*, not action-taking. Surface the verdict, let the user decide on promotion. If they approve, the promotion itself is a separate code change (and falls under the shadow-rollout norm: shadow → multi-week OOS → promote).
