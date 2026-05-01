# DFS Intelligence System

A data pipeline and modeling system for identifying +EV prop bets on daily fantasy sites (PrizePicks, Underdog). Currently active: **MLB pitcher strikeouts**. The hitter strikeouts pipeline exists but is paused while the pitcher model accumulates calibration data — see [Re-enabling hitters](#re-enabling-hitters).

## Daily Routine

Open **https://winningbets.netlify.app/** in any browser. The dashboard fetches the latest CSVs from this GitHub repo at page-load — click **Refresh data** any time to re-pull, no Netlify deploy required (so refreshing is *free*, doesn't burn your Netlify credit budget).

A GitHub Action runs the underlying pipeline daily at noon CT to keep the CSVs fresh. For an off-schedule pipeline run (e.g. before a big night slate, or when news drops):

```sh
gh workflow run "Refresh dashboard" -R chirsch95/bets
```

Wait ~1 minute and click **Refresh data** on the dashboard. The local Flask server isn't part of the daily routine — it's reserved for code-change testing (see [Local Development](#local-development)).

**Periodic (weekly is fine):**

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
├── README.md                       this file
├── requirements.txt
├── netlify.toml                    publish dir = output/, ignore rule skips no-data deploys
├── .github/
│   └── workflows/
│       └── refresh.yml             daily 17:00 UTC cron + workflow_dispatch
├── .env                            ODDS_API_KEY (gitignored, chmod 600)
├── .env.example
├── .venv/                          virtualenv (gitignored)
├── bets/                           package
│   ├── __init__.py
│   ├── config.py                   constants: blend weights, park factors, lineup PA, paths
│   ├── fetch.py                    MLB Stats API (starters + lineups + pitcher/hitter stats); Baseball Savant SwStr% (12h disk cache)
│   ├── odds.py                     The Odds API: pitcher_strikeouts + batter_strikeouts, multi-book aggregation, line preservation across reruns
│   ├── model.py                    v0/v1/v2 pitcher projections + v0 hitter projection; Poisson P(over); odds + EV math
│   ├── main.py                     CLI: project today's pitcher slate
│   ├── hitters.py                  CLI: project today's hitter slate (separate runner for clarity)
│   ├── settle.py                   Settle yesterday's pitchers + hitters with actuals
│   ├── analyze.py                  Aggregate settled history (pitcher only so far)
│   ├── web.py                      HTML+JS dashboard shell (client-side rendered); STATIC_MODE=1 hides local-only buttons
│   └── server.py                   Local Flask server (port 8000); GET / serves shell, also serves output/ CSVs as static
├── data/                           caches (gitignored): swstr_<season>.json
└── output/                         tracked — Netlify publishes from here:
    ├── pitcher_ks_<date>.csv
    ├── pitcher_ks_<date>_settled.csv
    ├── hitter_ks_<date>.csv
    ├── hitter_ks_<date>_settled.csv
    └── index.html                  latest dashboard
```

## Command Reference

| Command | When | Writes |
|---|---|---|
| `python -m bets.server` | Once per session — runs in foreground | Serves dashboard at http://127.0.0.1:8000 |
| `python -m bets.main` | Every morning (CLI flow) | `output/pitcher_ks_<today>.csv`, `output/index.html` |
| `python -m bets.hitters` | *Paused* — manual only, costs Odds API quota | `output/hitter_ks_<today>.csv` |
| `python -m bets.settle` | Every morning (settles yesterday — pitcher; hitter no-ops while paused) | `output/pitcher_ks_<yesterday>_settled.csv` |
| `python -m bets.settle 2026-04-30` | Settle a specific past date | `output/pitcher_ks_2026-04-30_settled.csv` |
| `python -m bets.analyze` | Periodic review | (prints to stdout) |
| `python -m bets.web` | Regenerate dashboard without re-running projections | `output/index.html` |

The Flask server's **Re-run pipeline** button runs the pitcher pipeline (hitter pipeline call is currently commented out — see `bets/server.py`). The dashboard renders a single Pitcher Ks view; the Hitter Ks tab returns when `SHOW_HITTERS = True` in `bets/web.py`.

## Local Development

The local Flask server is for **testing code changes before they hit Netlify** — each push to `main` consumes ~15 of your monthly Netlify build credits, so iterate locally first.

```sh
cd ~/bets
.venv/bin/python -m bets.server   # http://127.0.0.1:8000
```

Override the port if 8000 is taken: `BETS_PORT=5050 python -m bets.server`. macOS reserves 5000 for AirPlay Receiver, which is why 8000 is the default.

**Workflow:**

1. Edit code in `~/bets/bets/`
2. Restart the server (Ctrl+C, then re-run) to pick up Python changes — browser hard-refresh (Cmd+Shift+R) is enough for HTML/CSS only
3. Verify the change at `http://127.0.0.1:8000`
4. Commit + push **only when satisfied** — that's the action that costs credits
5. Netlify auto-deploys on push (subject to the `output/*.csv`-changed ignore rule)

Local data and Netlify data drift because they're independent disks. To pull whatever the cron last produced into your local copy: `git pull origin main`.

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

1. **GitHub Actions** runs the pipeline once a day at 17:00 UTC (~12:00 PM CDT / 11:00 AM CST). See `.github/workflows/refresh.yml`. Trigger an extra run anytime via `gh workflow run "Refresh dashboard" -R <user>/<repo>` or the Actions tab.
2. The Action regenerates `output/`, commits, and pushes.
3. **Netlify** publishes a thin HTML+JS shell. The browser fetches CSV data directly from `https://raw.githubusercontent.com/chirsch95/bets/main/output/*.csv` on each page load, so committing new CSV data does NOT require a Netlify redeploy. The `netlify.toml` ignore rule (`git diff $CACHED_COMMIT_REF $COMMIT_REF -- output/index.html`) only redeploys when the *shell itself* changes — i.e. when you ship a code/UI change. Daily cron commits are free.

This requires the repo to be **public** (so `raw.githubusercontent.com` can serve the CSVs unauthenticated). Picks are already public via the Netlify URL, so making the source repo public doesn't expose anything new.

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

**Pitcher Ks**
- ✅ v0 model: blended K% × flat expected BF
- ✅ v1 model: per-pitcher expected BF + log5 matchup vs opposing team K%
- ✅ v2 model: SwStr%-blended pitcher K% (Baseball Savant CSV, 12h cache), lineup-level opp K% (team K% fallback), park K factors

**Hitter Ks** *(paused 2026-05-01 — see Re-enabling hitters below)*
- ✅ v0 model: log5(hitter K%, opp starter K%) × park × lineup-slot PA — code intact in `bets/hitters.py`
- ⏳ v1: bullpen K% blending (currently treats whole game as vs starter), platoon splits, per-player PA history

### Re-enabling hitters

The hitter pipeline was disabled to conserve Odds API quota while the pitcher model gathers ~30 days of calibration data. To turn it back on:

1. Flip `SHOW_HITTERS = False` to `True` in `bets/web.py`
2. Uncomment the "Project today's hitters" step in `.github/workflows/refresh.yml`
3. Uncomment the `run_hitter_projections()` block in `bets/server.py:refresh`
4. Commit + push — Netlify will redeploy the shell with the hitter tab visible

That's it. The model code, settle path, and CSV format are all preserved untouched. Note: re-enabling adds ~15 quota/cron-run = ~450/month back to the Odds API budget; either bump cron to every-other-day or move to the paid Odds API tier.

**Pipeline + UI**
- ✅ The Odds API integration with multi-book aggregation: median line, best odds per side with sourcing book, median no-vig P(over)
- ✅ Line preservation across same-day reruns (`load_previous_*_lines` + `merge_lines`) so a late run doesn't wipe morning lines when books pull markets
- ✅ Calibration harness: settle vs actual outcomes, MAE / RMSE / bias for v0 / v1 / v2 head-to-head, P(over) buckets, edge-threshold ROI
- ✅ HTML dashboard with focus highlighting, OVER / UNDER recommendations, Recent Results section. Currently single-tab (Pitcher Ks); tabbed layout returns when hitters are re-enabled.
- ✅ Local Flask server (port 8000) with Refresh Lines / Settle Yesterday buttons — dev/test only
- ✅ Public Netlify deploy at https://winningbets.netlify.app/. **Client-side rendering**: thin HTML+JS shell on Netlify, browser fetches CSVs from raw.githubusercontent.com → CSV updates do NOT trigger Netlify redeploys, so daily cron commits are free. Manual **Refresh data** button re-fetches at any time.

**Future**
- ⏳ Empirical-Bayes shrinkage, catcher framing, umpire tendencies
- ⏳ Bankroll / Kelly sizing
- ⏳ Isotonic / Platt calibration of P(over) once ~30 days of settled data accumulate

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
