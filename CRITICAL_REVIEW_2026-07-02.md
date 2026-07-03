# Critical Review: Can 2-Leg UD Pitcher-K Parlays Make Reasonable Returns?

**Date:** 2026-07-02 · **Data:** 137 paid settled bets (May 1 – Jun 23), 1,418 graded starts across 54 slates, 111 settled suggester cards, 27 CLV-matched legs, 71 bet-time card snapshots. Canonical data pulled from the Air.

## Verdict

**A market-beating edge is not demonstrated. Reasonable returns are possible, but only through promotional economics (the daily 30% boost, multiplier-rich cards, free entries) — realistically ~$75–150/month at current stakes, with high variance. Unboosted standard-3× cash entries are approximately breakeven-to-negative and should not be played.**

## Evidence

### 1. Realized record (chad, paid = non-free, non-fun)
| Slice | n | ROI |
|---|---|---|
| All paid | 137 | **−0.7%** (95% bootstrap CI −29% → +30%) |
| Paid & boosted | 34 | **+16.4%** |
| Paid & unboosted | 103 | **−7.4%** |
| Paid 2-leg | 111 | +7.1% |
| Paid 3-leg | 24 | −49.9% |
| May | 100 | +13.4% |
| June | 35 | −46.3% |

The CI still spans ±30pp: two months of betting cannot statistically confirm or deny an edge. The boosted/unboosted split (+16% vs −7%) reaffirms `finding_boost_economics` on fresh data.

### 2. Per-leg hit rate in the core band [0.10, 0.15)
- Ledger legs: 38/63 = **60.3%** · Slate-wide (all pitchers, model side): 41/72 = **56.9%** · Pooled ≈ **58.5%**
- Significantly above coin-flip (z=1.98, p=.024) but **statistically indistinguishable from the 3.0× breakeven of 57.7%** (p=.42).
- Breakevens: 3.0× → 57.7%/leg · median confirmed card 3.81× → 51.2% · boosted ~4.96× → **44.9%**.

So: at standard 3× the observed hit rate has no provable margin. At boosted/multiplier payouts, even a pessimistic 50–54%/leg estimate clears breakeven comfortably. **The payout structure, not the model, is the profit source.**

### 3. Model probabilities are over-confident, worse in June
Suggester leg calibration (predicted p_hit vs actual):
- May: pred .607 → actual .524 (n=185) · June: pred .632 → actual .375 (n=32)
- **Overs: pred .597 → actual .449 (n=118)** · Unders: pred .627 → actual .566 (n=99)
- Rank-1 cards: claimed combined prob .401 → realized .300; claimed EV +16.5% on bet-time cards is not trustworthy.

The Over-side failure is the v2 over-projection bias (+0.28K May → +0.33K June). v2bc shadow fixes bias (−0.07K vs +0.33K, paired n=362) with RMSE a wash (2.311 vs 2.321) — it misses the ≥0.1K RMSE promotion bar but is the right fix for *betting probabilities*, where bias is what poisons Over edges.

### 4. CLV — the sharpest edge test — shows nothing
Of 27 same-line matched legs, 22 had zero movement bet→close (betting post-lineup ≈ betting at close), and 4 of the 5 that moved went against. No evidence of beating the close; no evidence the model knows something the market doesn't.

### 5. Suggester end-to-end
Rank-1 2-leg cards, 40 settled: 30.0% hit, ROI at UD payout **+2.5%** unboosted, **+7.0%** boosted. Noise-level positive, driven entirely by payout structure.

## What "reasonable returns" looks like
- **Boosted card (≤1/day, $10):** at realistic 52–55%/leg on ~4.5–5× boosted-multiplier cards, EV ≈ +15–35%/ticket → ~$2–4/day. Qualifying core-band card exists ~70% of days (44/63).
- **Free entries:** returned $134 over 2 months (~$65/mo).
- **Total realistic expectation: ~$75–150/month** on a $300 bankroll. Strong in relative terms; not an income in absolute terms — and capped by UD's one-boost limit, so it cannot scale. Variance is brutal: 30% ticket hit rate means 5–10 ticket losing streaks are routine (June was −46% on 35 bets without the model being meaningfully worse).

## Recommendations
1. **Hard rule: no unboosted standard-3× cash entries.** Boosted and/or multiplier-rich (≥~3.5×) cards only. This formalizes what the data has said twice now.
2. **Promote v2bc for edge/probability computation** (or refit calibration on it) before trusting suggester EVs again — the Over-side gap is the single biggest fixable defect. Verdict checkpoint ~7/04 is due.
3. **Do not scale stakes.** The pre-registered bars (significant ROI, positive CLV) remain unmet; nothing in this review triggers the bump checklist.
4. Note: no bets since 06-23. If that's a deliberate pause, fine; if it's a June-tilt reaction, the data says June was mostly variance on a thin unboosted margin — the boosted lane remained sound.
