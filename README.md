# DFS Intelligence System

A data pipeline and modeling system for identifying +EV prop bets on daily fantasy sites (PrizePicks, Underdog). Currently active: **MLB pitcher strikeouts**. The hitter strikeouts pipeline exists but is paused while the pitcher model accumulates calibration data — see [Re-enabling hitters](#re-enabling-hitters).

The dashboard has three main surfaces:
- **Pitcher Ks tab** — Today's slate (with first-pitch time in Central), "Today's Picks" hero cards for actionable focus picks, Parlay Suggestions ranked by EV per $1 (with one-click handoff to the Bets tab), Yesterday's Results report card, and a 14-day Track Record (sparkline + trend arrows + OVER/UNDER split).
- **Bets tab** — personal parlay ledger with live K tracking from MLB Stats API. Picker-driven entry from today's slate, live Combined stats panel (Payout / Hit % / Edge / EV / Profit-if-hit) that recomputes on every leg change, mid-game lock-in for HIT/MISS, auto-settle on definitive verdicts, free-entry exclusion from totals.
- **UD Lab tab** — recomputes the model against **Underdog's** actual line + Higher/Lower multipliers (we bet UD, but the pipeline's edge is vs the sportsbook line), with lineup-posted indicators, suggested UD parlays, and a "My record" betting-performance + bet-bar-tuning panel. Full reference: **[UD_LAB.md](UD_LAB.md)**.

Both surfaces live on the same Flask app served from an **M1 MacBook Air over Tailscale** — see [Deployment](#deployment-self-hosted-on-m1-air). Multi-user accounts gate the Bets tab per-person; the dashboard view is shared.

## Daily Routine

Open the Tailscale URL for the Air's Flask in any browser (your phone over Tailscale works too) — the dashboard fetches the latest CSVs from this GitHub repo at page-load.

**Air is the canonical pipeline.** Click **Re-run pipeline** in the dashboard — Flask's `/refresh` on the Air runs `bets.main`, then commits and pushes `output/` atomically under its pipeline lock (driven by the `BETS_AUTO_PUSH=1` env var set in the Air's `.env`). The laptop's `gitpull` cron picks it up within 60s for offline browsing; the dashboard updates as soon as the commit lands.

For local dev work (editing `bets/web.py`, testing changes before pushing), run Flask manually on the laptop:

```sh
.venv/bin/python -m bets.server   # http://127.0.0.1:8000
```

The laptop's `/refresh` does NOT auto-push (no `BETS_AUTO_PUSH` env var set) — it's safe to click during dev iteration. When happy, push via the laptop's `/push` button or `git push` directly.

**Escape hatch — `gh workflow run` (rarely needed now).** When the Air is down (rebooting, network issue) and you can't ssh in to bring it back:

```sh
gh workflow run "Refresh dashboard" -R chirsch95/bets
```

This runs the pipeline on GitHub's runner instead. Free if local has already run + pushed today (the short-circuit sees every starter already priced, so 0 Odds API credits). Costs ~16 credits if it's the first run of the day. Wait ~1 minute and click **Refresh data**.

Re-runs in either environment only spend Odds API credits on games not already priced in today's CSV — covered games are skipped automatically (`skip_team_pairs` in `odds.py`); when every starter is covered, the API call is skipped entirely.

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

Get an Odds API key at https://the-odds-api.com. The free Starter tier (500 req/month) works for low-volume use; the 20K plan ($30/mo) is comfortable headroom for daily morning pulls plus mid-day force-refreshes. Without a key the projections still run; only the line-comparison and EV columns are skipped.

## Project Layout

```
bets/
├── README.md                       this file
├── requirements.txt
├── .github/
│   └── workflows/
│       └── refresh.yml             manual-only (workflow_dispatch); cron removed 2026-05-01 to conserve Odds API quota
├── .env                            ODDS_API_KEY (gitignored, chmod 600)
├── .env.example
├── .venv/                          virtualenv (gitignored)
├── ops/                            self-hosting artifacts deployed on the M1 Air (see Deployment):
│   ├── git-pull.sh                 60s-cron script that runs `git pull --ff-only` on Air and laptop
│   ├── bets-url.sh                 legacy helper from the trycloudflare era; harmless to keep
│   ├── launchd/
│   │   ├── com.bets.flask.plist        LaunchAgent: python -m bets.server, KeepAlive
│   │   ├── com.bets.gitpull.plist      LaunchAgent: git-pull.sh, StartInterval=60
│   │   ├── com.bets.health.plist       LaunchAgent: bets.health watcher
│   │   └── com.bets.databackup.plist   LaunchAgent: 3am mirror of data/ to bets-data-backup repo
│   └── legacy/                     retired 2026-05-16 when Air went Tailscale-only:
│       ├── Caddyfile                   served public output/ via Cloudflare Tunnel
│       ├── com.bets.caddy.plist
│       └── com.bets.cloudflared.plist
├── bets/                           package
│   ├── __init__.py
│   ├── config.py                   constants: blend weights, park factors, lineup PA, paths
│   ├── fetch.py                    MLB Stats API (starters + lineups + pitcher/hitter stats); Baseball Savant SwStr% (12h disk cache)
│   ├── odds.py                     The Odds API: pitcher_strikeouts + batter_strikeouts, multi-book aggregation, line preservation across reruns, quota-header logging (writes output/odds_api_usage.json)
│   ├── model.py                    v0/v1/v2 pitcher projections + v0 hitter projection; Poisson P(over); odds + EV math
│   ├── main.py                     CLI: project today's pitcher slate; freezes first run as the day's slate snapshot
│   ├── hitters.py                  CLI: project today's hitter slate (separate runner for clarity)
│   ├── settle.py                   Settle yesterday with actuals + slate-time fields (slate_edge/line/over_hit/pnl) for honest pick grading
│   ├── analyze.py                  Aggregate settled history (pitcher only so far)
│   ├── live.py                     Slate-pitcher list + live K-count + pitches + IP from MLB Stats API boxscore + schedule (60s in-memory cache); also surfaces is_home for vs/@ rendering
│   ├── wagers.py                   Personal bet ledger: load/save/CRUD on data/bets.json; legs[] schema; totals exclude free entries; per-site by_site breakdown
│   ├── notify.py                   Optional Pushover notifications: bet settle (server-fired) + pulled-starter / one-to-go alerts (live-ks-piggyback). No-op without PUSHOVER_TOKEN/PUSHOVER_USER in .env
│   ├── web.py                      HTML+JS dashboard shell (client-side rendered); same HTML for Air/laptop, runtime hostname check toggles dev-only affordances
│   ├── server.py                   Flask server (port 8000); GET / serves shell + output/ CSVs as static; /api/bets, /api/slate-pitchers, /api/live-ks for the Bets tab; /refresh runs bets.main and (with BETS_AUTO_PUSH=1) commits+pushes atomically
│   ├── auth.py                     Multi-user login, per-user data/users/<id>/ namespacing
│   └── health.py                   Watcher (launchd): staleness checks + in-process retry via local /refresh
├── data/                           gitignored — caches + per-user ledgers:
│   ├── swstr_<season>.json
│   └── users/<id>/bets.json        personal bet ledger (NEVER committed)
└── output/                         tracked — Air writes here, laptop gitpulls for read-only sync:
    ├── pitcher_ks_<date>.csv               live state, overwritten on each run
    ├── pitcher_ks_<date>_slate.csv         frozen first-run snapshot for grading
    ├── pitcher_ks_<date>_settled.csv       projections + actuals + slate_* fields
    ├── hitter_ks_<date>.csv
    ├── hitter_ks_<date>_settled.csv
    ├── odds_api_usage.json                 latest Odds API quota snapshot + per-call history (powers the header pill)
    ├── icon-128.png                        128×128 PNG of the favicon (for Pushover application icon upload)
    ├── apple-touch-icon.png                180×180 PNG used by iOS Safari "Add to Home Screen"
    ├── icon-192.png, icon-512.png          PWA manifest icons (Android Chrome / generic)
    ├── manifest.webmanifest                PWA manifest — makes the site installable as a home-screen app
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

The local Flask server is for **testing code changes before they hit Air's Tailscale dashboard** — the Air pulls every 60s, so any push lands on the live dashboard within a minute. Iterate locally first.

```sh
cd ~/bets
.venv/bin/python -m bets.server   # http://127.0.0.1:8000
```

Override the port if 8000 is taken: `BETS_PORT=5050 python -m bets.server`. macOS reserves 5000 for AirPlay Receiver, which is why 8000 is the default.

**Workflow:**

1. Edit code in `~/bets/bets/`
2. Restart the server (Ctrl+C, then re-run) to pick up Python changes — browser hard-refresh (Cmd+Shift+R) is enough for HTML/CSS only
3. Verify the change at `http://127.0.0.1:8000`
4. Commit + push **only when satisfied** — within ~1 minute the M1 Air's `git pull` cron picks it up and the Tailscale dashboard updates

**Why localhost and Air's Tailscale URL can show different data:** `bets/web.py:baseUrl()` swaps the CSV fetch root based on `location.hostname`. On `localhost`/`127.0.0.1` it fetches `./pitcher_ks_*.csv` from the laptop's Flask filesystem (so unpushed working-tree CSVs show up). On the Tailscale URL it fetches from `raw.githubusercontent.com` (so only pushed CSVs show up). If hero cards or stats differ between the two surfaces, you almost certainly have unpushed CSV changes locally.

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

### Odds API quota pill

A pill in the header (`Odds API: 188/20000 · +9 today`) tracks usage against the active plan's monthly cap. The `used/cap` figures come from `x-requests-remaining` / `x-requests-used` headers The Odds API returns on every response, so they're authoritative — captured by `_log_quota` in `bets/odds.py` after each fetch and persisted to `output/odds_api_usage.json` (latest snapshot + per-call history capped at 500 entries). The `+N today` segment is computed locally from the log and answers "did this re-run consume too many credits?" — useful for confirming the skip-covered-games optimization is doing its job (a same-day re-run after every starter is priced should bump it by 0). Pill turns yellow at ≥75% used, red at ≥90%. Hover for last-update time and breakdown. Note: `/events` calls cost 0 credits; only the per-event `/odds` calls bill against quota.

### Time + live status column

Each row's Time cell does triple duty depending on game state:

- **Pre-game**: `7:10 PM CT (in 47m)`. The relative countdown updates every minute; the suffix turns yellow/bold under 30 minutes.
- **Live**: `● B5 4K` — pulsing red dot, compact half-inning (`B5` = bottom 5th, `T3` = top 3rd, etc.), running K count.
- **Pulled** (game still in progress, starter replaced): `Pulled · 6K`. Detected by checking each team's `pitchers[]` array in the boxscore — if the starter isn't the last entry they've been replaced, and their K count is locked. The over/under verdict can be called immediately without waiting for Final.
- **Final**: `Final · 7K`.

Once first pitch passes the row dims to 55% opacity (it's locked from the bet window). Live K + game status are pulled directly from the public MLB Stats API in JS — works on the public URL and locally without a proxy. The fetch auto-polls every 60s (paused while the browser tab is hidden, self-stops once every game has gone Final or had its starter pulled), so K counts and inning state update without clicking **Refresh data**.

### Today's Picks hero cards

Each focus pick gets a card at the top with `BET OVER 6.5` (or UNDER), the model edge, our projection, our %, and a **live status box** showing the same data as the row's time cell. While the game is in progress the live box also surfaces a `5 of 6.5` pace label.

**Pulse line**: while the pitcher is actively throwing (status `Live` and not yet pulled), a thin line under the stats row shows `92 P · 5.2 IP` so you can sense workload + how much of the start is left. Hidden pre-game, on pull, and at Final so it never lingers on stale data. Field source is the same MLB boxscore call already running — no extra API hits.

**Projection tooltip**: hovering "Our Proj" reveals the v0/v1/v2/ML breakdown so you can eyeball where the v2 number came from and how shadow ML compares.

**Outcome coloring**: once a card's pick is mathematically settled — mid-game (`ks > line` permanently locks the verdict, or the starter has been pulled with Ks ≤ line) or at Final — the card flips to a **solid saturated fill** (deep green for HIT, deep red for MISS) with white text and a **full-width ✓ HIT / ✗ MISS banner** across the top, regardless of the original direction. The glyph prefix gives a color-blind-safe redundancy on top of the green/red fill. Pre-settle cards keep the subtle pale tint, so the flip is unmistakable on a slate of mostly-pending cards. The original `BET OVER 6.5` pill stays visible in the header so you can see the bet you placed.

### Parlay Suggestions

Below the hero picks, a **Parlay Suggestions** section ranks the top 5 two-leg and top 3 three-leg combinations of today's focus picks by **EV per $1**. Each card shows the leg list (direction badge · pitcher · line · game time) and a stats row (combined Payout · Hit % · Edge · EV / $1). Cards get a green left-border for +EV, red for −EV.

Combined probabilities assume leg independence — fine for K props since two pitchers in the same game face *different* lineups. Eligible legs are focus-band only; investigate / noise picks are excluded.

When running locally, each card has a **+ Add to bets** button that switches to the Bets tab and pre-populates a new parlay form with the suggested legs. The button is hidden on the public URL.

### Yesterday's Results report card

A summary card sits above the per-pitcher results table showing **W–L record on focus picks**, **net units (1u flat)**, and **hit rate**. Picks are graded against the **slate-time** line + edge (the morning state, not whatever survived to gametime), so the verdict reflects what you'd actually have bet at. Full per-pitcher table below shows Off By, Line, Our Pick badge, and HIT/MISS verdict for actionable picks (informational OVER hit/UNDER hit for non-bets).

### Track Record (last 14 days)

A rolling-window section below Yesterday's Results aggregates focus picks across the available settled days:
- Top stats: Picks · Hit rate · Units · ROI (with week-over-week trend arrows once 8+ picks accumulate)
- SVG sparkline of cumulative units (auto-scales, fills green/red below the zero line). **Hover any day** for a tooltip with date, day units (signed/colored), W–L, and cumulative total to that point.
- OVER/UNDER split panel showing share + per-side W-L + units
- **ROI by edge bucket** — every graded pick (focus + below-threshold) bucketed by `|edge|` into 0–2% / 2–5% / 5–10% / 10%+ rows with picks · hits · hit% · units · ROI. Sanity check that higher-edge picks actually pay off, and tells you where to set your threshold.
- **Calibration scatter** — projected v2 K vs actual K with a 45° reference line. Mean residual + RMSE printed above the chart so model bias jumps out (positive residual = model under-projecting, negative = over-projecting). Dot opacity fades by recency so a recent drift is spottable.
- Per-day breakdown table

Slate-time fields (`slate_edge`, `slate_line`, `slate_over_hit`, etc.) are added by `settle.py` when a `_slate.csv` snapshot exists for the date. Older settled rows fall back to live/final-state fields gracefully.

### Bets tab (local only)

A **personal parlay ledger** for tracking actual DFS bets, gated behind per-user login and Tailscale (no public exposure). Backed by `data/users/<id>/bets.json` which is gitignored.

- **Structured parlay entry** with leg-count selector (2–6 legs, matching DFS-site minimums). Each leg has a pitcher picker (auto-fills from today's slate including model recommendation), a per-leg line override (DFS lines often differ from sportsbook), and an O/U toggle.
- **Live Combined stats panel** above the stake/odds inputs: recomputes Payout, Hit %, Edge, EV per $1, and Profit-if-hit on every leg-state change (pitcher select, line input, O/U toggle, leg-count, stake). Auto-fills the Odds field with the parlay decimal — once you type into Odds yourself, your value sticks. Reading from the slate's `p_over` / `novig_over`, so the math you see in the editor matches what the Pitcher-tab Parlay Suggestions show.
- **+ Add to bets** handoff: clicking a parlay-suggester card jumps to the Bets tab with the suggested legs already filled in and the Combined panel showing the same numbers.
- **Live K tracking** per leg: queries MLB Stats API boxscore + schedule via `/api/live-ks` (60s in-memory cache). Each leg shows `5 K ✓ (B5)` style — K count + a single-glyph verdict (`✓` hit / `✗` miss) + compact inning tag (`B5` = Bottom 5th). The tab auto-polls every 60s (paused on hidden browser tab, self-stops once every linked pitcher has gone Final or been pulled) so K counts update without clicking **Refresh live**; the manual button still works for an on-demand pull.
- **Mid-game lock-in**: once `ks > line`, an OVER bet locks as hit and an UNDER bet locks as miss regardless of game state — Ks can only increase. The opposite cases (over not yet reached, under still alive) lock the moment the starter is **pulled** (detected via the boxscore `pitchers[]` array — once the starter isn't the last entry, their Ks won't change), or otherwise wait for game final. Verdict badges include a `(pulled B5)` tag when the lock-in came from a pitching change.
- **Ticket-card view**: each bet renders as a card showing date · leg count · site/free/boost badges · stake → payout · odds, with the full leg list always visible below. Today's bets sit at the top; everything else is hidden behind a "Show N older bets" toggle. Tap a card to reveal an action drawer with Mark Win / Mark Loss / Edit / Reopen / Delete (settled cards show Edit / Reopen / Delete). On a laptop the cards lay out two-up via `grid-template-columns: repeat(auto-fit, minmax(380px, 1fr))`; phones auto-collapse to a single column.
- **Card-level status tint**: the card's background and border color reflect the aggregated live verdict — green when every leg has hit, red as soon as any leg busts, yellow while any leg is still pending with no miss. A manual Mark Win / Mark Loss overrides the live tint (same colors, applied directly from `b.result`). No banner — the per-leg glyphs convey the detail.
- **Auto-settle** on definitive verdicts: when a parlay is locked Win or Loss, the bet's W/L is automatically updated and payout calculated (`stake × odds` for W, `0` for L). User can override with Reopen.
- **Free-entry flag**: tickets marked as free entries are excluded from `staked` and `ROI` totals (their winnings still count toward `returned`). Shown separately on a secondary totals line.
- **Per-site P&L row**: under the totals strip, a "By site:" line breaks out tickets · W–L–pending · net · ROI for each of PP / UD / DK separately, so you can see whether one site is actually paying off differently. Tickets with no site tag are excluded from the row.
- **Mobile quick-status strip** (≤600px only): a compact band above the toolbar shows one tappable row per still-pending bet, one chip per leg. Each chip leads with the wagered side + line (`U7.5`, `O8.5`) so the K count reads against what was bet, then appends `5K · 87P · 4th` while live or settles to `5K ✓` / `9K ✗` once `legHitState` locks in. Chip color = verdict (green/red/yellow/gray); tooltip carries the longer state. Tap a row to expand + scroll to the matching ledger card. Strip is hidden on desktop.

The Flask server's `/api/bets` (CRUD), `/api/slate-pitchers`, and `/api/live-ks` routes serve the tab. Access is gated by Tailscale — only tailnet members reach the Flask URL at all, and per-user login further scopes the Bets tab to its owner.

### Install as an iPhone app (PWA)

The dashboard ships a web app manifest + apple-touch icons, so it installs from Safari as a standalone home-screen app — no App Store, no Apple Developer account, no Xcode.

1. Open the **Tailscale URL** for the Air's Flask in Safari on your iPhone (the Tailscale app must be active on the phone).
2. Tap **Share → Add to Home Screen**. Default name is "K Props".
3. Tap the new icon — it opens full-screen with no Safari chrome, dark theme matching the dashboard, and your phone treats it like any other app (icon on the home screen, lives in the app switcher).

**Refresh patterns:** in-app **Refresh data** / **↻ Refresh live** buttons reload data only. Pull-to-refresh from the top of the screen does a full page reload (iOS 16+). After a deploy that changes UI, force-quit + re-open the app via the app switcher to guarantee a fresh load of HTML/JS.

**Files involved:** `output/manifest.webmanifest` declares the app; `output/apple-touch-icon.png` (180×180) is what iOS pins to the home screen; `icon-192.png` / `icon-512.png` are referenced by the manifest for non-iOS contexts. Flask serves these as static files from `output/`.

### Pushover notifications (optional, local-only)

When `PUSHOVER_TOKEN` and `PUSHOVER_USER` are set in `.env`, the local Flask server fires push notifications to your phone for three events:

- **Bet settled** — every transition from pending → W/L through `PUT /api/bets/<id>` (covers manual marks and the JS auto-settle). Title shows net P&L; body lists the legs.
- **Pulled starter** — when a pitcher on a still-pending bet leg is pulled mid-game (`done=true` and game not yet Final), so you immediately know whether the leg busted or held instead of staring at a frozen K count.
- **Parlay one-to-go** — when a multi-leg parlay has all-but-one legs hit, exactly one pending, and zero misses. For OVER picks it computes how many more Ks are needed; both O/U include current K count and inning.

Setup: create a free Pushover account at https://pushover.net, install the iOS/Android app and log in, then create an Application/API token (`Apps & Plugins → Create an Application/API Token`). Add both keys to `.env`:

```sh
PUSHOVER_TOKEN=your-app-api-token
PUSHOVER_USER=your-user-key
```

Restart the Flask server. If either env var is missing the notification calls are silent no-ops, so the dashboard works unchanged without Pushover configured.

The pulled-starter and one-to-go alerts piggyback on the dashboard's `/api/live-ks` 60s poll (the dashboard tab needs to be open). The bet-settled alert fires on the API call regardless of which tab is open. Dedup state for the live alerts lives in `data/notify_state.json` (gitignored, prunes entries older than 7 days), one fire per event per day. A 128×128 PNG of the favicon ships at `output/icon-128.png` for upload as the Pushover application icon.

## Deployment (self-hosted on M1 Air)

The dashboard runs on a dedicated **M1 MacBook Air** sitting on the home network, accessed exclusively over **Tailscale** — no public internet exposure. Architecture (as of 2026-05-16):

1. **The M1 Air** is the canonical pipeline writer. Four LaunchAgents run there:
   - `com.bets.flask` runs `python -m bets.server`, served on Tailscale. `/refresh` runs the pipeline; with `BETS_AUTO_PUSH=1` set in `.env`, it also commits and pushes `output/` atomically under the same lock, so the 60s gitpull cron never sees a dirty working tree mid-refresh.
   - `com.bets.gitpull` runs `ops/git-pull.sh` every 60s. Mostly a no-op on the Air (it's the writer), but useful for picking up code changes pushed from the laptop.
   - `com.bets.health` periodically checks slate/lineups/swstr freshness; on staleness it POSTs to the local `/refresh` for an in-process retry (no GH Actions round-trip), with Pushover alert after `RETRY_CAP=3` failures.
   - `com.bets.databackup` runs at 3am, mirroring `data/` (gitignored) into the private `bets-data-backup` repo so the SwStr cache, residuals scratch, and `health_state.json` survive disk loss.
2. **The laptop** is the dev/escape-hatch host. It runs only `com.bets.gitpull` (60s) for read-only sync of `output/`, useful for offline browsing and manual `bets.main` runs (which hit the `all_covered` short-circuit and burn 0 Odds API credits). To preview UI changes mid-dev, run `python -m bets.server` manually — `/refresh` on the laptop does NOT auto-push because `BETS_AUTO_PUSH` is not set.
3. **GH Actions** keeps the `Refresh dashboard` workflow as a manual disaster fallback — fire it from your laptop if the Air is wedged.

The dashboard fetches CSVs from `https://raw.githubusercontent.com/chirsch95/bets/main/output/*.csv` on each page load, so the Flask host doesn't need to re-serve them; updates appear within ~30s of the commit landing on GitHub.

### Network + access architecture

- **Tailscale** is installed on the Air, the main laptop, and any phones/devices that need dashboard access. SSH into the Air uses the alias `bets-host` (`~/.ssh/config` maps it to the Tailscale IP). Works from any network — the Air doesn't need to be on the same wifi.
- The Air sits on the **Asus router's guest VLAN** (Access Intranet disabled), so even if it's compromised it can't reach other devices on the home LAN.
- The router has no inbound port forwarding. There's no public surface — `trycloudflare.com` and Caddy were retired 2026-05-16; their configs live in `ops/legacy/` for reference.
- The Air is configured to **never sleep** (`pmset disablesleep 1` + Energy Saver tweaks), so the lid can be closed without taking the dashboard down. Auto-login is on so LaunchAgents start without manual intervention after reboots.

### Auth: git push from Air

The Air's deploy key is registered on the GitHub repo with **Allow write access** enabled. Air's local repo config sets `user.name = bets-host` and `user.email = bets-host@local` so auto-refresh commits are visibly distinct from manual laptop commits in `git log`.

### One-time setup

```sh
# 1. Initialize git locally (if not already)
git init -b main
git add .
git commit -m "initial commit"

# 2. Create a GitHub repo and push (gh CLI shown; web UI also works)
gh repo create bets --public --source=. --remote=origin --push

# 3. Add the Odds API key as a GitHub Actions secret (for the disaster-fallback workflow)
gh secret set ODDS_API_KEY     # paste your key when prompted

# 4. Set up the M1 Air host:
#    - Install Tailscale, sign in (same account as your laptop and phone)
#    - Generate an SSH deploy key on the Air, add it to GitHub repo settings with "Allow write access" checked
#    - git clone the repo into ~/bets
#    - Copy .env (must include ODDS_API_KEY and BETS_AUTO_PUSH=1)
#    - git config user.name "bets-host" && git config user.email "bets-host@local"  (in ~/bets)
#    - launchctl bootstrap each plist in ops/launchd/
#    - pmset/Energy-Saver to never sleep, auto-login enabled
```

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
4. Commit + push — the M1 Air will pick up the new `index.html` on its next 60s `git pull` and the Tailscale dashboard will show the hitter tab

That's it. The model code, settle path, and CSV format are all preserved untouched. Note: re-enabling adds another full per-game odds call to each pipeline run (`batter_strikeouts` market) — roughly doubles the per-run credit cost. With manual-only pulls this is manageable but worth keeping in mind before triggering many re-runs in a day.

**Pipeline + UI**
- ✅ The Odds API integration with multi-book aggregation: median line, best odds per side with sourcing book, median no-vig P(over)
- ✅ Line preservation across same-day reruns (`load_previous_*_lines` + `merge_lines`) so a late run doesn't wipe morning lines when books pull markets
- ✅ Calibration harness: settle vs actual outcomes, MAE / RMSE / bias for v0 / v1 / v2 head-to-head, P(over) buckets, edge-threshold ROI
- ✅ HTML dashboard with focus highlighting, OVER / UNDER recommendations, Recent Results section. Currently single-tab (Pitcher Ks); tabbed layout returns when hitters are re-enabled.
- ✅ Flask server (port 8000) on Air with Re-run pipeline / Settle Yesterday / Push buttons; `/refresh` auto-commits+pushes when `BETS_AUTO_PUSH=1` is set
- ✅ **Self-hosted dashboard on M1 Air, Tailscale-only as of 2026-05-16.** The Cloudflare Tunnel + Caddy public path was retired; everything (read + bets) is served by Flask over Tailscale. Pipeline runs on Air; laptop is dev/escape-hatch.

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
