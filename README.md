# DFS Intelligence System

A data pipeline and modeling system for identifying +EV prop bets on daily fantasy sites (PrizePicks, Underdog), covering **MLB pitcher strikeouts** and **MLB hitter strikeouts**. The dashboard surfaces both as separate tabs.

## Daily Routine

The simplest workflow is the **local dashboard server** — start it once, click buttons in your browser the rest of the morning.

```sh
cd ~/bets
.venv/bin/python -m bets.server
```

Then open <http://127.0.0.1:8000> in a browser (override with `BETS_PORT=5050 python -m bets.server` if you need a different port — 5000 conflicts with macOS AirPlay Receiver). The dashboard has two buttons in the header:

- **Refresh Lines** — re-pulls odds and recomputes today's projections (~30s, costs ~16 of 500 monthly Odds API requests)
- **Settle Yesterday** — fetches actual K outcomes for yesterday's slate and updates the Recent Results section

Recommended morning order: click **Settle Yesterday** first (cheap, fast), then **Refresh Lines** once.

**Pure CLI alternative** (no server, ideally before noon ET):

```sh
.venv/bin/python -m bets.main && .venv/bin/python -m bets.settle
open output/index.html   # static dashboard, no buttons
```

That's it. The chained command does two things:

1. `bets.main` — projects today's slate and captures sportsbook lines from The Odds API. Writes `output/pitcher_ks_<today>.csv` and regenerates `output/index.html`.
2. `bets.settle` — settles **yesterday's** projections against actual K outcomes from MLB. Writes `output/pitcher_ks_<yesterday>_settled.csv`, which feeds the calibration harness.

**Run early.** Sportsbooks pull pitcher prop lines once games start, so a late run captures fewer comparisons and overwrites the morning's CSV with a worse snapshot.

**Periodic (whenever, weekly is fine):**

```sh
.venv/bin/python -m bets.analyze
```

Reviews v0/v1/v2 model accuracy head-to-head, P(over) calibration, and edge-strategy ROI across every settled day. Not time-sensitive — the more settled days you've accumulated, the more meaningful the numbers.

## First-Time Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env to add your ODDS_API_KEY
chmod 600 .env
```

Get a free Odds API key at https://the-odds-api.com (500 requests / month). Without a key the projections still run; only the line-comparison and EV columns are skipped.

## Project Layout

```
bets/
├── README.md                  this file
├── requirements.txt
├── .env                       ODDS_API_KEY (gitignored, chmod 600)
├── .env.example
├── .venv/                     virtualenv (gitignored)
├── bets/                      package
│   ├── __init__.py
│   ├── config.py              constants: blend weights, park factors, paths, league averages
│   ├── fetch.py               MLB Stats API: probable starters + lineups, pitcher stats, team K%, batched lineup K%; Baseball Savant: SwStr% with 12h disk cache
│   ├── odds.py                The Odds API: multi-book aggregation (median line, best odds, median consensus P(over))
│   ├── model.py               v0 / v1 / v2 projection functions; Poisson P(over); American odds + no-vig + EV math
│   ├── main.py                CLI entry: fetch slate → project all 3 models → match lines → write CSV → regenerate dashboard
│   ├── settle.py              Fetch actual K outcomes from MLB gameLog; write *_settled.csv with errors and PnL
│   ├── analyze.py             Aggregate every settled CSV: MAE / RMSE / bias for v0/v1/v2, P(over) buckets, edge-threshold ROI
│   ├── web.py                 Static HTML dashboard generator (focus tags, recent results section, tooltips)
│   └── server.py              Flask server: GET / serves dashboard, POST /refresh runs main, POST /settle settles yesterday
├── data/                      caches (gitignored): swstr_<season>.json
└── output/                    daily artifacts (gitignored except .gitkeep):
    ├── pitcher_ks_<date>.csv          one per day of projections
    ├── pitcher_ks_<date>_settled.csv  one per settled day
    └── index.html                     latest dashboard
```

## Command Reference

| Command | When | Writes |
|---|---|---|
| `python -m bets.server` | Once per session — runs in foreground | Serves dashboard at http://127.0.0.1:8000 |
| `python -m bets.main` | Every morning (CLI flow) | `output/pitcher_ks_<today>.csv`, `output/index.html` |
| `python -m bets.hitters` | Every morning, after lineups post (~2–3 hrs before first pitch) | `output/hitter_ks_<today>.csv` |
| `python -m bets.settle` | Every morning (settles yesterday — both pitchers and hitters) | `output/{pitcher,hitter}_ks_<yesterday>_settled.csv` |
| `python -m bets.settle 2026-04-30` | Settle a specific past date | `output/{pitcher,hitter}_ks_2026-04-30_settled.csv` |
| `python -m bets.analyze` | Periodic review | (prints to stdout) |
| `python -m bets.web` | Regenerate dashboard without re-running projections | `output/index.html` |

The Flask server's **Refresh Lines** button runs both pitcher and hitter pipelines back-to-back; **Settle Yesterday** settles both. The dashboard has two tabs: `#pitchers` (default) and `#hitters`.

## Dashboard

```sh
open output/index.html
```

The dashboard sorts pitchers into tiers based on the model's edge versus the no-vig fair line:

- **Focus** (green = over, red = under, 5–15% edge): plausible disagreement, worth a closer look.
- **Investigate** (yellow, ≥ 20% edge): edge too large to trust — almost always indicates a model gap (sample size, role change, missing context). Worth understanding *why* the model disagrees, not bet on directly.
- **No line** (gray): no sportsbook line available, either because the book hasn't posted or the game has already started. Projection only.

The dashboard also has a **Recent Results** section below today's slate, showing the most recently settled day with predicted vs. actual Ks and OVER/UNDER hits — so you get same-page feedback on how the model has been doing.

The 14-day calibration summary at the top (MAE, bias, flat-bet ROI) populates as you accumulate `*_settled.csv` files.

## Deployment (Netlify + GitHub Actions)

The dashboard can be published to a public Netlify URL so you can read it from any device. The architecture:

1. **GitHub Actions** runs the pipeline once a day (default: 13:30 UTC ≈ 9:30 AM ET) — see `.github/workflows/refresh.yml`.
2. The Action regenerates `output/`, commits, and pushes.
3. **Netlify** auto-deploys the new `output/` folder on every push.

The published page is **read-only** — when `STATIC_MODE=1` is set in the workflow env, the action buttons are replaced with an "Auto-refreshed daily" timestamp. The `ODDS_API_KEY` lives only in GitHub Secrets, never on Netlify.

### One-time setup

```sh
# 1. Initialize git locally (if not already)
git init -b main
git add .
git commit -m "initial commit"

# 2. Create a private GitHub repo and push (gh CLI shown; web UI also works)
gh repo create bets --private --source=. --remote=origin --push

# 3. Add the Odds API key as a GitHub Actions secret
gh secret set ODDS_API_KEY     # paste your key when prompted

# 4. Connect the repo to Netlify
#    https://app.netlify.com/start → pick the repo → it auto-detects netlify.toml
#    The publish dir is "output" (already configured); no build command needed.
```

After the first manual workflow run (Actions tab → "Refresh dashboard" → Run workflow), Netlify deploys the dashboard. Daily refreshes are then automatic.

To change the refresh time, edit the cron in `.github/workflows/refresh.yml` — the schedule is in UTC.

## Status

- ✅ v0 model: blended K% × flat expected BF
- ✅ v1 model: per-pitcher expected BF + log5 matchup vs opposing team K%
- ✅ v2 model: SwStr%-blended pitcher K% (via Baseball Savant, cached 12h), lineup-level opp K% (with team K% fallback when lineup not posted), park K factors
- ✅ The Odds API integration with Poisson `P(over)`, no-vig fair probability, per-side EV
- ✅ Calibration harness: settle vs actual K outcomes, MAE / RMSE / bias for v0 / v1 / v2 head-to-head, P(over) buckets, edge-threshold ROI
- ✅ Static HTML dashboard with focus highlighting and explicit OVER / UNDER recommendations
- ✅ Multi-book line aggregation: median consensus line, best odds with sourcing book, median no-vig probability across books
- ✅ Local Flask server with Refresh Lines / Settle Yesterday buttons + same-page Recent Results section
- ✅ Hitter K v0 model: log5(hitter K%, opp starter K%) × park × lineup-slot PA — separate dashboard tab + own settle path
- ⏳ Hitter K v1: bullpen K% blending (currently treats all PAs as vs starter), platoon splits, lineup-slot PA from per-player history
- ⏳ Empirical-Bayes shrinkage, catcher framing, umpire tendencies
- ⏳ Bankroll / Kelly sizing

---

## Background

### Goal

- Pull public sports data
- Build statistical models for player prop outcomes
- Compare model projections vs. PrizePicks / Underdog lines
- Surface bets where modeled expected value clears the platform's built-in vig

### Feasibility Notes

**Technically feasible.** The data is largely free and public; the math is well-understood. The hard part is the *edge*, not the build.

- PrizePicks / Underdog lines have sharpened significantly over the last few years
- ~4% built-in vig on standard 2-pick flex contests must be cleared by every bet on average
- Most public projection sources are commoditized — moats come from injury-news latency, modeling underwatched props, or disciplined bet selection

**Non-technical considerations:**

- Scraping PP / UD's own boards likely violates their TOS — plan to pull comparison lines from sportsbook APIs and treat the PP / UD board as manual input
- These are legally DFS contests in most US states, with different tax and account-limit implications than sportsbooks

### Why Baseball First

- **Best free public data ecosystem** of any sport: MLB Stats API, Statcast (Baseball Savant), pybaseball, FanGraphs
- **Daily volume**: 162-game season → constant opportunities and fast model iteration
- **Modeling-friendly structure**: at-bats are relatively independent units; park factors and weather are well-quantified

### Why Pitcher Strikeouts as the First Prop

Pitcher K's are the most tractable MLB prop:

- **Stickiest stat**: pitcher K% is one of the most stable, year-over-year predictable stats in baseball
- **Clean math**: `expected Ks ≈ pitcher K% × expected batters faced`, adjusted for opposing lineup K rates and park
- **Lower variance than hitter props**: a starter facing 20–28 batters has much less single-game variance than a hitter with 4 plate appearances
- **Rich data**: per-pitcher and per-batter K rates available daily, with platoon splits, recent form, and matchup history

**Tradeoff:** Pitcher Ks is also the most-watched MLB prop, so lines are relatively sharp. The early win is a clean end-to-end pipeline on a tractable prop, not finding huge edges. Once the system is solid, extend to:

1. Hitter strikeouts (similar math, inverse)
2. Hitter hits / total bases (noisier but potentially more edge)
3. Pitcher outs recorded (manager-dependent, harder)

### Data Sources (planned)

| Source | Use | Notes |
|---|---|---|
| MLB Stats API | Schedules, probable starters, lineups, game state | Free, official, no auth |
| Baseball Savant / Statcast | Pitch-level data, K rates, xStats | Free, comprehensive |
| pybaseball (Python) | Convenience wrapper for Statcast / FanGraphs | Open source library |
| FanGraphs | Advanced metrics, public projections | Free for basic; some paywalled |
| The Odds API | Sportsbook prop lines for comparison | Free tier available |
| Weather API | Game-time conditions | Lower priority for K props |

### Pitcher K Model — Approach

**v0 (baseline):** Project pitcher K's as:

```
expected_Ks = pitcher_K%_recent × expected_BF
```

Where:

- `pitcher_K%_recent` blends season K% with last-N-starts K%
- `expected_BF` was a flat constant in v0; v1 derives it per-pitcher from gameLog

**v1 (matchup-adjusted):** log5 adjustment for opposing lineup:

```
matchup_K% = (pitcher_K% × opp_team_K%) / league_K%
expected_Ks = matchup_K% × per-pitcher expected_BF
```

**v2 (current):** v1 plus three Tier-1 inputs:

```
xK% = pitcher_SwStr% × (league_K% / league_SwStr%)         # SwStr-implied K%
pitcher_K% = SWSTR_BLEND × xK% + (1 - SWSTR_BLEND) × actual_K%
opp_K% = lineup_K% if confirmed lineup posted else team_K%  # finer-grained
matchup_K% = (pitcher_K% × opp_K% / league_K%) × park_factor
expected_Ks = matchup_K% × per-pitcher expected_BF
```

SwStr% is fetched from Baseball Savant's pitcher leaderboard CSV and cached 12 hours. Lineups are hydrated on the schedule call (typically populated 2–3 hrs pre-game). Park factors are a static multi-year-average table in `config.py:PARK_K_FACTORS`.

**v3 — patience first, then features.** First v2 measurement showed v2 marginally *worse* than v1 on a tiny sample (v1 MAE 1.77, v2 MAE 1.82), which is what overfitting / small-sample noise dominance looks like. The honest priority order:

1. **Run for 30+ days** to get a real sample for the calibration harness. Every conclusion before that is noise.
2. **Tune `SWSTR_BLEND_WEIGHT`** (currently 0.35) once there's data — sweep it and pick what minimizes v2 MAE / RMSE.
3. **Refresh `PARK_K_FACTORS`** annually from FanGraphs Guts! — current values are conservative multi-year approximations.
4. **Empirical-Bayes shrinkage** for small-sample pitchers (best response to early-season Houser-style outliers).
5. **Platoon splits** (lineup K% vs LHP/RHP).
6. **Catcher framing**, **umpire tendencies** — modest signal, real complexity.

The bigger structural edge isn't more model features — it's **information speed** (line movement tracking, late-news monitoring). That's a v4+ concern.

**Output:** A modeled distribution (currently Poisson) so we can compute `P(over)` for any line. The calibration harness compares v0 / v1 / v2 head-to-head on realized outcomes, so model changes get measured rather than assumed.

### Open Questions

- Backtesting: can we source historical PP / UD lines, or do we backtest against sportsbook K props as a proxy?
- Bankroll management: Kelly-fraction sizing vs. flat unit?
- How to handle late scratches and weather scratches in the pipeline?
- Multi-leg correlation: PP / UD parlays are correlated by definition — how do we account for that in EV?
