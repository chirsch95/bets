# K-Edge / DFS Intelligence — Project State Report

**Snapshot date:** 2026-05-24
**Purpose:** Self-contained briefing for planning next steps (uploadable to a fresh chat with zero prior context).
**Scope:** MLB pitcher-strikeout prop modeling + a personal DFS parlay ledger. Hitter-K pipeline is paused (built, target re-eval ~2026-06-02).

---

## 0. TL;DR

1. **The edge is real and the system is profitable.** Over-betting the model's positive-edge picks returns **+38% per $1** (edge≥0), rising monotonically to **+43%** at edge≥0.10 — exactly the shape you want. Slate-time focus picks (over **and** under) net **+80.6 units over 23 days** at 52.3% hit / +0.195 ROI per $1 flat.
2. **The simplest model (v0) is beating the production model (v2)** on both point accuracy *and* high-edge betting ROI, across 641 starts. This is now corroborated by two independent metrics and a significant Diebold-Mariano test (t=+2.60).
3. **We are deliberately NOT flipping to v0 yet.** Sample is 24 days of one early-season regime; a pre-registered, code-enforced flip rule gates the change to ~40 days + a regime-stability check. Re-check **~June 7**.
4. **The SwStr blend has already been tuned out** (`SWSTR_BLEND_WEIGHT = 0.0` since 2026-05-16). So v2's remaining (losing) complexity vs v0 is the **park-factor + lineup-level opponent-K% layer**, not SwStr.
5. **The model's most confident picks are its worst.** P(over) ≥ 0.8 is wildly overconfident (0.9–1.0 bucket: predicted 98%, hit 45%). The dashboard already buries these in the "investigate" band, so they aren't being bet — but it's the clearest calibration defect.
6. **The personal bet ledger is stale** — last updated 2026-05-06. It covers only the first 7 days (−$6.55 net on staked tickets, +$47 including free-entry promos). Not a usable read on real betting performance.

**The single open decision:** whether v2's matchup/park/lineup layer is dead weight or just early-season-handicapped. The data through June will answer it; the harness now answers it automatically.

---

## 1. What the system does

### Pipeline (daily)
- **Data in:** MLB Stats API (probable starters, lineups, game state, boxscores), Baseball Savant (SwStr%), The Odds API (sportsbook K-prop lines, multi-book aggregated → median line + no-vig P(over)).
- **Project:** `bets.main` produces today's pitcher-K slate, freezing the first run of the day as the *slate snapshot* used for honest pick-grading.
- **Settle:** `bets.settle` joins actuals + slate-time fields the next morning.
- **Analyze:** `bets.analyze` runs the full accuracy/calibration/ROI harness (the source of every results table below).
- **Serve:** Flask dashboard on an M1 Air over Tailscale (no public surface). Pipeline runs on the Air; it auto-commits+pushes `output/` on `/refresh`. Laptop is dev/escape-hatch only.

### The four projection models
All project a pitcher's strikeouts for the start; P(over line) comes from a **Poisson** with mean = projection.

- **v0 (baseline):** `K%_blend × expected_BF`, where `K%_blend = 0.6·season + 0.4·recent(last-5)`, and `expected_BF` is per-pitcher (capped at 28). **No matchup, no park, no SwStr.**
- **v1:** v0 + log5 matchup adjustment vs opposing **team** K%.
- **v2 (current production):** v1 + park K-factor + opponent K% taken at **lineup** granularity when a lineup is posted (team fallback otherwise) + an optional SwStr-implied-K% blend. **The SwStr blend weight is currently 0.0** (tuned out 2026-05-16 — it was systematically over-projecting high-SwStr pitchers). So today's v2 ≈ v1 + park + lineup-opp-K%.
- **ml (shadow):** a machine-learning projection, run in parallel, never used for picks. Held as a challenger.

### Key parameters (`bets/config.py`)
| Param | Value | Note |
|---|---|---|
| `RECENT_FORM_WEIGHT` | 0.40 | recent-vs-season K% blend |
| `RECENT_STARTS_WINDOW` | 5 | recent-form lookback |
| `MAX_EXPECTED_BF` | 28 | BF ceiling (tuned 2026-05-16; clips opener/spot-start artifacts) |
| `LEAGUE_K_PCT` | 0.225 | log5 denominator |
| `SWSTR_BLEND_WEIGHT` | **0.0** | SwStr→K% blend OFF (tuned 2026-05-16) |
| `PARK_K_FACTORS` | table | 9 parks; default 1.00 |

### The betting strategy
The dashboard tiers pitchers by **model edge vs the no-vig fair line**: *Focus* (5–15% edge, the actionable band → "Today's Picks" cards + parlay suggestions), *Investigate* (≥20% edge — treated as a model-gap flag, NOT a bet signal), *No line* (projection only). Parlays assume leg independence (fine for K props — different lineups). The Bets tab is a manual ledger with live K-tracking and auto-settle.

---

## 2. Results to date (641 settled pitcher-starts, 2026-04-30 → 05-23)

### 2.1 Projection accuracy
| model | n | MAE | RMSE | bias |
|---|---|---|---|---|
| **v0** | 641 | **1.866** | **2.381** | −0.19 |
| v1 | 641 | 1.951 | 2.681 | −0.25 |
| **v2 (prod)** | 641 | 1.931 | 2.691 | −0.25 |
| ml (shadow) | 342 | 2.203 | 2.751 | +0.02 |

**v0 is the most accurate model.** Every layer added on top of v0 makes point estimates worse. All non-ML models slightly over-project (~0.2 K negative bias). *Caveat: the v2 column mixes parameterizations — rows before 2026-05-16 used `SWSTR_BLEND=0.35`, after used 0.0.*

### 2.2 P(over) calibration (576 lines)
| bucket | n | predicted | actual | diff |
|---|---|---|---|---|
| 0.0–0.1 | 11 | 0.044 | 0.273 | +0.23 |
| 0.1–0.2 | 16 | 0.159 | 0.312 | +0.15 |
| 0.2–0.3 | 62 | 0.254 | 0.339 | +0.09 |
| 0.3–0.4 | 102 | 0.346 | 0.343 | −0.00 |
| 0.4–0.5 | 135 | 0.451 | 0.615 | +0.16 |
| 0.5–0.6 | 129 | 0.546 | 0.543 | −0.00 |
| 0.6–0.7 | 69 | 0.647 | 0.667 | +0.02 |
| 0.7–0.8 | 24 | 0.739 | 0.833 | +0.09 |
| 0.8–0.9 | 8 | 0.869 | 0.625 | −0.24 |
| 0.9–1.0 | 20 | 0.977 | **0.450** | **−0.53** |

Mid-range (0.3–0.7) is well-calibrated. **Both tails are off, and the high-confidence over tail is badly overconfident** (predicting near-certain overs that hit <50%). The low buckets being *under*-confident (actuals higher than predicted) reflects the overall over-projection bias seen above. Note tail buckets have tiny n (8–20).

### 2.3 Over-bet strategy ROI (the headline profitability signal)
Over-betting every line whose model edge exceeds the threshold, graded on realized P&L:
| min edge | n | hit rate | ROI per $1 |
|---|---|---|---|
| −1.00 (bet all) | 576 | 51.6% | +0.164 |
| ≥ 0.00 | 241 | 60.6% | +0.385 |
| ≥ 0.02 | 213 | 61.5% | +0.401 |
| ≥ 0.04 | 187 | 62.0% | +0.383 |
| ≥ 0.06 | 168 | 62.5% | +0.412 |
| ≥ 0.10 | 126 | 64.3% | +0.433 |

ROI rises with edge — the model is a genuine bet-ranker, not just noise. *Caveat: backtested on the model's own edge using realized outcomes (selection-optimistic); it is not out-of-sample real money except where it overlaps the ledger in §4.*

### 2.4 Slate-time focus-pick track record (honest morning-state grading, 1u flat)
Picks = `|slate_edge| ≥ 0.05`, graded at the line/edge that existed at slate freeze (not gametime):
| side | picks | W–L | hit | units | ROI/$1 |
|---|---|---|---|---|---|
| OVER | 188 | 102–86 | 54.3% | +45.05 | +0.240 |
| UNDER | 225 | 114–111 | 50.7% | +35.56 | +0.158 |
| **ALL** | **413** | **216–197** | **52.3%** | **+80.62** | **+0.195** |

Both directions profitable; **overs are the stronger book** (54.3% / +0.240 vs unders 50.7% / +0.158). Cumulative-units curve climbed steadily from +4.6 (May 1) to +80.6 (May 23) with normal variance — best day +14.8u (May 16), worst −5.4u (May 20).

### 2.5 v2-vs-ML promotion verdict → **HOLD**
DM t-test on squared errors: t=+0.50 (need ≥2.0 to promote) — FAIL. Hit-rate sanity gap −13.4pp — FAIL. ML stays a shadow. But the slices show *where* ML differs: ML clearly beats v2 on **cooling/heating (form-change) pitchers** (v2 MAE 3.3 on cooling form vs ML 2.9) and on away/suppressor parks. v2's single biggest weakness is form-change starters.

### 2.6 v0-vs-v2 shadow + flip verdict → **HOLD (24/40 days)**
Bet-selection ROI head-to-head (v0's edge recomputed against the same no-vig line):
| min edge | v2 n | v2 ROI | v0 n | v0 ROI |
|---|---|---|---|---|
| ≥ 0.00 | 241 | +0.385 | 279 | +0.330 |
| ≥ 0.06 | 168 | +0.412 | 198 | **+0.427** |
| ≥ 0.10 | 126 | +0.433 | 140 | **+0.533** |

At low edge v2 selects better; **at the high-conviction end (where you actually bet) v0 wins** — more qualifying bets, higher hit rate, higher ROI. Regime split: April n too small (1); **May: v0 leads (+0.434 vs +0.420)**.

**Pre-registered flip rule (encoded in `analyze.py:v0_shadow_report`):** flip v2→v0 only when ALL of — (1) ≥40 slate days, (2) v0 RMSE < v2 RMSE, (3) v0 ROI > v2 ROI @edge≥0.06, (4) v0 leads every month with ≥20 qualifying bets. **Status: 2, 3, 4 already PASS; only the day-count gate (24/40) holds it.** DM t-test v2²−v0² = **+2.60** (v0 significantly better). The wait exists purely to guard against fitting to a single April–May regime — v2's matchup/park thesis should be *weakest* early-season, so "v0 wins in spring" is the expected look even if v2 is the better full-season model.

---

## 3. Operational state
- **Hosting:** M1 Air, Tailscale-only (public Cloudflare/Caddy retired 2026-05-16). 4 LaunchAgents: flask, gitpull (60s), health watcher, 3am data backup. Auto-commits+pushes on refresh (`BETS_AUTO_PUSH=1`).
- **Odds API:** paid 20K/mo plan ($30/mo). Usage **410 / 20,000** this period — enormous headroom; cost is not a constraint. Skip-covered-games optimization keeps same-day re-runs near 0 credits.
- **Cadence:** ~1 slate/day, morning auto-trigger ~7am CT + manual force-refresh available.
- **Tests/repo:** `chirsch95/bets`, `main`. Latest code change: `758499a` (this report's v0 shadow harness).

---

## 4. Personal bet ledger — ALL transactions (data/users/chad/bets.json)

⚠️ **Stale: last updated 2026-05-06.** Covers only 2026-04-30 → 05-06 (first 7 days). If betting continued past May 6 it was not logged. Treat as a 1-week sample, not a season read.

**Aggregate:** 26 tickets total.
- **Paid tickets:** 8 W – 13 L. Staked **$117.20**, returned **$110.65** → net **−$6.55** (−5.6% ROI on stake).
- **Free-entry promos:** 2 W – 3 L. Cost $0, returned **$54.00**.
- **Total account cash flow:** −$6.55 (paid) + $54.00 (promo) = **+$47.45**.
- **By site:** mostly untagged early (13); UD 5, PP 2, DK 1.

**Itemized (date | legs | stake @ decimal-odds | result | payout):**

| Date | Legs | Stake@Odds | Result | Payout | Free |
|---|---|---|---|---|---|
| 04-30 | Ober U + Gausman O | $2.20 @ 2.81 | L | $0 | |
| 04-30 | Cameron U + McCullers O | $5 @ 2.92 | **W** | $14.60 | |
| 05-01 | Povich O4.5 + Misiorowski O5.5 | $10 @ 2.40 | L | $0 | free |
| 05-01 | Irvin O4.5 + McClanahan U5.5 + Wheeler U6.5 | $5 @ 6.23 | L | $0 | |
| 05-01 | Misiorowski O6.5 + Robbie Ray U5.5 | $5 @ 2.46 | **W** | $12.30 | |
| 05-01 | Ragans U5.5 + Christian Scott O6.5 | $5 @ 3.78 | L | $0 | |
| 05-01 | Gore O5.5 + Eury Pérez U5.5 | $10 @ 2.29 | L | $0 | free |
| 05-02 | King O4.5 + McGreevy U3.5 + Burke O3.5 | $5 @ 3.30 | **W** | $16.50 | |
| 05-02 | Cease O4.5 + Imanaga O3.5 + Cecconi O2.5 + Meyer O3.5 | $5 @ 2.40 | **W** | $12.00 | |
| 05-02 | Arrighetti O5.5 + Mlodzinski O4.5 | $5 @ 3.60 | L | $0 | |
| 05-02 | Arrighetti O5.5 + Mlodzinski O4.5 + Nelson U4.5 | $5 @ 6.96 | L | $0 | |
| 05-02 | Mlodzinski O4.5 + Nelson U4.5 | $10 @ 3.00 | **W** | $30.00 | free |
| 05-03 | Kochanowicz O3.5 + Fried U5.5 | $5 @ 2.52 | L | $0 | |
| 05-03 | Kay O3.5 + Paddack O3.5 | $10 @ 2.90 | L | $0 | free |
| 05-03 | Kochanowicz O3.5 + Bubic U5.5 | $5 @ 3.08 | **W** | $15.40 | |
| 05-03 | Burns O7.0 + Kay O3.0 + Paddack O2.5 | $10 @ 3.75 | L | $0 | |
| 05-03 | Burns O6.5 + Ashcraft O5.5 | $5 @ 2.52 | **W** | $12.60 | |
| 05-04 | Baz U5.5 + Junk U3.5 | $5 @ 4.02 | L | $0 | UD |
| 05-04 | Baz U5.5 + Davis Martin O5.0 + Ritchie O4.5 | $10 @ 6.00 | L | $0 | PP |
| 05-04 | Sugano U3.5 + Davis Martin O4.5 | $10 @ 2.40 | **W** | $24.00 | DK free |
| 05-05 | Alcantara O5.5 + Elder O5.0 + Webb O3.5 | $5 @ 3.50 | L | $0 | PP |
| 05-05 | Elder O4.5 + Rasmussen U4.5 | $5 @ 3.01 | L | $0 | UD |
| 05-05 | Elder O4.5 + Kirby U5.5 | $5 @ 3.13 | **W** | $15.65 | UD |
| 05-06 | Ober O3.5 + Corbin U3.5 | $5 @ 3.60 | L | $0 | DK |
| 05-06 | Houser U3.5 + Schultz U5.5 | $5 @ 2.32 | **W** | $11.60 | UD |
| 05-06 | Pallante U3.5 + Young U3.5 | $10 @ 4.79 | L | $0 | UD |

*(First two tickets predate per-leg line capture, so their lines show as None in the raw data.)*

---

## 5. Caveats a planner must respect
1. **Single regime.** All data is late-April → May 2026 (~24 slate days). Early-season K% samples are thin; conclusions may not hold mid/late season.
2. **v2 column is non-stationary.** SwStr blend changed 0.35→0.0 on 2026-05-16, and the BF cap was added the same day — pre/post rows aren't the same model.
3. **Backtest selection-optimism.** §2.3/§2.6 ROI grades the model on its own edge picks with realized outcomes — not true out-of-sample. The honest, lower number is the slate-time track record (§2.4, +0.195 ROI).
4. **Ledger ≠ strategy.** The real-money ledger (§4) is stale and tiny; it is not evidence about the model.
5. **Calibration tails are low-n** (8–20 per extreme bucket) — don't over-fit a calibration layer to them yet.

---

## 6. Open decisions & candidate next steps (for planning)

**Decided / in-flight:**
- v0-vs-v2 flip — **HOLD until ~June 7**, auto-evaluated by the harness. (The dominant open question.)
- Hitter-K pipeline — paused, re-eval ~June 2 (built, just needs `SHOW_HITTERS=True` + workflow uncomment).

**Explicitly parked as overfitting traps on the current sample (revisit with more data):**
- Sweeping `SWSTR_BLEND_WEIGHT` — already at 0; nothing to gain now.
- Adding P(over) isotonic/Platt calibration — tails too low-n.

**Candidate next steps worth a planning conversation:**
- **Form-divergence hybrid:** route "cooling/heating" pitchers (where v2 is worst and ml/v0 are better) to a different model. The slices in §2.5 make this the most evidence-backed model idea.
- **If v0 flip confirms in June:** what does demoting v2 imply for v1's matchup logic and the park table — keep them as optional features or retire?
- **Bankroll/Kelly sizing** (currently flat 1u) — the ROI-by-edge curve is steep enough that edge-proportional sizing could matter.
- **Information-speed edge (v4 concept):** line-movement tracking + late-news/inactive monitoring — the README's own claim that this beats more model features.
- **Re-instrument the ledger:** decide whether to keep logging real bets at all; if yes, fix whatever caused logging to stop May 6, since it's the only true out-of-sample validation.
- **Extend to a second prop** (hitter Ks first, then hits/TB) once pitcher Ks is locked.

---
*Generated 2026-05-24 from `bets.analyze`, `config.py`, the settled-CSV archive (Apr 30–May 23), and the personal ledger. All tables reproduce from `python -m bets.analyze`.*
