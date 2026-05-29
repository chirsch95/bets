# Blind-Spots Review — 2026-05-28

**Context:** Chad asked Claude to review the ~/bets project and surface questions he may not have thought of yet. These deliberately avoid what the existing docs already cover (v0 flip timing, Kelly sizing, calibration tails, hitter pipeline, generic regime risk) and target unexamined assumptions — places the analysis may be deceiving itself.

**Status legend:** 🟡 partially addressed this session · ⬜ open · (each item notes where it stands)

---

## Tier 1 — these question whether "the edge is real and profitable" is actually established

### 1. 🟡 You model against one market but bet in a completely different one
The entire edge calc is model-projection vs **sportsbook** no-vig (DK/FanDuel via The Odds API). But since 2026-05-25 all bets are on **Underdog** pick'em — fixed 3×/6× multipliers on *UD's* lines, which are set independently and are often softer/slower than the sharp consensus. Every validated edge number (`+0.43 ROI`, the v0 flip case, the 52–54% hit rate) is "model vs sportsbook." Whether that edge survives on UD's lines was, until this session, literally unmeasured.
**Status:** UD Lab tab shipped (`39a3a81`) — captures UD lines and recomputes the model against them. Makes #1 *measurable*; the actual answer comes after a few weeks of captured lines + the first 5/29 read.

### 2. 🟡 Your honest hit rate is below the Underdog breakeven
UD power-play breakevens: 2-leg @3× needs **57.7%** per leg, 3-leg @6× needs **55.0%**. Honest slate-time focus picks hit **52.3% overall, 54.3% on overs** (§2.4) — *below* breakeven for the format actually being played. The +64% at edge≥0.10 is single-leg sportsbook grading with selection-optimism, not the UD reality. The docs never put "52–54% honest hit rate" next to "need 55–58% to beat UD."
**Status:** UD Lab verdicts now enforce this — a pick only reads "Bet" if its calibrated prob clears the UD break-even, so the gap is visible per-pitcher. Still need real-money confirmation.

### 3. ⬜ Roughly half your real-money profit is free promo money
§4 reports +27.1% ROI / +$161.83. But $81.45 of that was free-entry promos at $0 cost. Paid-only: $596.35 staked → $676.73 returned = **+13.5%**, not +27.1%. And +13.5% over 96 high-variance parlays at a 33% hit rate — is that statistically distinguishable from zero? The standard error likely swamps it. The doc calls the ledger "the single best evidence the edge is real," but the promo-stripped number is half as large and may be noise.
**Status:** Open. Worth computing the promo-stripped ROI's confidence interval.

### 4. ⬜ You never measure closing line value (CLV)
Outcome-based ROI over 96 bets is near-pure variance. CLV isn't: if a morning line moves *toward* your side by game time, that's market-confirmed edge independent of the result — and it's *more* powerful precisely when the sample is small. The slate-time line is already captured; the closing line is not.
**Status:** Open. Phase-2 candidate (CLV-against-UD), now feasible because UD Lab captures the entry line.

---

## Tier 2 — model design and statistics

### 5. ⬜ Why is v0 better — and does the mechanism imply shrinkage, not a binary flip?
The docs treat "v0 wins" as a fact to act on (June 7 flip) but don't pin the mechanism. Likely: the matchup/park/lineup adjustments are noisily estimated on thin early-season samples, adding variance without reducing bias. If so, the right fix isn't "v0 forever" — it's **regularize the adjustments toward zero** so they contribute when the sample is large (late season) and vanish when thin (now). A hard v0/v2 flip permanently discards a signal that may be real by August.
**Status:** Open. Consider shrinkage as an alternative to the binary flip before/around June 7.

### 6. ⬜ The overconfidence tail may be a distributional bug, not a calibration bug
The 0.9–1.0 P(over) bucket predicts 98%, hits 45% (§2.2). Poisson assumes variance = mean; real K counts are **overdispersed** (variance > mean). If the count distribution is wrong, *every* P(over) is miscalibrated — the tail just shows it most. Swapping Poisson → **negative binomial** might fix calibration globally, not just patch the tail with Platt.
**Status:** Open. Test the distributional assumption itself.

### 7. ⬜ You're structurally long overs into a regime that historically regresses
Overs are the strong book (+0.240 vs unders +0.158), and the whole sample is cold-weather April–May when leaguewide K rates run high. As weather warms and hitters catch up, K rates fall — the over edge could **invert**, not just fade. The docs flag "single regime" generically; they don't flag the *specific directional risk* that the best book is the one most exposed to seasonal mean-reversion.
**Status:** Open. Watch over-vs-under ROI split as the season warms.

### 8. ⬜ The leg-independence assumption has real same-day correlations
"Fine for K props — different lineups" misses: same-game starters (pitcher's duel → both over; slugfest → both under), shared home-plate **umpire** (tight zone lifts Ks across that game), weather, and the day's leaguewide offensive environment. Correlated legs make parlays riskier than the independence math says — and on UD, where you *must* parlay, this directly inflates assumed win probability.
**Status:** Open. Phase-2 candidate (correlation adjustment in the UD-aware suggester).

---

## Tier 3 — data integrity, risk, and purpose

### 9. ⬜ Are injury / early-hook / rain-shortened starts poisoning your actuals?
A pitcher pulled after 2 IP with a tweaked hamstring logs a low K count that isn't a model miss — it's a non-baseball event. Those rows corrupt both model evaluation (fake "errors") and bet settlement. Does anything filter starts that ended abnormally early?
**Status:** Open. Audit settle.py / actuals ingestion for an early-exit filter.

### 10. ⬜ Have you modeled risk of ruin?
$300 bankroll, $5–10 tickets, 33% hit rate on parlays = enormous variance. What's the probability of a 50% drawdown in a normal cold streak? There are bankroll *discipline* rules but no *ruin probability* under the actual parlay variance.
**Status:** Open.

### 11. ⬜ What is success, really? (the framing question)
Path-C says "building primary, bets are integration tests, no volume target," yet the docs lean hard on "the edge is real and profitable." Those sit in tension: if the edge is genuinely +13.5% real, the rational move is to scale — but the bankroll is static $300 with no volume target. **Is the money the point, or is the modeling the point?** If modeling, then #1–#4 (is the edge real?) matter less and the optimization target is learning. If money, the static bankroll leaves the thesis untested at any meaningful scale. This changes which of the above questions are worth the time.
**Status:** Open — arguably the one to answer first, since it reprioritizes everything else.

---

## Claude's priority read (at the time)
Highest-value: **#1, #2, #4** — they could mean the headline profitability claim is measuring the wrong market with the wrong instrument. #1 and #2 are now partially addressed by the UD Lab tab; #4 is unblocked by it. **#11** is the meta-question that determines how much the rest matter.
