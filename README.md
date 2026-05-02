# DFS Intelligence System

A data pipeline and modeling system for identifying +EV prop bets on daily fantasy sites (PrizePicks, Underdog). Currently active: **MLB pitcher strikeouts**. The hitter strikeouts pipeline exists but is paused while the pitcher model accumulates calibration data — see [Re-enabling hitters](#re-enabling-hitters).

The dashboard has two main surfaces:
- **Pitcher Ks tab** — public on Netlify. Today's slate (with first-pitch time in Central), "Today's Picks" hero cards for actionable focus picks, Parlay Suggestions ranked by EV per $1 (with one-click handoff to the Bets tab when running locally), Yesterday's Results report card, and a 14-day Track Record (sparkline + trend arrows + OVER/UNDER split).
- **Bets tab** — local-only personal parlay ledger with live K tracking from MLB Stats API. Picker-driven entry from today's slate, live Combined stats panel (Payout / Hit % / Edge / EV / Profit-if-hit) that recomputes on every leg change, mid-game lock-in for HIT/MISS, auto-settle on definitive verdicts, free-entry exclusion from totals.

## Daily Routine

Open **https://winningbets.netlify.app/** in any browser. The dashboard fetches the latest CSVs from this GitHub repo at page-load — click **Refresh data** any time to re-pull, no Netlify deploy required (so refreshing is *free*, doesn't burn your Netlify credit budget).

The pipeline runs only when you ask it to — there is no daily cron. Pull lines on demand close to first pitch, either from the local dashboard's **Re-run pipeline** button or from GitHub:

```sh
gh workflow run "Refresh dashboard" -R chirsch95/bets
```

Wait ~1 minute and click **Refresh data** on the dashboard. Re-runs only spend Odds API credits on games that aren't already priced in today's CSV — covered games are skipped automatically.

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
│       └── refresh.yml             manual-only (workflow_dispatch); cron removed 2026-05-01 to conserve Odds API quota
├── .env                            ODDS_API_KEY (gitignored, chmod 600)
├── .env.example
├── .venv/                          virtualenv (gitignored)
├── bets/                           package
│   ├── __init__.py
│   ├── config.py                   constants: blend weights, park factors, lineup PA, paths
│   ├── fetch.py                    MLB Stats API (starters + lineups + pitcher/hitter stats); Baseball Savant SwStr% (12h disk cache)
│   ├── odds.py                     The Odds API: pitcher_strikeouts + batter_strikeouts, multi-book aggregation, line preservation across reruns
│   ├── model.py                    v0/v1/v2 pitcher projections + v0 hitter projection; Poisson P(over); odds + EV math
│   ├── main.py                     CLI: project today's pitcher slate; freezes first run as the day's slate snapshot
│   ├── hitters.py                  CLI: project today's hitter slate (separate runner for clarity)
│   ├── settle.py                   Settle yesterday with actuals + slate-time fields (slate_edge/line/over_hit/pnl) for honest pick grading
│   ├── analyze.py                  Aggregate settled history (pitcher only so far)
│   ├── live.py                     Slate-pitcher list + live K-count from MLB Stats API boxscore + schedule (60s in-memory cache)
│   ├── wagers.py                   Personal bet ledger: load/save/CRUD on data/bets.json; legs[] schema; totals exclude free entries
│   ├── web.py                      HTML+JS dashboard shell (client-side rendered); same HTML works on Netlify + localhost (runtime hostname check hides local-only buttons/tab)
│   └── server.py                   Local Flask server (port 8000); GET / serves shell + output/ CSVs as static; /api/bets, /api/slate-pitchers, /api/live-ks for the local-only Bets tab
├── data/                           gitignored — caches + private bet ledger:
│   ├── swstr_<season>.json
│   └── bets.json                   personal bet ledger (NEVER committed; never reaches Netlify)
└── output/                         tracked — Netlify publishes from here:
    ├── pitcher_ks_<date>.csv               live state, overwritten on each run
    ├── pitcher_ks_<date>_slate.csv         frozen first-run snapshot for grading
    ├── pitcher_ks_<date>_settled.csv       projections + actuals + slate_* fields
    ├── hitter_ks_<date>.csv
    ├── hitter_ks_<date>_settled.csv
    └── index.html                          latest dashboard
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

Local data and Netlify data drift because they're independent disks. To pull whatever the most recent GitHub Actions run produced into your local copy: `git pull origin main`.

## Dashboard

```sh
open output/index.html
```

The dashboard sorts pitchers into tiers based on the model's edge versus the no-vig fair line:

- **Focus** (green = over, red = under, 5–15% edge): plausible disagreement, worth a closer look. These surface as **Today's Picks** hero cards above the full table.
- **Investigate** (yellow, ≥ 20% edge): edge too large to trust — almost always indicates a model gap (sample size, role change, missing context). Worth understanding *why* the model disagrees, not bet on directly.
- **No line** (gray): no sportsbook line available, either because the book hasn't posted or the game has already started. Projection only.

By default only **focus + investigate** rows are shown. A "Show N noise / no-line" toggle above the table reveals the rest; preference persists in `localStorage`.

The Opponent column prefixes the team name with **`vs`** when the pitcher is the home team and **`@`** when away, so you can tell at a glance whether the pitcher is on the mound for the top or bottom of each inning. The same convention applies in the hero cards, the Bets-tab pitcher dropdown, and the Yesterday's Results table.

### Time + live status column

Each row's Time cell does triple duty depending on game state:

- **Pre-game**: `7:10 PM CT (in 47m)`. The relative countdown updates every minute; the suffix turns yellow/bold under 30 minutes.
- **Live**: `● B5 4K` — pulsing red dot, compact half-inning (`B5` = bottom 5th, `T3` = top 3rd, etc.), running K count.
- **Final**: `Final · 7K`.

Once first pitch passes the row dims to 55% opacity (it's locked from the bet window). Live K + game status are pulled directly from the public MLB Stats API in JS — works on Netlify and locally without a proxy.

### Today's Picks hero cards

Each focus pick gets a card at the top with `BET OVER 6.5` (or UNDER), the model edge, our projection, our %, and a **live status box** showing the same data as the row's time cell. While the game is in progress the live box also surfaces a `5 of 6.5` pace label.

**Outcome coloring**: once a card's pick is mathematically settled — mid-game (`ks > line` permanently locks the verdict) or at Final — the card flips to a **solid saturated fill** (deep green for HIT, deep red for MISS) with white text and a **full-width HIT/MISS banner** across the top, regardless of the original direction. Pre-settle cards keep the subtle pale tint, so the flip is unmistakable on a slate of mostly-pending cards. The original `BET OVER 6.5` pill stays visible in the header so you can see the bet you placed.

### Parlay Suggestions

Below the hero picks, a **Parlay Suggestions** section ranks the top 5 two-leg and top 3 three-leg combinations of today's focus picks by **EV per $1**. Each card shows the leg list (direction badge · pitcher · line · game time) and a stats row (combined Payout · Hit % · Edge · EV / $1). Cards get a green left-border for +EV, red for −EV.

Combined probabilities assume leg independence — fine for K props since two pitchers in the same game face *different* lineups. Eligible legs are focus-band only; investigate / noise picks are excluded.

When running locally, each card has a **+ Add to bets** button that switches to the Bets tab and pre-populates a new parlay form with the suggested legs. The button is hidden on Netlify.

### Yesterday's Results report card

A summary card sits above the per-pitcher results table showing **W–L record on focus picks**, **net units (1u flat)**, and **hit rate**. Picks are graded against the **slate-time** line + edge (the morning state, not whatever survived to gametime), so the verdict reflects what you'd actually have bet at. Full per-pitcher table below shows Off By, Line, Our Pick badge, and HIT/MISS verdict for actionable picks (informational OVER hit/UNDER hit for non-bets).

### Track Record (last 14 days)

A rolling-window section below Yesterday's Results aggregates focus picks across the available settled days:
- Top stats: Picks · Hit rate · Units · ROI (with week-over-week trend arrows once 8+ picks accumulate)
- SVG sparkline of cumulative units (auto-scales, fills green/red below the zero line). **Hover any day** for a tooltip with date, day units (signed/colored), W–L, and cumulative total to that point.
- OVER/UNDER split panel showing share + per-side W-L + units
- Per-day breakdown table

Slate-time fields (`slate_edge`, `slate_line`, `slate_over_hit`, etc.) are added by `settle.py` when a `_slate.csv` snapshot exists for the date. Older settled rows fall back to live/final-state fields gracefully.

### Bets tab (local only)

A **personal parlay ledger** for tracking actual DFS bets, hidden on Netlify (visible only when the page is loaded from `localhost`/`127.0.0.1`). Backed by `data/bets.json` which is gitignored.

- **Structured parlay entry** with leg-count selector (2–6 legs, matching DFS-site minimums). Each leg has a pitcher picker (auto-fills from today's slate including model recommendation), a per-leg line override (DFS lines often differ from sportsbook), and an O/U toggle.
- **Live Combined stats panel** above the stake/odds inputs: recomputes Payout, Hit %, Edge, EV per $1, and Profit-if-hit on every leg-state change (pitcher select, line input, O/U toggle, leg-count, stake). Auto-fills the Odds field with the parlay decimal — once you type into Odds yourself, your value sticks. Reading from the slate's `p_over` / `novig_over`, so the math you see in the editor matches what the Pitcher-tab Parlay Suggestions show.
- **+ Add to bets** handoff: clicking a parlay-suggester card jumps to the Bets tab with the suggested legs already filled in and the Combined panel showing the same numbers.
- **Live K tracking** per leg: queries MLB Stats API boxscore + schedule via `/api/live-ks` (60s in-memory cache). Statuses: `Sched`, `Live · Top 5th`, `7 K [HIT]`, `2 K [MISS]`, etc.
- **Mid-game lock-in**: once `ks > line`, an OVER bet locks as HIT and an UNDER bet locks as MISS regardless of game state — Ks can only increase. The opposite cases (over not yet reached, under still alive) wait for game final.
- **Parlay-level rollup** in the expanded view: "Win confirmed", "Loss confirmed", or "In progress" with leg counts (1H · 1M · 1P) plus a mismatch warning if your manual W/L disagrees with the math.
- **Inline status badge** on each row (no expand needed): compact `1H · 1M · 1P` next to the legs summary.
- **Auto-settle** on definitive verdicts: when a parlay is locked Win or Loss, the bet's W/L is automatically updated and payout calculated (`stake × odds` for W, `0` for L). User can override with Reopen.
- **Free-entry flag**: tickets marked as free entries are excluded from `staked` and `ROI` totals (their winnings still count toward `returned`). Shown separately on a secondary totals line.

The Flask server's `/api/bets` (CRUD), `/api/slate-pitchers`, and `/api/live-ks` routes serve the tab. None of these reach Netlify — the tab itself is hidden via the `local-only` CSS class plus a synchronous head script that adds `is-local` to `<html>` only when `location.hostname` matches localhost.

## Deployment (Netlify + GitHub Actions)

The dashboard can be published to a public Netlify URL so you can read it from any device. The architecture:

1. **GitHub Actions** runs the pipeline only on demand (`workflow_dispatch`). Trigger via `gh workflow run "Refresh dashboard" -R <user>/<repo>`, the Actions tab, or the local **Re-run pipeline** button. Cron was removed 2026-05-01 to conserve Odds API quota — the pipeline costs ~16 credits/run on a typical 15-game day, so a daily cron alone (~480/month) would burn nearly the whole 500/month free tier. Re-runs skip games already priced in today's CSV (`skip_team_pairs` in `odds.py`) so a second pull only spends credits on still-uncovered games.
2. The Action regenerates `output/`, commits, and pushes.
3. **Netlify** publishes a thin HTML+JS shell. The browser fetches CSV data directly from `https://raw.githubusercontent.com/chirsch95/bets/main/output/*.csv` on each page load, so committing new CSV data does NOT require a Netlify redeploy. The `netlify.toml` ignore rule (`git diff $CACHED_COMMIT_REF $COMMIT_REF -- output/index.html`) only redeploys when the *shell itself* changes — i.e. when you ship a code/UI change. CSV-only commits are free.

This requires the repo to be **public** (so `raw.githubusercontent.com` can serve the CSVs unauthenticated). Picks are already public via the Netlify URL, so making the source repo public doesn't expose anything new.

The published page is **read-only** by runtime detection — a synchronous head script checks `location.hostname` and only reveals the local-server-only buttons (Re-run pipeline, Settle yesterday) and the local-only Bets tab when running from `localhost`/`127.0.0.1`. Same `index.html` works in both environments — no env-var gotcha at build time. The `ODDS_API_KEY` lives only in GitHub Secrets, never on Netlify.

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

After the first manual workflow run (Actions tab → "Refresh dashboard" → Run workflow), Netlify deploys the dashboard. From there, refresh on demand whenever you need new lines.

If you later want a scheduled pull (e.g. weekly to keep settled CSVs current), add a `schedule:` block back to `.github/workflows/refresh.yml` — the existing `workflow_dispatch` trigger is preserved.

## Status

**Pitcher Ks**
- ✅ v0 model: blended K% × flat expected BF
- ✅ v1 model: per-pitcher expected BF + log5 matchup vs opposing team K%
- ✅ v2 model: SwStr%-blended pitcher K% (Baseball Savant CSV, 12h cache), lineup-level opp K% (team K% fallback), park K factors

**Hitter Ks** *(paused 2026-05-01, target re-eval ~2026-06-02 — see Re-enabling hitters below)*
- ✅ v0 model: log5(hitter K%, opp starter K%) × park × lineup-slot PA — code intact in `bets/hitters.py`
- ⏳ v1: bullpen K% blending (currently treats whole game as vs starter), platoon splits, per-player PA history

### Re-enabling hitters

The hitter pipeline is paused for **~1 month** (paused 2026-05-01, target re-eval ~2026-06-02) so the pitcher Ks model can be dialed in first — both to free up Odds API quota for pitcher reruns and to keep the calibration signal focused on a single market while v2 weights and `SWSTR_BLEND_WEIGHT` get tuned. To turn it back on:

1. Flip `SHOW_HITTERS = False` to `True` in `bets/web.py`
2. Uncomment the "Project today's hitters" step in `.github/workflows/refresh.yml`
3. Uncomment the `run_hitter_projections()` block in `bets/server.py:refresh`
4. Commit + push — Netlify will redeploy the shell with the hitter tab visible

That's it. The model code, settle path, and CSV format are all preserved untouched. Note: re-enabling adds another full per-game odds call to each pipeline run (`batter_strikeouts` market) — roughly doubles the per-run credit cost. With manual-only pulls this is manageable but worth keeping in mind before triggering many re-runs in a day.

**Pipeline + UI**
- ✅ The Odds API integration with multi-book aggregation: median line, best odds per side with sourcing book, median no-vig P(over)
- ✅ Line preservation across same-day reruns (`load_previous_*_lines` + `merge_lines`) so a late run doesn't wipe morning lines when books pull markets
- ✅ Calibration harness: settle vs actual outcomes, MAE / RMSE / bias for v0 / v1 / v2 head-to-head, P(over) buckets, edge-threshold ROI
- ✅ HTML dashboard with focus highlighting, OVER / UNDER recommendations, Recent Results section. Currently single-tab (Pitcher Ks); tabbed layout returns when hitters are re-enabled.
- ✅ Local Flask server (port 8000) with Refresh Lines / Settle Yesterday buttons — dev/test only
- ✅ Public Netlify deploy at https://winningbets.netlify.app/. **Client-side rendering**: thin HTML+JS shell on Netlify, browser fetches CSVs from raw.githubusercontent.com → CSV updates do NOT trigger Netlify redeploys, so on-demand pipeline commits are free. Manual **Refresh data** button re-fetches at any time.

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
