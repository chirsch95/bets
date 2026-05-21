"""Static HTML dashboard generator (client-side rendered).

Generates a thin HTML shell with embedded JavaScript. The JS fetches CSV
files at page-load:
  - On localhost / 127.0.0.1: from same origin (`./...csv`) — Flask
    server serves them from output/.
  - On any other host (the public URL): from
    `https://raw.githubusercontent.com/chirsch95/bets/main/output/...csv`.

Rendering happens client-side, so the M1 Air host (which serves only this
shell via Caddy + Cloudflare Tunnel) never has to re-render or rebuild
when CSVs change — the browser pulls fresh CSVs straight from GitHub raw
on every page load. The Air's 60s `git pull` cron picks up shell changes
(`output/index.html`) within ~1 minute of any push.

Run with:
    python -m bets.web              # today
    python -m bets.web 2026-04-30   # specific date (only affects header label)
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

from .config import OUTPUT_DIR

REPO = "chirsch95/bets"
BRANCH = "main"

# Hitter-Ks pipeline is paused (2026-05-01) to conserve Odds API quota
# while the pitcher model accumulates calibration data. Flip to True once
# pitcher Ks are validated and you're ready to re-enable. The Python
# pipeline code (bets/hitters.py, model.py:project_hitter_ks_v0,
# settle.py:settle_hitters_date) is kept intact — flipping this back on
# plus re-adding the workflow step + server route is all you need.
SHOW_HITTERS = False

# Edge bands. Mirror values used by the JS classifier — keep in sync with
# live.py + parlay_suggest.py. Applied against CALIBRATED edge (cal_edge_v2)
# under Path C — see pickEdge() helper in the JS below and project_path_c memory.
FOCUS_EDGE_MIN = 0.065
FOCUS_EDGE_MAX = 0.15
INVESTIGATE_EDGE = 0.20
# Minimum line to consider a pitcher as a focus pick. Real starters are
# almost never priced below 3.5 K; lines at or under 2.5 K signal the
# book treating the pitcher as an opener / reliever / spot appearance.
# Our model has no role-detection — it pulls season K% and multiplies by
# season-average BF, so it mis-projects relievers as full starts (e.g.,
# Brazobán 2026-05-16: line 0.5, our proj 5.5). The gate filters these
# from focus picks and parlay suggestions; they still show in the table.
MIN_LINE_FOR_FOCUS = 3.0


CSS = """
  :root {
    --bg: #0a1628;
    --panel: #142336;
    --text: #e6f0ff;
    --muted: #8aa0bd;
    --border: #1f3251;
    --brand-blue: #2ab8e6;
    --brand-green: #5dfa7a;
    --green: #4ade80;
    --green-bg: rgba(74, 222, 128, 0.1);
    --green-solid: #15803d;
    --red: #f87171;
    --red-bg: rgba(248, 113, 113, 0.1);
    --red-solid: #b91c1c;
    --yellow: #fbbf24;
    --yellow-bg: rgba(251, 191, 36, 0.1);
    /* Subtle overlays used for hover/header tints. White-on-dark in
       dark mode; flipped to black-on-light below. */
    --hover-overlay: rgba(255,255,255,0.04);
    --header-tint: rgba(255,255,255,0.02);
  }
  :root[data-theme="light"] {
    --bg: #ffffff;
    --panel: #ffffff;
    --text: #1a1d24;
    --muted: #6b7280;
    --border: #e2e5eb;
    --green: #16a34a;
    --green-bg: rgba(22, 163, 74, 0.12);
    --green-solid: #15803d;
    --red: #dc2626;
    --red-bg: rgba(220, 38, 38, 0.12);
    --red-solid: #b91c1c;
    --yellow: #d97706;
    --yellow-bg: rgba(217, 119, 6, 0.14);
    --hover-overlay: rgba(0,0,0,0.05);
    --header-tint: rgba(0,0,0,0.03);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    padding: 16px 32px;
    border-bottom: 1px solid var(--border);
  }
  /* New header layout: big logo anchored on the far left, everything
     else stacked in a right column (topbar with tabs + utility cluster,
     scoreboard panel, status strip). Falls back to the original stacked
     layout on phones/tablets so nothing breaks below 1100px. */
  @media (min-width: 1101px) {
    header {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      grid-template-areas: "logo  right";
      column-gap: 28px;
      align-items: stretch;
    }
    header > .brand-area   { grid-area: logo; }
    header > .header-right { grid-area: right; }
  }
  @media (max-width: 1100px) {
    header > .header-right > .header-scoreboard { display: none; }
  }
  header .brand-area {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 0;
  }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header h1.brand { margin: 0; padding: 0; line-height: 0; font-size: 0; }
  header .brand-logo {
    height: 144px;
    width: auto;
    display: block;
    filter: drop-shadow(0 2px 14px rgba(45, 212, 255, 0.22));
  }
  header .brand-logo-light { display: none; }
  :root[data-theme="light"] header .brand-logo-dark { display: none; }
  :root[data-theme="light"] header .brand-logo-light { display: block; }
  /* Tabs + theme/refresh/admin cluster sits directly under the logo on
     the far left. Centered horizontally against the logo so the brand
     block reads as one composed unit. Wraps to a second row on very
     narrow phones rather than overflowing. */
  header .brand-actions {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  @media (max-width: 768px) {
    header { padding: 12px 16px; }
    header .brand-logo { height: 96px; }
    header .brand-area { gap: 10px; }
  }
  @media (max-width: 400px) {
    header .brand-logo { height: 76px; }
    header .brand-area { gap: 8px; }
  }
  /* Right column inside the header: scoreboard up top, status strip
     at the bottom. (Tabs used to live here in a topbar wrapper; they
     now sit under the logo inside .brand-area.) */
  header .header-right {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }
  /* Theme + refresh + admin icons. Originally absolute-positioned in
     the corners of the centered brand block — now they sit inline in
     the utility cluster, so we reset the positioning rules. */
  header .float-btn {
    position: static;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    height: 36px;
    min-width: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    cursor: pointer;
    color: var(--text);
    padding: 0;
    font-family: inherit;
  }
  header .float-btn:hover { background: var(--hover-overlay); }
  header .float-btn svg { width: 16px; height: 16px; }
  header .float-btn.theme-btn { width: 36px; }
  header .float-btn.refresh-btn {
    width: 36px;
    background: var(--green);
    border-color: var(--green);
    color: #001a00;
  }
  header .float-btn.refresh-btn:hover {
    background: var(--green);
    filter: brightness(1.08);
  }
  header form.settle-form { display: contents; }
  header .float-btn.settle-btn {
    width: 36px;
    background: var(--brand-blue);
    border-color: var(--brand-blue);
    color: #001a22;
  }
  header .float-btn.settle-btn:hover {
    background: var(--brand-blue);
    filter: brightness(1.08);
  }
  /* Force re-fetch lives in the status-row (right side on desktop, below
     brand-area on phone), NOT in the brand-actions cluster, so accidental
     phone taps near the green Refresh button can't fire it. Amber outline
     to signal "this one's different." */
  header .force-refresh-form { display: contents; }
  .status-row .force-refresh-btn {
    height: 22px;
    min-width: 28px;
    padding: 0 6px;
    border-radius: 6px;
    background: transparent;
    border: 1px solid #d97706;
    color: #d97706;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: inherit;
  }
  .status-row .force-refresh-btn:hover { background: rgba(217, 119, 6, 0.12); }
  .status-row .force-refresh-btn svg { width: 13px; height: 13px; }
  .status-row .force-refresh-btn:disabled { opacity: 0.5; cursor: wait; }
  header .float-btn.loading svg { animation: ptr-spin 0.8s linear infinite; }
  header .float-btn:disabled { opacity: 0.5; cursor: wait; }
  header .float-btn .theme-sun { display: block; }
  header .float-btn .theme-moon { display: none; }
  :root[data-theme="light"] header .float-btn .theme-sun { display: none; }
  :root[data-theme="light"] header .float-btn .theme-moon { display: block; }
  @media (max-width: 480px) {
    header .float-btn { height: 32px; min-width: 32px; }
    header .float-btn.theme-btn { width: 32px; }
    header .float-btn.refresh-btn { width: 32px; }
  }
  /* Admin overflow (local-only). Native <details> for zero-JS dropdown. */
  header .admin-menu {
    position: relative;
    align-self: auto;
    margin-top: 0;
  }
  header .admin-menu summary {
    list-style: none;
    cursor: pointer;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0 10px;
    font-size: 16px;
    line-height: 1;
    color: var(--muted);
    user-select: none;
    height: 26px;
    display: inline-flex;
    align-items: center;
    font-family: inherit;
  }
  header .admin-menu summary::-webkit-details-marker { display: none; }
  header .admin-menu summary::marker { content: ""; }
  header .admin-menu summary:hover { color: var(--text); border-color: var(--muted); }
  header .admin-menu[open] summary { color: var(--text); border-color: var(--muted); }
  header .admin-items {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 180px;
    z-index: 5;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  }
  header .admin-items form { margin: 0; }
  header .admin-items button {
    width: 100%;
    text-align: left;
    background: transparent;
    border: 1px solid transparent;
    color: var(--text);
    padding: 7px 10px;
    font-size: 12px;
    cursor: pointer;
    border-radius: 5px;
    font-family: inherit;
  }
  header .admin-items button:hover { background: var(--hover-overlay); }
  body.loading { cursor: progress; }
  body.loading header .float-btn,
  body.loading header .admin-items button { opacity: 0.5; cursor: wait; }
  /* Two visibility scopes set by the head script from location.hostname:
     - .local-only: laptop only (⋯ admin menu: re-run pipeline / settle / push)
     - .bets-only:  Air's Tailscale URL (bets tab, settle button, add-to-bets) */
  .local-only { display: none; }
  html.is-local .local-only { display: revert; }
  .bets-only { display: none; }
  html.is-bets .bets-only { display: revert; }
  /* Status row: last-refresh + quota + health collapsed into a single
     line with `·` separators. Each child keeps its own .visible toggle —
     the ::before separator inherits display from its host so it hides
     automatically when the pill isn't loaded. */
  .status-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-start;
    gap: 0;
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    line-height: 1.4;
    margin-top: 0;
  }
  .status-row > * + *::before {
    content: "·";
    margin: 0 7px;
    color: var(--border);
  }
  .last-refresh { color: var(--muted); font-size: 11px; }
  .last-refresh strong { color: var(--text); font-weight: 500; }
  .quota-pill {
    display: none;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--panel);
    color: var(--muted);
    font-size: 11px;
    line-height: 1;
    cursor: help;
  }
  .quota-pill.visible { display: inline-flex; }
  .quota-pill strong { color: var(--text); font-weight: 600; }
  .quota-pill .quota-today { color: var(--muted); }
  .quota-pill.warn { border-color: var(--yellow); color: var(--yellow); }
  .quota-pill.warn strong { color: var(--yellow); }
  .quota-pill.danger { border-color: var(--red); color: var(--red); }
  .quota-pill.danger strong { color: var(--red); }
  /* Inside the unified status row: strip pill chrome so the line reads
     as plain inline text separated by `·`. Color/strong tokens still
     carry warn/danger meaning. */
  .status-row .quota-pill {
    border: none;
    background: transparent;
    padding: 0;
    border-radius: 0;
    font-size: 11px;
    cursor: help;
  }
  /* Health pill: same shape as quota-pill, fed by GET /api/health.
     Hidden until loadHealth() succeeds — on the public (Caddy-static)
     deploy the API doesn't exist so the pill never reveals
     (consistent with .local-only / .bets-only being scope-aware UI). */
  .health-pill {
    display: none;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--panel);
    color: var(--muted);
    font-size: 11px;
    line-height: 1;
    cursor: help;
  }
  .health-pill.visible { display: inline-flex; }
  .health-pill .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--muted);
  }
  .health-pill.ok { border-color: var(--green); color: var(--green); }
  .health-pill.ok .dot { background: var(--green); }
  .health-pill.warn { border-color: var(--yellow); color: var(--yellow); }
  .health-pill.warn .dot { background: var(--yellow); }
  .health-pill.danger { border-color: var(--red); color: var(--red); }
  .health-pill.danger .dot { background: var(--red); }
  /* Inside the status row: drop the pill border to match quota-pill;
     keep the dot + colored text so health state still reads at a glance. */
  .status-row .health-pill {
    border: none;
    background: transparent;
    padding: 0;
    border-radius: 0;
    font-size: 11px;
    cursor: help;
  }
  /* Pull-to-refresh indicator. Standalone PWAs disable Safari's native
     overscroll PTR, so we render our own. Hidden by default; the JS
     translates it down with the finger and snaps to a fixed spot when
     refreshing. Stays out of the document flow (position: fixed) so it
     can never push content around. */
  .ptr-indicator {
    position: fixed;
    top: 0;
    left: 50%;
    width: 36px;
    height: 36px;
    margin-left: -18px;
    border-radius: 50%;
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--muted);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    transform: translateY(-60px);
    opacity: 0;
    pointer-events: none;
    transition: transform 0.25s ease, opacity 0.2s ease;
    -webkit-user-select: none;
  }
  .ptr-indicator.dragging { transition: none; }
  .ptr-indicator.ready { color: var(--green); border-color: var(--green); }
  .ptr-indicator.refreshing svg { animation: ptr-spin 0.8s linear infinite; }
  @keyframes ptr-spin { to { transform: rotate(360deg); } }
  /* Segmented toggle (Tailscale URL only) — compact pill replacement
     for the old tab strip. Hidden by default; html.is-bets reveals it
     where Bets data is canonical. The active button gets a green fill,
     inactive sits transparent against the panel-toned pill container.
     Sits inline with the theme/refresh/admin buttons in .brand-actions
     under the logo. */
  .segmented {
    display: none;
    margin: 0;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 3px;
    width: max-content;
    gap: 2px;
  }
  html.is-bets .segmented { display: flex; }
  .segmented button {
    background: transparent;
    border: none;
    color: var(--muted);
    padding: 5px 16px;
    font-size: 12px;
    font-weight: 600;
    border-radius: 999px;
    cursor: pointer;
    font-family: inherit;
    white-space: nowrap;
    transition: background 0.15s, color 0.15s;
  }
  .segmented button:hover { color: var(--text); }
  .segmented button.active {
    background: var(--green);
    color: #001a00;
  }
  /* Header scoreboard — twin "Model" + "Bankroll" panel on the right
     half of the header. Hidden on mobile (handled by header media
     rule). Built from existing track-record + /api/bets data, so no
     extra fetches. */
  .header-scoreboard {
    position: relative;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    padding: 6px 0 12px;
    background:
      linear-gradient(135deg, rgba(45, 212, 255, 0.06), rgba(74, 222, 128, 0.06));
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    min-height: 0;
  }
  .header-scoreboard::before {
    /* Soft accent glow at the top edge — pulls the eye to the panel
       without competing with the data. */
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, var(--green), transparent);
    opacity: 0.4;
  }
  .scoreboard-col {
    position: relative;
    padding: 10px 16px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
    min-width: 0;
  }
  .scoreboard-col + .scoreboard-col {
    border-left: 1px solid var(--border);
  }
  .scoreboard-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .scoreboard-label .scoreboard-window {
    color: var(--text);
    font-weight: 600;
    opacity: 0.7;
  }
  .scoreboard-live-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--red);
    animation: scoreboard-live-pulse 1.6s ease-in-out infinite;
  }
  @keyframes scoreboard-live-pulse {
    0%, 100% { opacity: 0.4; transform: scale(1); }
    50%      { opacity: 1;   transform: scale(1.3); }
  }
  .scoreboard-hero {
    font-size: 26px;
    font-weight: 800;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
    margin-top: 2px;
  }
  .scoreboard-hero.pos {
    color: var(--green);
    text-shadow: 0 0 16px rgba(74, 222, 128, 0.35);
  }
  .scoreboard-hero.neg {
    color: var(--red);
    text-shadow: 0 0 16px rgba(248, 113, 113, 0.35);
  }
  .scoreboard-hero.flat { color: var(--muted); }
  .scoreboard-spark {
    margin: 2px 0 4px;
    width: 100%;
    height: 28px;
    display: block;
    overflow: visible;
  }
  .scoreboard-spark-area {
    fill-opacity: 0.18;
    stroke: none;
  }
  .scoreboard-spark-area.pos { fill: var(--green); }
  .scoreboard-spark-area.neg { fill: var(--red); }
  .scoreboard-spark-area.flat { fill: var(--muted); }
  .scoreboard-spark-path {
    fill: none;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    /* No dasharray draw-in animation: pathLength + vector-effect:
       non-scaling-stroke + stroke-dasharray interact badly (dash effects
       are computed in screen space, which silently ignores pathLength
       and renders the line as a fragmented dot pattern). The path
       just appears solid; tip dot still fades in for "freshness" cue. */
  }
  .scoreboard-spark-path.pos  { stroke: var(--green); }
  .scoreboard-spark-path.neg  { stroke: var(--red); }
  .scoreboard-spark-path.flat { stroke: var(--muted); }
  .scoreboard-spark-tip {
    /* Last-point marker. Drawn as a 0-length stroked path with round
       linecap so the marker stays a true circle on screen even though
       the parent SVG has preserveAspectRatio="none" (which would
       distort a <circle> into an ellipse). */
    fill: none;
    stroke-width: 5;
    stroke-linecap: round;
    opacity: 0;
    animation: scoreboard-spark-tip-fade 0.5s ease 0.1s forwards;
  }
  .scoreboard-spark-tip.pos  { stroke: var(--green); }
  .scoreboard-spark-tip.neg  { stroke: var(--red); }
  .scoreboard-spark-tip.flat { stroke: var(--muted); }
  @keyframes scoreboard-spark-tip-fade {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
  /* Interactive overlay: invisible <rect> catches the cursor across the
     full SVG (not just where the curve sits); the vertical line and
     snap-dot get positioned by JS on mousemove. Readout sits above. */
  .scoreboard-spark-wrap {
    position: relative;
  }
  .scoreboard-spark-hit {
    cursor: crosshair;
  }
  .scoreboard-spark-cursor {
    stroke: var(--muted);
    stroke-width: 1;
    stroke-dasharray: 2 2;
    opacity: 0.7;
    pointer-events: none;
  }
  .scoreboard-spark-hover-dot {
    fill: none;
    stroke-width: 6;
    stroke-linecap: round;
    pointer-events: none;
  }
  .scoreboard-spark-hover-dot.pos  { stroke: var(--green); }
  .scoreboard-spark-hover-dot.neg  { stroke: var(--red); }
  .scoreboard-spark-hover-dot.flat { stroke: var(--muted); }
  .scoreboard-spark-readout {
    position: absolute;
    bottom: calc(100% + 4px);
    transform: translateX(-50%);
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 10px;
    line-height: 1.35;
    color: var(--muted);
    white-space: nowrap;
    pointer-events: none;
    z-index: 6;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
  }
  .scoreboard-spark-readout strong { color: var(--text); }
  .scoreboard-spark-readout .cum.pos  { color: var(--green); }
  .scoreboard-spark-readout .cum.neg  { color: var(--red); }
  .scoreboard-spark-readout .cum.flat { color: var(--muted); }
  /* Tiny per-day heatmap directly under each sparkline. Cells flex to
     fill the column width, so the strip reads as a continuous timeline
     regardless of how many days the column covers. Hovering a cell
     pops the .scoreboard-heat-tip with date · units/$ · W-L. */
  .scoreboard-heat {
    position: relative;
    display: flex;
    gap: 2px;
    margin: 2px 0 4px;
    height: 10px;
  }
  .scoreboard-heat-cell {
    flex: 1 1 0;
    min-width: 3px;
    border-radius: 1px;
    background: var(--border);
    cursor: pointer;
  }
  .scoreboard-heat-cell.pos { background: rgba(74, 222, 128, var(--cell-i, 0.5)); }
  .scoreboard-heat-cell.neg { background: rgba(248, 113, 113, var(--cell-i, 0.5)); }
  .scoreboard-heat-cell.flat { opacity: 0.45; cursor: default; }
  .scoreboard-heat-cell[data-heat-date]:hover { outline: 1px solid var(--text); }
  .scoreboard-heat-tip {
    position: absolute;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 6px 8px;
    font-size: 11px;
    line-height: 1.4;
    pointer-events: none;
    transform: translate(-50%, -100%);
    margin-top: -6px;
    white-space: nowrap;
    z-index: 6;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
  }
  .scoreboard-heat-tip strong { color: var(--text); }
  .scoreboard-heat-tip .tip-units.pos { color: var(--green); }
  .scoreboard-heat-tip .tip-units.neg { color: var(--red); }
  .scoreboard-supporting {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 11px;
    color: var(--muted);
    flex-wrap: wrap;
  }
  .scoreboard-supporting strong {
    color: var(--text);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .scoreboard-trend {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 999px;
    background: var(--panel);
    border: 1px solid var(--border);
    font-variant-numeric: tabular-nums;
    margin-left: auto;
  }
  .scoreboard-trend.pos { color: var(--green); border-color: rgba(74, 222, 128, 0.4); }
  .scoreboard-trend.neg { color: var(--red);   border-color: rgba(248, 113, 113, 0.4); }
  .scoreboard-trend.flat { color: var(--muted); }
  .scoreboard-empty {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.4;
    padding: 4px 0 0;
  }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  .results-section { margin-top: 32px; }
  .results-section h2 {
    margin: 0 0 12px;
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }
  td.hit { color: var(--green); font-weight: 500; }
  td.miss { color: var(--red); font-weight: 500; }
  td.num.pos { color: var(--green); }
  td.num.neg { color: var(--red); }
  .track-summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  .track-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
  }
  .track-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }
  .track-val { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .track-val.pos { color: var(--green); }
  .track-val.neg { color: var(--red); }
  .track-trend {
    font-size: 11px;
    font-weight: 500;
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
  }
  .track-trend.pos { color: var(--green); }
  .track-trend.neg { color: var(--red); }
  .track-trend.flat { color: var(--muted); }
  .sparkline-wrap {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 16px;
    margin-top: 12px;
  }
  .sparkline-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin-bottom: 8px;
  }
  .sparkline-svg { display: block; width: 100%; height: 60px; }
  /* Edge-bucket table — same visual idiom as the day breakdown but
     squeezed into the sparkline panel for grouping. */
  .track-bucket-wrap {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 16px;
    margin-top: 12px;
  }
  table.track-buckets { font-size: 13px; }
  table.track-buckets th { font-weight: 600; color: var(--muted); }
  table.track-buckets .pos { color: var(--green); }
  table.track-buckets .neg { color: var(--red); }
  /* Calibration scatter. */
  .calibration-wrap {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 16px;
    margin-top: 12px;
  }
  .cal-stats {
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
    font-variant-numeric: tabular-nums;
  }
  .cal-stats strong { color: var(--text); font-weight: 600; }
  .cal-stats strong.pos { color: var(--green); }
  .cal-stats strong.neg { color: var(--red); }
  .cal-stats strong.flat { color: var(--muted); }
  .cal-svg { display: block; width: 100%; max-width: 480px; height: auto; margin: 0 auto; }
  .cal-tick { font-size: 9px; fill: var(--muted); }
  .cal-axis { font-size: 10px; fill: var(--muted); font-weight: 600; }
  /* Projection cells with a v0/v1/v2/ML breakdown tooltip — subtle
     dotted underline on the value hints that hover reveals more. */
  td.num.proj-cell { cursor: help; }
  td.num.proj-cell:hover { background: var(--hover-overlay); }
  .pick-card-stat.proj-cell { cursor: help; }
  .pick-card-stat.proj-cell .pick-card-stat-val {
    text-decoration: underline dotted rgba(255,255,255,0.3);
    text-underline-offset: 3px;
  }
  .sparkline-axis { stroke: var(--border); stroke-width: 1; stroke-dasharray: 2 3; }
  .sparkline-line { fill: none; stroke-width: 2; }
  .sparkline-line.pos { stroke: var(--green); }
  .sparkline-line.neg { stroke: var(--red); }
  .sparkline-area.pos { fill: var(--green); opacity: 0.12; }
  .sparkline-area.neg { fill: var(--red); opacity: 0.12; }
  .sparkline-dot { fill: var(--text); }
  .split-row {
    display: grid;
    grid-template-columns: 60px 1fr 140px;
    gap: 10px;
    align-items: center;
    padding: 6px 0;
    font-variant-numeric: tabular-nums;
  }
  .split-label { font-size: 12px; font-weight: 600; }
  .split-label.over { color: var(--green); }
  .split-label.under { color: var(--red); }
  .split-bar {
    height: 8px;
    background: var(--border);
    border-radius: 4px;
    overflow: hidden;
    position: relative;
  }
  .split-bar-fill {
    height: 100%;
    border-radius: 4px;
  }
  .split-bar-fill.over { background: var(--green); }
  .split-bar-fill.under { background: var(--red); }
  .split-stats { font-size: 12px; color: var(--muted); text-align: right; }
  .split-stats strong { color: var(--text); font-weight: 600; }
  /* Daily-decision row: single picks (left) + parlay suggestions (right)
     side-by-side on desktop so both sit above the fold and read as one
     decision surface. Stacks below 1100px so cards don't get squeezed. */
  .daily-decision {
    display: grid;
    grid-template-columns: minmax(0, 8fr) minmax(0, 5fr);
    gap: 24px;
    margin-bottom: 24px;
    align-items: start;
  }
  .daily-decision > .picks-hero,
  .daily-decision > .parlay-suggester { margin: 0; }
  @media (max-width: 1100px) {
    .daily-decision { grid-template-columns: 1fr; gap: 16px; }
  }
  /* "Today's Picks" hero cards — surfaces actionable focus picks above
     the dense All Pitchers table. */
  .picks-hero { margin-bottom: 24px; }
  .picks-hero-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin-bottom: 10px;
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .picks-hero-count {
    color: var(--text);
    font-weight: 700;
  }
  .picks-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .pick-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 10px;
    position: relative;
    overflow: hidden;
  }
  .pick-card.over { border-left: 3px solid var(--green); }
  .pick-card.under { border-left: 3px solid var(--red); }
  .pick-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .pick-card-badge {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 3px 7px;
    border-radius: 4px;
  }
  .pick-card-badge.over { background: var(--green); color: #001a00; }
  .pick-card-badge.under { background: var(--red); color: #2a0000; }
  .pick-card-edge { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .pick-card-edge.over { color: var(--green); }
  .pick-card-edge.under { color: var(--red); }
  .pick-card-pitcher {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px;
    margin-bottom: 6px;
    min-width: 0;
  }
  .pick-card-pitcher .pitcher-name {
    font-size: 12px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1 1 auto;
  }
  .pick-card-pitcher .pitcher-meta {
    font-size: 11px;
    color: var(--muted);
    font-weight: 500;
    white-space: nowrap;
    flex: 0 0 auto;
    font-variant-numeric: tabular-nums;
  }
  .pick-card-stats {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding-top: 7px;
    border-top: 1px solid var(--border);
    font-size: 12px;
  }
  .pick-card-stat { display: flex; flex-direction: column; gap: 1px; }
  .pick-card-stat-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
  .pick-card-stat-val { font-weight: 600; font-variant-numeric: tabular-nums; }
  /* Live cell on each hero card. Mirrors the table-row time-cell
     palette so the same color cues read consistently in both places.
     nowrap on every variant — the stat box is narrow and an extra
     line of "Bottom 5th 4K" wraps and looks broken. */
  .pick-card-stat-val.live-pending,
  .pick-card-stat-val.live-now,
  .pick-card-stat-val.live-final { white-space: nowrap; }
  .pick-card-stat-val.live-pending { color: var(--muted); font-weight: 500; }
  .pick-card-stat-val.live-now { color: var(--text); }
  .pick-card-stat-val.live-now .live-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--red);
    margin-right: 5px;
    vertical-align: middle;
    animation: live-pulse 1.6s ease-in-out infinite;
  }
  .pick-card-stat-val.live-now .live-ks { color: var(--green); margin-left: 4px; }
  .pick-card-stat-val.live-final { color: var(--muted); }
  .pick-card-stat-val.live-final .live-ks { color: var(--green); margin-left: 4px; }
  .pick-card-pulse {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px dashed var(--border);
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
  }
  .pick-card-pulse strong { color: var(--text); font-weight: 700; }
  .pick-card.hit .pick-card-pulse,
  .pick-card.miss .pick-card-pulse {
    border-top-color: rgba(255,255,255,0.22);
    color: rgba(255,255,255,0.85);
  }
  .pick-card.hit .pick-card-pulse strong,
  .pick-card.miss .pick-card-pulse strong { color: #fff; }
  /* Once a game has started or finished, dim the surrounding stats so
     the live cell carries the eye. Edge / proj are no longer actionable. */
  .pick-card.locked .pick-card-stat:not(:last-child) { opacity: 0.55; }
  /* Settled state: card flips from the pre-settle pale tint to a solid
     saturated fill so the change is impossible to miss on a slate of
     mostly-pending cards. Green = HIT, red = MISS, regardless of bet
     direction — the original "BET OVER 6.5" pill stays visible but
     dimmed + struck-through so you can still see what you bet on.
     Specificity (3 classes) wins over .pick-card.over/under. */
  .pick-card.hit, .pick-card.miss { border-left: none; color: #fff; }
  .pick-card.hit { background: var(--green-solid); }
  .pick-card.miss { background: var(--red-solid); }
  .pick-card.hit .pick-card-pitcher,
  .pick-card.miss .pick-card-pitcher,
  .pick-card.hit .pick-card-stat-val,
  .pick-card.miss .pick-card-stat-val { color: #fff; }
  .pick-card.hit .pitcher-meta,
  .pick-card.miss .pitcher-meta,
  .pick-card.hit .pick-card-stat-label,
  .pick-card.miss .pick-card-stat-label { color: rgba(255,255,255,0.78); }
  .pick-card.hit .pick-card-stats,
  .pick-card.miss .pick-card-stats { border-top-color: rgba(255,255,255,0.22); }
  .pick-card.hit .pick-card-edge,
  .pick-card.miss .pick-card-edge { color: rgba(255,255,255,0.9); }
  /* Live cell readability on saturated bg. */
  .pick-card.hit .pick-card-stat-val.live-pending,
  .pick-card.miss .pick-card-stat-val.live-pending,
  .pick-card.hit .pick-card-stat-val.live-now,
  .pick-card.miss .pick-card-stat-val.live-now,
  .pick-card.hit .pick-card-stat-val.live-final,
  .pick-card.miss .pick-card-stat-val.live-final { color: #fff; }
  .pick-card.hit .live-ks,
  .pick-card.miss .live-ks { color: #fff; }
  /* Full-bleed banner across the top of a settled card. Replaces the
     small inline chip — the bigger letterform + edge-to-edge background
     is the unmistakable cue that the card just flipped. */
  .pick-card-banner {
    display: none;
    margin: -10px -12px 8px;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.20em;
    text-align: center;
    text-transform: uppercase;
  }
  .pick-card-banner.hit {
    display: block;
    background: rgba(255,255,255,0.18);
    color: #fff;
  }
  .pick-card-banner.miss {
    display: block;
    background: rgba(0,0,0,0.32);
    color: #fff;
  }
  .picks-empty {
    background: var(--panel);
    border: 1px dashed var(--border);
    border-radius: 8px;
    padding: 18px 20px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
  }
  /* Bet badges on hero cards — one per open parlay this pitcher is in.
     Color follows the live state of THIS leg so a glance answers "is
     my action on this guy still alive?". Tooltip carries the parlay
     details (other legs, site, payout). */
  .pick-card-bets {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 0 0 7px;
  }
  .pick-card-bet-badge {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    font-variant-numeric: tabular-nums;
    cursor: help;
    white-space: nowrap;
    line-height: 1.4;
  }
  .pick-card-bet-badge .bb-label {
    font-weight: 700;
    margin-right: 4px;
    opacity: 0.78;
  }
  .pick-card-bet-badge.pending { background: var(--panel); }
  .pick-card-bet-badge.hit {
    background: var(--green); color: #001a00; border-color: var(--green);
  }
  .pick-card-bet-badge.miss {
    background: var(--red); color: #2a0000; border-color: var(--red);
  }
  .pick-card-bet-badge.hit .bb-label,
  .pick-card-bet-badge.miss .bb-label { opacity: 0.65; }
  /* Settled hero cards have a saturated fill — make badges read against
     that by switching to translucent white panels. */
  .pick-card.hit .pick-card-bet-badge,
  .pick-card.miss .pick-card-bet-badge {
    background: rgba(255,255,255,0.18);
    color: #fff;
    border-color: rgba(255,255,255,0.3);
  }
  .pick-card.hit .pick-card-bet-badge .bb-label,
  .pick-card.miss .pick-card-bet-badge .bb-label { opacity: 0.85; }
  /* "Why this pick" disclosure on each hero card. Surfaces the raw
     model inputs so you can sanity-check what the edge is reacting to.
     Tilt arrows compare each input to a stable league pivot (not an
     invented threshold): starter K% ≈ .22, SwStr% ≈ .115, lineup K%
     ≈ .22, park = 1.00. ↑K = favors strikeouts (regardless of bet
     direction), ↓K = suppresses, • = roughly neutral. */
  .pick-why { margin-top: 8px; }
  .pick-why-summary {
    cursor: pointer;
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    user-select: none;
    padding: 3px 0;
    list-style: none;
  }
  .pick-why-summary::-webkit-details-marker { display: none; }
  .pick-why-summary::before { content: "▸ "; display: inline-block; transition: transform 0.12s; }
  .pick-why[open] .pick-why-summary::before { transform: rotate(90deg); }
  .pick-why-summary:hover { color: var(--text); }
  .pick-why-list {
    list-style: none;
    padding: 4px 0 0;
    margin: 0;
    display: grid;
    gap: 2px;
  }
  .pick-why-list li {
    display: grid;
    grid-template-columns: 1fr auto auto;
    align-items: baseline;
    gap: 8px;
    padding: 1px 0;
    font-variant-numeric: tabular-nums;
  }
  .why-label { color: var(--muted); font-size: 11px; }
  .why-val { font-weight: 600; font-size: 11px; }
  .why-tilt { font-size: 11px; font-weight: 700; min-width: 18px; text-align: right; }
  .why-tilt.k-up { color: var(--green); }
  .why-tilt.k-down { color: var(--red); }
  .why-tilt.k-flat { color: var(--muted); }
  /* On settled (saturated) cards, override muted/border colors so the
     disclosure stays legible against the green/red fill. */
  .pick-card.hit .pick-why-summary,
  .pick-card.miss .pick-why-summary,
  .pick-card.hit .why-label,
  .pick-card.miss .why-label { color: rgba(255,255,255,0.78); }
  .pick-card.hit .why-val,
  .pick-card.miss .why-val { color: #fff; }
  .pick-card.hit .why-tilt.k-up,
  .pick-card.miss .why-tilt.k-up,
  .pick-card.hit .why-tilt.k-down,
  .pick-card.miss .why-tilt.k-down,
  .pick-card.hit .why-tilt.k-flat,
  .pick-card.miss .why-tilt.k-flat { color: rgba(255,255,255,0.95); }
  /* Parlay suggester — combos of focus picks, ranked by EV per $1. */
  .parlay-suggester { margin: 8px 0 24px; }
  .parlay-suggester-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  .parlay-suggester-header h3 { margin: 0; font-size: 16px; font-weight: 600; }
  .parlay-note { color: var(--muted); font-size: 12px; }
  .parlay-section { margin-top: 10px; }
  .parlay-section-title {
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 6px;
  }
  .parlay-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 8px;
  }
  .parlay-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--muted);
    border-radius: 8px;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    position: relative;
  }
  /* Marks a 3-leg card that shares a pitcher with the top 2-leg ticket.
     The disjoint rule was dropped 2026-05-16 for visibility, but the
     2026-05-15 audit (overlapping 3-legs went -78% ROI vs disjoint
     +22%) is preserved as a UI signal so the user makes a conscious
     overlap decision instead of a silent one. Inline above the legs
     so it doesn't overlap leg content (time on the right, pill on the left). */
  .parlay-overlap-badge {
    align-self: flex-start;
    font-size: 10px;
    color: var(--yellow);
    background: rgba(245, 195, 35, 0.12);
    border: 1px solid rgba(245, 195, 35, 0.35);
    padding: 1px 6px;
    border-radius: 8px;
    cursor: help;
    line-height: 1.4;
  }
  .parlay-card.pos { border-left-color: var(--green); }
  .parlay-card.neg { border-left-color: var(--red); }
  /* Lineup-status border overrides EV color: red until every leg's opp
     lineup is posted, green once they all are. Source order matters —
     these must come after .pos/.neg to win the cascade. */
  .parlay-card.lineup-blocked { border-left-color: var(--red); opacity: 0.82; }
  .parlay-card.lineup-ready { border-left-color: var(--green); }
  html.is-bets .parlay-card.lineup-blocked { cursor: not-allowed; }
  html.is-bets .parlay-card.lineup-blocked:hover { background: var(--panel); border-color: var(--border); }
  .parlay-legs { display: flex; flex-direction: column; gap: 4px; }
  .parlay-leg {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: baseline;
    gap: 6px;
    font-size: 12px;
  }
  .parlay-leg-dir {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
    padding: 1px 6px;
    border-radius: 3px;
    white-space: nowrap;
  }
  .parlay-leg-dir.over { background: rgba(74, 222, 128, 0.15); color: var(--green); }
  .parlay-leg-dir.under { background: rgba(248, 113, 113, 0.15); color: var(--red); }
  .parlay-leg-name { font-weight: 500; }
  .parlay-leg-time { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .parlay-stats {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding-top: 7px;
    border-top: 1px solid var(--border);
    font-size: 12px;
  }
  .parlay-stat { display: flex; flex-direction: column; gap: 1px; }
  .parlay-stat-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
  .parlay-stat-val { font-weight: 600; font-variant-numeric: tabular-nums; }
  .parlay-stat-val.pos { color: var(--green); }
  .parlay-stat-val.neg { color: var(--red); }
  /* On the Bets URL the whole card is the "add to bets" affordance.
     The data attr is always present; gating on html.is-bets keeps the
     cursor/hover off the public URL where the bets form doesn't exist. */
  html.is-bets .parlay-card[data-legs] {
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  html.is-bets .parlay-card[data-legs]:hover {
    background: var(--hover-overlay);
    border-color: var(--muted);
  }
  /* Live "Combined" panel inside the Bets-tab parlay editor — recomputes
     on every leg change. Mirrors the suggester card stats so the math
     shows up identically when you tap a suggested parlay. */
  .bets-combined-panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--muted);
    border-radius: 6px;
    padding: 10px 12px;
    margin: 8px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .bets-combined-panel.pos { border-left-color: var(--green); }
  .bets-combined-panel.neg { border-left-color: var(--red); }
  .bets-combined-stats {
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    font-size: 12px;
  }
  .bets-combined-stats .parlay-stat-label { font-size: 10px; }
  .bets-combined-stats .parlay-stat-val { font-size: 14px; }
  .bets-combined-hint { color: var(--muted); font-size: 11px; }
  /* Yesterday's report card — big numbers above the table. */
  .report-card {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin: 8px 0 16px;
  }
  .report-stat {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }
  .report-stat.headline { border-left: 3px solid var(--green); }
  .report-stat.headline.neg { border-left-color: var(--red); }
  .report-stat.headline.flat { border-left-color: var(--muted); }
  .report-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .report-val {
    font-size: 22px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
  }
  .report-val.pos { color: var(--green); }
  .report-val.neg { color: var(--red); }
  .report-sub {
    font-size: 11px;
    color: var(--muted);
    margin-top: 3px;
  }
  .results-aux {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 12px;
  }
  details.tag-help {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 12px;
  }
  details.tag-help summary {
    cursor: pointer;
    user-select: none;
    display: inline-block;
  }
  details.tag-help[open] summary { margin-bottom: 8px; }
  details.tag-help .legend-row {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    margin-top: 4px;
  }
  /* Shared twisty chrome for every collapsible deep-dive section
     (Browse all pitchers, Model accuracy, Yesterday's pitcher detail,
     Track Record daily table). Each section adds its own X-wrap class
     for distinct identity, plus this common .twisty-wrap for the chrome. */
  details.twisty-wrap {
    margin: 16px 0 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--panel);
  }
  details.twisty-wrap > summary {
    cursor: pointer;
    user-select: none;
    list-style: none;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  details.twisty-wrap > summary::-webkit-details-marker { display: none; }
  details.twisty-wrap > summary::marker { content: ""; }
  details.twisty-wrap > summary::after {
    content: "▸";
    color: var(--muted);
    font-size: 11px;
    transition: transform 0.15s ease;
  }
  details.twisty-wrap[open] > summary::after { transform: rotate(90deg); }
  details.twisty-wrap > .twisty-body { padding: 0 14px 14px; }
  /* Slate-specific extras: legacy .slate-table-body kept as the body
     selector + a count chip rendered in the summary. */
  details.slate-table-wrap > summary .slate-table-count {
    color: var(--muted);
    font-weight: 500;
    font-size: 12px;
    margin-left: auto;
    margin-right: 8px;
  }
  details.slate-table-wrap > .slate-table-body { padding: 0 14px 14px; }
  details.slate-table-wrap > .slate-table-body .slate-toolbar { margin-top: 4px; }
  /* Parlay Track Record cards. Three cards (2-leg, 3-leg, Combined)
     show units / hit rate / predicted / ROI with a small calibration
     delta line at the bottom (actual − predicted hit rate). */
  .parlay-track-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
    margin-top: 8px;
  }
  .parlay-track-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }
  .parlay-track-card-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 4px;
  }
  .parlay-track-card-label {
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
  }
  .parlay-track-card-count {
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .parlay-track-card-hero {
    font-size: 22px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    margin-bottom: 8px;
  }
  .parlay-track-card-hero.pos { color: var(--green); }
  .parlay-track-card-hero.neg { color: var(--red); }
  .parlay-track-card-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
  }
  .parlay-track-stat { display: flex; flex-direction: column; gap: 2px; }
  .parlay-track-stat-label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .parlay-track-stat-val {
    font-size: 13px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .parlay-track-stat-val.pos { color: var(--green); }
  .parlay-track-stat-val.neg { color: var(--red); }
  .parlay-track-card-calib {
    margin-top: 8px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    color: var(--muted);
  }
  .parlay-track-card-calib.pos { color: var(--green); }
  .parlay-track-card-calib.neg { color: var(--red); }
  .parlay-track-card-calib.flat { color: var(--muted); }
  .parlay-track-card-sb {
    margin-top: 4px;
    font-size: 10px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .parlay-track-card-sb strong { font-weight: 600; }
  .parlay-track-card-sb strong.pos { color: var(--green); }
  .parlay-track-card-sb strong.neg { color: var(--red); }
  /* Header row holding the section h2 + payout-profile dropdown. */
  .parlay-track-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .parlay-track-header h2 { margin: 0; }
  .parlay-track-profile {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .parlay-track-profile select {
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
    text-transform: none;
    letter-spacing: 0;
  }
  /* Real-bets sub-block: same card chrome but a separator + sub-heading
     so the user can scan "theory above, reality below" at a glance. */
  .parlay-actual-wrap {
    margin-top: 24px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .parlay-actual-title {
    margin: 0 0 4px;
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  /* Track Record: cumulative units + OVER/UNDER split rendered as a
     single side-by-side row instead of two stacked cards. Stacks below
     900px so neither chart gets squeezed below readability. */
  .track-charts-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    align-items: start;
    margin-top: 8px;
  }
  .track-charts-row > * { margin-top: 0 !important; }
  @media (max-width: 900px) {
    .track-charts-row { grid-template-columns: 1fr; gap: 12px; }
  }
  /* Free-entry checkbox + row badge. */
  .bets-form-actions label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font-size: 13px;
    cursor: pointer;
    user-select: none;
  }
  .bets-form-actions input[type="checkbox"] {
    accent-color: var(--green);
    cursor: pointer;
  }
  .free-badge {
    display: inline-block;
    margin-left: 6px;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.06em;
    background: var(--yellow);
    color: #2a1f00;
    vertical-align: middle;
  }
  .totals-card-secondary {
    margin-top: 8px;
    padding: 8px 12px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 12px;
    color: var(--muted);
    display: flex;
    gap: 14px;
    align-items: center;
    flex-wrap: wrap;
  }
  .totals-card-secondary strong { color: var(--text); font-weight: 600; }
  .site-pnl-cell {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    padding: 2px 8px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .site-pnl-cell strong { color: var(--text); font-weight: 700; }
  .site-pnl-cell strong.pos { color: var(--green); }
  .site-pnl-cell strong.neg { color: var(--red); }
  /* Inline parlay status (in the row, no expand needed). */
  .parlay-inline-status {
    display: inline-flex;
    gap: 4px;
    margin-left: 10px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .parlay-inline-status .pi-h { color: var(--green); font-weight: 700; }
  .parlay-inline-status .pi-m { color: var(--red); font-weight: 700; }
  .parlay-inline-status .pi-p { color: var(--muted); }
  /* Bets tab — per-user ledger. */
  .user-chip {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 14px;
    margin-bottom: 12px;
    font-size: 13px;
    color: var(--muted);
  }
  .user-chip-name strong { color: var(--text); }
  .user-chip-logout {
    color: var(--muted);
    text-decoration: none;
    border: 1px solid var(--border);
    padding: 4px 10px;
    border-radius: 5px;
    font-size: 12px;
  }
  .user-chip-logout:hover {
    color: var(--text);
    background: rgba(255, 255, 255, 0.04);
  }
  .auth-card {
    max-width: 440px;
    margin: 32px auto;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px 28px;
  }
  .auth-wizard { max-width: 560px; }
  .auth-title {
    margin: 0 0 6px;
    font-size: 20px;
    color: var(--text);
  }
  .auth-sub {
    margin: 0 0 18px;
    color: var(--muted);
    font-size: 13px;
  }
  .auth-form { display: flex; flex-direction: column; gap: 14px; }
  .auth-row { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
  .auth-row > span {
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .auth-row > input {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 9px 11px;
    font-family: inherit;
    font-size: 14px;
  }
  .auth-row > input:focus {
    outline: none;
    border-color: var(--text);
  }
  .auth-hint {
    font-size: 11px;
    color: var(--muted);
    margin-top: 2px;
  }
  .auth-row-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .auth-rules {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    font-size: 12px;
    color: var(--muted);
    line-height: 1.5;
  }
  .auth-rules h3 {
    margin: 0 0 8px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text);
  }
  .auth-rules p { margin: 4px 0; }
  .auth-rules ul { margin: 4px 0 8px 18px; padding: 0; }
  .auth-rules li { margin: 2px 0; }
  .auth-rules em { color: var(--text); font-style: normal; font-weight: 600; }
  .auth-ack {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-top: 10px;
    color: var(--text);
    font-size: 13px;
  }
  .auth-ack input { margin-top: 2px; }
  .auth-error {
    background: rgba(220, 60, 60, 0.12);
    border: 1px solid rgba(220, 60, 60, 0.4);
    color: var(--red, #e88);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
  }
  .auth-submit {
    background: var(--text);
    color: var(--bg);
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  .auth-submit:disabled { opacity: 0.6; cursor: default; }
  .bets-form-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    margin-bottom: 20px;
  }
  .bets-form-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin-bottom: 12px;
  }
  .bets-form-grid {
    display: grid;
    grid-template-columns: 130px 1fr 110px 100px 100px 1fr;
    gap: 10px;
    align-items: end;
  }
  .bets-field { display: flex; flex-direction: column; gap: 4px; }
  .bets-field label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }
  .bets-field input {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px 9px;
    font-family: inherit;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }
  .bets-field input:focus {
    outline: none;
    border-color: var(--green);
  }
  .bets-form-actions {
    margin-top: 12px;
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .bets-form-actions button {
    background: var(--green);
    color: #001a00;
    border: 1px solid var(--green);
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }
  .bets-form-actions button:hover { filter: brightness(1.08); }
  .bets-form-msg { color: var(--muted); font-size: 12px; }
  .bets-form-msg.error { color: var(--red); }
  .bets-totals-card {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
  }
  .bets-totals-card .report-stat { padding: 10px 12px; }
  /* Bankroll card — one-line summary of the experiment's running
     bankroll. Sits above the totals strip. Color edges (left border)
     reflect status: green when ahead, neutral while active, amber on
     pause-review, red on ended. */
  .bankroll-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--muted);
    border-radius: 8px;
    padding: 10px 14px;
    margin-top: 14px;
    margin-bottom: 8px;
  }
  .bankroll-card.pause { border-left-color: var(--amber, #d4a017); }
  .bankroll-card.ended { border-left-color: var(--red); }
  .bankroll-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
  }
  .bankroll-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
  }
  .bankroll-val {
    font-size: 22px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .bankroll-pct { color: var(--muted); font-size: 13px; }
  .bankroll-net { font-size: 13px; font-variant-numeric: tabular-nums; }
  .bankroll-net.pos { color: var(--green); }
  .bankroll-net.neg { color: var(--red); }
  .bankroll-sub { color: var(--muted); font-size: 11px; margin-top: 4px; }
  .bankroll-pending { color: var(--text); }
  .bankroll-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 3px 7px;
    border-radius: 4px;
    margin-left: auto;
  }
  .bankroll-badge.pause { background: var(--amber, #d4a017); color: #000; }
  .bankroll-badge.ended { background: var(--red); color: #fff; }
  /* Ticket cards — one card per bet, replacing the old table view.
     Card body is always tappable; tapping toggles the action drawer
     (W / L / Edit / Reopen / Delete) at the bottom. */
  .bets-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 10px;
    margin-top: 4px;
    align-items: start;
  }
  /* Full-width children inside the cards grid: empty state message and
     the "Show older bets" divider should span both columns instead of
     sitting in one half. */
  .bets-cards > .empty-msg,
  .bets-cards > .bets-older-divider { grid-column: 1 / -1; }
  .bet-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px 10px;
    cursor: pointer;
    transition: border-color 0.12s ease, background 0.12s ease;
  }
  .bet-card:hover { border-color: var(--muted); }
  .bet-card.expanded-actions { border-color: var(--text); }
  /* Live tint: applied from paintLiveKs based on aggregated leg states.
     Listed before the manual result-W/L rules so the user's explicit
     mark wins (same specificity, later source = winner). */
  .bet-card.live-win {
    background: rgba(74, 222, 128, 0.07);
    border-color: rgba(74, 222, 128, 0.45);
  }
  .bet-card.live-loss {
    background: rgba(248, 113, 113, 0.07);
    border-color: rgba(248, 113, 113, 0.40);
  }
  .bet-card.live-pending {
    background: rgba(251, 191, 36, 0.05);
    border-color: rgba(251, 191, 36, 0.40);
  }
  .bet-card.result-W {
    background: rgba(74, 222, 128, 0.07);
    border-color: rgba(74, 222, 128, 0.45);
  }
  .bet-card.result-L {
    background: rgba(248, 113, 113, 0.07);
    border-color: rgba(248, 113, 113, 0.40);
  }
  .bet-card.older-hidden { display: none; }
  .bet-card-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 6px;
  }
  .bet-card-meta {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--muted);
  }
  .bet-card-date {
    color: var(--text);
    font-weight: 600;
    font-size: 13px;
  }
  .bet-card-legcount { font-size: 11px; }
  .bet-card-money {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-variant-numeric: tabular-nums;
    font-size: 14px;
  }
  .bet-card-stake { color: var(--text); font-weight: 600; }
  .bet-card-arrow { color: var(--muted); }
  .bet-card-payout { font-weight: 700; }
  .bet-card-payout.pos { color: var(--green); }
  .bet-card-payout.zero { color: var(--muted); }
  .bet-card-odds {
    color: var(--muted);
    font-size: 12px;
    margin-left: 2px;
  }
  .bet-card-boost {
    font-size: 11px;
    color: var(--yellow);
    border: 1px solid rgba(251, 191, 36, 0.4);
    border-radius: 3px;
    padding: 1px 5px;
  }
  /* The old leg list was indented for a ▶ caret in the row view; in
     cards, no caret, so reset the padding. */
  .bet-card .parlay-leg-list { padding-left: 0; }
  /* Bet-time vs current — "placed at +9.4% ↘" — small chip showing the
     EV/$1 we saw when the bet was submitted, plus a drift arrow against
     current slate state. Lets you see whether a win/loss was a sharp
     bet or a lucky/unlucky one without flipping back and forth. */
  .leg-placed-at {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    padding: 1px 5px;
    border-radius: 3px;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border);
    cursor: help;
    white-space: nowrap;
  }
  .leg-placed-at.pos { color: var(--green); border-color: rgba(74, 222, 128, 0.4); }
  .leg-placed-at.neg { color: var(--red); border-color: rgba(248, 113, 113, 0.4); }
  .leg-placed-at.flat { color: var(--muted); }
  .leg-delta.pos  { color: var(--green); }
  .leg-delta.neg  { color: var(--red); }
  .leg-delta.flat { color: var(--muted); opacity: 0.7; }
  .bet-card-actions {
    display: none;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .bet-card.expanded-actions .bet-card-actions { display: flex; }
  .bet-card-actions button.act {
    flex: 1;
    min-width: 80px;
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px 10px;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
  }
  .bet-card-actions button.act:hover { color: var(--text); border-color: var(--text); }
  .bet-card-actions button.act.win:hover { color: var(--green); border-color: var(--green); }
  .bet-card-actions button.act.lose:hover { color: var(--red); border-color: var(--red); }
  .bet-card-actions button.act.del:hover { color: var(--red); border-color: var(--red); }
  /* Show-older toggle button used to live in a table row; now standalone. */
  .bets-older-divider {
    display: flex;
    justify-content: center;
    padding: 8px 0;
  }
  .bets-table-wrap { overflow-x: auto; }
  table.bets-ledger { min-width: 900px; }
  table.bets-ledger td.actions { white-space: nowrap; text-align: right; }
  table.bets-ledger button.act {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 3px 8px;
    margin-left: 4px;
    cursor: pointer;
    font-family: inherit;
    font-size: 11px;
    font-weight: 600;
  }
  table.bets-ledger button.act:hover { color: var(--text); border-color: var(--text); }
  table.bets-ledger button.act.win:hover { color: var(--green); border-color: var(--green); }
  table.bets-ledger button.act.lose:hover { color: var(--red); border-color: var(--red); }
  table.bets-ledger button.act.del:hover { color: var(--red); border-color: var(--red); }
  /* iPad landscape (and other tablet-class touch devices): the
     phone-only stacked layout at max-width: 600px doesn't fire here,
     but the desktop 11px buttons are still too small for thumbs. Bump
     padding/font-size on any coarse-pointer device above phone width. */
  @media (pointer: coarse) and (min-width: 601px) {{
    table.bets-ledger button.act {{
      padding: 8px 12px;
      font-size: 13px;
      margin-left: 6px;
    }}
  }}
  table.bets-ledger td.payout.pos { color: var(--green); }
  table.bets-ledger td.payout.zero { color: var(--muted); }
  table.bets-ledger tr.editing td { background: rgba(74, 222, 128, 0.04); }
  table.bets-ledger input.cell-edit {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--green);
    border-radius: 4px;
    padding: 4px 6px;
    font-family: inherit;
    font-size: 13px;
    width: 100%;
    box-sizing: border-box;
    font-variant-numeric: tabular-nums;
  }
  /* Parlay form — leg-count selector + N leg rows. */
  .bets-form-top {
    display: flex;
    gap: 14px;
    align-items: end;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .bets-form-top .bets-field { min-width: 130px; }
  .bets-leg-rows {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 12px;
    padding: 10px 12px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  .bets-leg-row {
    display: grid;
    grid-template-columns: 60px 1fr 80px 100px;
    gap: 8px;
    align-items: center;
  }
  .bets-leg-row .leg-line-input {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px 9px;
    font-family: inherit;
    font-size: 13px;
    width: 100%;
    box-sizing: border-box;
    font-variant-numeric: tabular-nums;
    text-align: center;
  }
  .bets-leg-row .leg-line-input:focus {
    outline: none;
    border-color: var(--green);
  }
  .bets-leg-row .leg-line-input.overridden {
    border-color: var(--yellow);
  }
  .bets-leg-label {
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .ou-toggle {
    display: inline-flex;
    border: 1px solid var(--border);
    border-radius: 5px;
    overflow: hidden;
    width: 100%;
  }
  .ou-toggle button {
    flex: 1;
    background: transparent;
    color: var(--muted);
    border: none;
    padding: 7px 0;
    font-family: inherit;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    letter-spacing: 0.04em;
  }
  .ou-toggle button + button { border-left: 1px solid var(--border); }
  .ou-toggle button.active.over { background: var(--green); color: #001a00; }
  .ou-toggle button.active.under { background: var(--red); color: #2a0000; }
  .ou-toggle button:hover:not(.active) { color: var(--text); }
  /* Site selector (PP / UD / DK) — same visual pattern as .ou-toggle. */
  .site-toggle {
    display: inline-flex;
    border: 1px solid var(--border);
    border-radius: 5px;
    overflow: hidden;
    width: 100%;
  }
  .site-toggle button {
    flex: 1;
    background: transparent;
    color: var(--muted);
    border: none;
    padding: 7px 0;
    font-family: inherit;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    letter-spacing: 0.04em;
  }
  .site-toggle button + button { border-left: 1px solid var(--border); }
  .site-toggle button.active { background: var(--green); color: #001a00; }
  .site-toggle button:hover:not(.active) { color: var(--text); }
  .site-badge {
    display: inline-block;
    margin-left: 6px;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.06em;
    background: rgba(74, 222, 128, 0.18);
    color: var(--green);
    border: 1px solid rgba(74, 222, 128, 0.35);
    vertical-align: middle;
  }
  .bets-form-bottom {
    display: grid;
    grid-template-columns: 130px 100px 100px 1fr;
    gap: 10px;
    align-items: end;
    margin-bottom: 12px;
  }
  /* Compact parlay display in the ledger table. */
  .parlay-summary {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .parlay-summary:hover .parlay-toggle { color: var(--text); }
  .parlay-toggle {
    color: var(--muted);
    font-size: 10px;
    width: 10px;
    display: inline-block;
    transition: transform 0.15s;
  }
  tr.expanded .parlay-toggle { transform: rotate(90deg); }
  .parlay-leg-list {
    margin: 0;
    padding: 6px 0 6px 22px;
    list-style: none;
    font-size: 12px;
    color: var(--muted);
  }
  .parlay-leg-list li {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr) max-content max-content;
    gap: 4px 10px;
    padding: 2px 0;
    align-items: baseline;
  }
  /* No wraps inside a leg row — Leg N, O/U, and live status all stay
     on one line. The pitcher name (the only flexible column) truncates
     with ellipsis if the row gets too narrow to fit everything. */
  .parlay-leg-list li > * { white-space: nowrap; }
  .parlay-leg-ou.over { color: var(--green); font-weight: 600; }
  .parlay-leg-ou.under { color: var(--red); font-weight: 600; }
  .parlay-leg-name {
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }
  tr.parlay-detail td { padding-top: 0; padding-bottom: 8px; background: rgba(0,0,0,0.15); }
  tr.parlay-detail.hidden { display: none; }
  tr.parlay-row { cursor: pointer; }
  tr.parlay-row:hover td { background: rgba(255, 255, 255, 0.02); }
  tr.parlay-row.expanded td { background: rgba(74, 222, 128, 0.04); }
  /* W/L row shading on the Bets ledger. Pending rows stay neutral.
     Uses the same alpha range as the slate table's row-focus tints so
     the visual language carries across both tables. The expanded +
     hover overrides exist because, without them, the existing
     .expanded / :hover td rules above paint over the W/L tint. */
  tr.parlay-row.result-W td { background: rgba(74, 222, 128, 0.16); }
  tr.parlay-row.result-L td { background: rgba(248, 113, 113, 0.14); }
  tr.parlay-row.result-W:hover td { background: rgba(74, 222, 128, 0.22); }
  tr.parlay-row.result-L:hover td { background: rgba(248, 113, 113, 0.20); }
  tr.parlay-row.result-W.expanded td { background: rgba(74, 222, 128, 0.22); }
  tr.parlay-row.result-L.expanded td { background: rgba(248, 113, 113, 0.20); }
  tr.parlay-detail.result-W td { background: rgba(74, 222, 128, 0.18); }
  tr.parlay-detail.result-L td { background: rgba(248, 113, 113, 0.16); }
  /* Pitcher picker (select + custom-text fallback). */
  .leg-picker { position: relative; }
  .leg-picker select.pitcher-select {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px 9px;
    font-family: inherit;
    font-size: 13px;
    width: 100%;
  }
  .leg-picker select.pitcher-select:focus {
    outline: none;
    border-color: var(--green);
  }
  .leg-picker input.pitcher-custom {
    margin-top: 4px;
    width: 100%;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px 9px;
    font-family: inherit;
    font-size: 13px;
  }
  .leg-picker input.pitcher-custom:focus {
    outline: none;
    border-color: var(--green);
  }
  .leg-picker input.pitcher-custom.hidden { display: none; }
  .leg-picker .leg-context {
    font-size: 11px;
    color: var(--muted);
    margin-top: 3px;
    line-height: 1.3;
  }
  .leg-picker .leg-context.over { color: var(--green); }
  .leg-picker .leg-context.under { color: var(--red); }
  .leg-picker .leg-context.investigate { color: var(--yellow); }
  .live-status {
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--muted);
  }
  .live-status .live-ks { font-weight: 700; color: var(--text); margin-right: 6px; }
  .live-status.hit .live-ks { color: var(--green); }
  .live-status.miss .live-ks { color: var(--red); }
  .live-status .live-pitches { color: var(--muted); font-weight: 500; margin-right: 6px; }
  .live-status .live-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 700;
  }
  /* Hover affordance: badges with a score title get a help cursor. */
  .live-status .live-badge[title],
  .live-status .muted[title] { cursor: help; }
  .live-status.hit .live-badge { background: var(--green); color: #001a00; }
  .live-status.miss .live-badge { background: var(--red); color: #2a0000; }
  .live-status.live .live-badge { background: var(--yellow); color: #2a1f00; }
  .live-status.preview .live-badge {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
  }
  /* Mobile-only "Live now" quick-status strip at the top of the bets
     panel: one tappable row per still-pending bet, one chip per leg.
     Hidden on desktop where the full ledger is already at the top. */
  .bets-quickstatus { display: none; }
  .bets-quickstatus-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    padding: 6px 4px 6px 0;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
  }
  .bets-quickstatus-row:last-child { border-bottom: none; }
  .bets-quickstatus-row:active { background: rgba(255, 255, 255, 0.04); }
  .bets-quickstatus-row .qs-chevron {
    margin-left: auto;
    color: var(--muted);
    font-size: 16px;
    line-height: 1;
    flex: 0 0 auto;
    align-self: center;
    padding-left: 4px;
  }
  .bets-quickstatus-row .qs-meta {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-right: 2px;
    flex: 0 0 auto;
  }
  .bets-qs-chip {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    border: 1px solid var(--border);
    color: var(--muted);
    background: transparent;
    white-space: nowrap;
  }
  .bets-qs-chip .qs-name { font-weight: 600; color: var(--text); }
  .bets-qs-chip.hit { background: rgba(74, 222, 128, 0.15); border-color: var(--green); color: var(--green); }
  .bets-qs-chip.hit .qs-name { color: var(--green); }
  .bets-qs-chip.miss { background: rgba(248, 113, 113, 0.15); border-color: var(--red); color: var(--red); }
  .bets-qs-chip.miss .qs-name { color: var(--red); }
  .bets-qs-chip.live { background: rgba(251, 191, 36, 0.12); border-color: var(--yellow); color: var(--yellow); }
  .bets-qs-chip.live .qs-name { color: var(--text); }
  .bets-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }
  .bets-toolbar button.refresh-live {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
  }
  .bets-toolbar button.refresh-live:hover { color: var(--text); border-color: var(--text); }
  .bets-toolbar .live-stamp { font-size: 11px; color: var(--muted); }
  /* "Show older" divider row in the bets ledger. */
  tr.older-hidden { display: none; }
  tr.bets-older-toggle td {
    padding: 8px 14px;
    background: rgba(255, 255, 255, 0.02);
    border-top: 1px solid var(--border);
    text-align: center;
  }
  .bets-older-btn {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 12px;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
  }
  .bets-older-btn:hover { color: var(--text); border-color: var(--text); }
  td.error.over { color: var(--green); }
  td.error.under { color: var(--red); }
  td.error.zero { color: var(--muted); }
  main { padding: 24px 32px; }
  .summary {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 24px;
  }
  .summary p { margin: 0; }
  .legend {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 16px;
    font-size: 12px;
    color: var(--muted);
  }
  .legend-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--panel);
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--border);
  }
  th, td {
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
  }
  tr:last-child td { border-bottom: none; }
  th {
    background: var(--header-tint);
    font-weight: 500;
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  th[title] {
    cursor: help;
    border-bottom: 1px dotted var(--muted);
  }
  details.howto {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 16px;
    font-size: 13px;
  }
  details.howto summary {
    cursor: pointer;
    color: var(--muted);
    user-select: none;
    font-weight: 500;
  }
  details.howto[open] summary { margin-bottom: 10px; }
  details.howto dl { margin: 0; }
  details.howto dt {
    font-weight: 600;
    color: var(--text);
    margin-top: 8px;
  }
  details.howto dt:first-child { margin-top: 0; }
  details.howto dd {
    margin: 2px 0 0 0;
    color: var(--muted);
  }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.player { font-weight: 500; }
  td.slot { color: var(--muted); }
  td.gametime { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
  /* Pitcher Ks tab — live status injected into the time cell. */
  td.gametime .time-rel { color: var(--muted); font-size: 11px; margin-left: 4px; }
  td.gametime .time-rel.urgent { color: var(--yellow); font-weight: 600; }
  td.gametime .time-started { color: var(--muted); font-size: 11px; margin-left: 4px; }
  td.gametime .live-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--red);
    margin-right: 5px;
    vertical-align: middle;
    animation: live-pulse 1.6s ease-in-out infinite;
  }
  @keyframes live-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }
  td.gametime .live-text { color: var(--text); font-weight: 500; }
  td.gametime .live-ks { color: var(--green); font-weight: 600; margin-left: 4px; }
  td.gametime .final-text { color: var(--muted); }
  /* Locked-out: game has started, bet window closed. Demote the row
     visually but keep it readable so you can still see the model number. */
  tr.row-locked { opacity: 0.55; }
  tr.row-locked td.gametime { color: var(--text); opacity: 1; }
  /* Noise/no-line filter toggle: hide noise rows by default; show on toggle. */
  .slate-toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 12px 0 8px;
    font-size: 12px;
    color: var(--muted);
  }
  .slate-toolbar button {
    background: var(--panel);
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 5px 10px;
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
  }
  .slate-toolbar button:hover { color: var(--text); border-color: var(--text); }
  .slate-toolbar button.active { color: var(--text); border-color: var(--text); }
  body.hide-noise tr.row-noise,
  body.hide-noise tr.row-noline { display: none; }
  /* Sparkline tooltip overlay — absolutely positioned over the SVG. */
  .sparkline-wrap { position: relative; }
  .sparkline-tip {
    position: absolute;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 6px 8px;
    font-size: 11px;
    line-height: 1.4;
    pointer-events: none;
    transform: translate(-50%, -100%);
    margin-top: -8px;
    white-space: nowrap;
    z-index: 5;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
  }
  .sparkline-tip strong { color: var(--text); }
  .sparkline-tip .tip-units.pos { color: var(--green); }
  .sparkline-tip .tip-units.neg { color: var(--red); }
  .sparkline-hover-target {
    fill: transparent;
    stroke: transparent;
    cursor: pointer;
  }
  .sparkline-hover-target:hover ~ .sparkline-dot,
  .sparkline-svg circle.sparkline-dot.sparkline-dot-hover { fill: var(--green); }
  /* Daily P&L heatmap — GitHub-style row of squares, one per day in
     the track-record window. Color intensity scales with |units| via
     the --cell-i custom prop; days with no picks render as a flat
     muted square. Hover tip is a separate sibling div positioned by
     a dedicated handler (the sparkline tip handler is SVG-bound). */
  .cal-wrap { position: relative; margin-top: 14px; }
  .cal-grid {
    display: flex;
    gap: 3px;
    margin-top: 6px;
    flex-wrap: wrap;
  }
  .cal-cell {
    width: 22px;
    height: 22px;
    border-radius: 3px;
    background: var(--border);
    flex-shrink: 0;
    cursor: pointer;
  }
  .cal-cell.cal-empty { cursor: default; opacity: 0.45; }
  .cal-cell.pos { background: rgba(74, 222, 128, var(--cell-i, 0.5)); }
  .cal-cell.neg { background: rgba(248, 113, 113, var(--cell-i, 0.5)); }
  .cal-cell.flat { background: var(--border); }
  .cal-cell:hover { outline: 1px solid var(--text); }
  /* Money-mode cells (Bets tab heatmap). Wider so the $ amount fits
     inside the tile; date label sits underneath the dollar value. */
  .cal-grid.cal-grid-money { gap: 4px; }
  .cal-cell.cal-cell-money {
    width: auto;
    min-width: 64px;
    height: 38px;
    padding: 3px 8px;
    border-radius: 4px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    line-height: 1.1;
  }
  .cal-cell.cal-cell-money .cal-money-amt {
    font-size: 11px;
    font-weight: 600;
    color: var(--text);
    letter-spacing: 0.2px;
  }
  .cal-cell.cal-cell-money .cal-money-date {
    font-size: 9px;
    color: var(--muted);
    margin-top: 1px;
  }
  .cal-cell.cal-cell-money.cal-empty .cal-money-amt { color: var(--muted); font-weight: 400; }
  .cal-legend {
    display: flex;
    gap: 5px;
    align-items: center;
    margin-top: 8px;
    font-size: 10px;
    color: var(--muted);
  }
  .cal-legend .cal-cell { width: 12px; height: 12px; cursor: default; }
  .cal-legend .cal-legend-spacer { width: 6px; }
  .cal-tip {
    position: absolute;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 6px 8px;
    font-size: 11px;
    line-height: 1.4;
    pointer-events: none;
    transform: translate(-50%, -100%);
    margin-top: -6px;
    white-space: nowrap;
    z-index: 5;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
  }
  .cal-tip strong { color: var(--text); }
  .cal-tip .tip-units.pos { color: var(--green); }
  .cal-tip .tip-units.neg { color: var(--red); }
  td.edge.over { color: var(--green); font-weight: 500; }
  td.edge.under { color: var(--red); font-weight: 500; }
  tr.row-focus.dir-over { background: var(--green-bg); }
  tr.row-focus.dir-under { background: var(--red-bg); }
  tr.row-investigate { background: var(--yellow-bg); }
  tr.row-noline td { color: var(--muted); }
  tr.row-focus.dir-over td:first-child {
    border-left: 4px solid var(--green);
    padding-left: 8px;
  }
  tr.row-focus.dir-under td:first-child {
    border-left: 4px solid var(--red);
    padding-left: 8px;
  }
  tr.row-investigate td:first-child {
    border-left: 4px solid var(--yellow);
    padding-left: 8px;
  }
  .tag {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .tag strong { font-weight: 700; letter-spacing: 0.06em; }
  .tag-focus.tag-dir-over { background: var(--green); color: #001a00; }
  .tag-focus.tag-dir-under { background: var(--red); color: #2a0000; }
  .tag-investigate { background: var(--yellow); color: #2a1f00; }
  .tag-noise { background: transparent; color: var(--muted); }
  .tag-noline { background: transparent; color: var(--muted); }
  .lineup-pending {
    display: inline-block;
    margin-left: 6px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--yellow);
    opacity: 0.85;
    white-space: nowrap;
  }
  .pitcher-meta .lineup-pending { font-size: 10px; }
  .muted { color: var(--muted); }
  .empty-msg { color: var(--muted); padding: 24px; text-align: center; }
  footer {
    padding: 16px 32px;
    color: var(--muted);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 32px;
  }
  code {
    background: var(--border);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 12px;
  }
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  @media (max-width: 768px) {
    .table-scroll > table { min-width: max-content; }
    .table-scroll > table th, .table-scroll > table td { white-space: nowrap; }
    body { font-size: 13px; }
    header { padding: 16px 14px 0; }
    main { padding: 16px 14px; }
    footer { padding: 12px 14px; }
    header h1 { font-size: 16px; }
    .results-section { margin-top: 24px; }
    th, td { padding: 7px 8px; }
    th { font-size: 10px; }
    .picks-grid { grid-template-columns: repeat(2, 1fr); }
    .parlay-grid { grid-template-columns: 1fr; }
    .track-summary { grid-template-columns: repeat(2, 1fr); }
    .report-card { grid-template-columns: repeat(2, 1fr); }
    .bets-totals-card { grid-template-columns: repeat(2, 1fr); }
    .bets-form-grid { grid-template-columns: 1fr 1fr; }
    .bets-form-grid > .bets-field { grid-column: span 2; }
    .bets-form-grid > .bets-field:nth-child(n+3):nth-child(-n+5) { grid-column: span 1; }
    .bets-form-bottom { grid-template-columns: 1fr 1fr; }
    .bets-form-bottom > :last-child { grid-column: span 2; }
    .bets-leg-row { grid-template-columns: auto 1fr 80px; gap: 6px; }
    .bets-leg-row .leg-picker { grid-column: span 3; }
    .bets-leg-label { font-size: 10px; }
    .split-row { grid-template-columns: 50px 1fr 100px; gap: 8px; }
    .split-stats { font-size: 11px; }
    .parlay-leg { grid-template-columns: auto 1fr; }
    .parlay-leg-time { grid-column: 2; padding-left: 0; }
    table.bets-ledger { min-width: 720px; }
    .pick-card-pitcher { font-size: 14px; }
    .report-val { font-size: 18px; }
  }
  @media (max-width: 480px) {
    .picks-grid { grid-template-columns: 1fr; }
    .track-summary { grid-template-columns: 1fr 1fr; }
    .report-card { grid-template-columns: 1fr 1fr; }
    .actions button { flex: 1; min-width: 0; }
  }
  /* Phone-sized bets ledger: keep the same <table> DOM (so event
     handlers, expand/collapse, W/L tints, and live-K painting all
     keep working) but visually re-layout each row as a stacked card.
     Each parlay-row td maps to a grid-area; the parlay-detail row
     becomes a continuation block joined to its summary card. */
  @media (max-width: 600px) {
    .bets-table-wrap { overflow: visible; -webkit-overflow-scrolling: auto; }
    table.bets-ledger { min-width: 0; width: 100%; border-collapse: separate; border-spacing: 0; }
    table.bets-ledger thead { display: none; }
    table.bets-ledger,
    table.bets-ledger tbody { display: block; }
    table.bets-ledger tr.parlay-row {
      display: grid;
      grid-template-columns: 1fr auto;
      grid-template-areas:
        "date    result"
        "parlay  parlay"
        "stake   stake"
        "odds    odds"
        "payout  payout"
        "actions actions";
      gap: 4px 10px;
      align-items: center;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      margin: 0 0 10px;
    }
    table.bets-ledger tr.parlay-row > td {
      display: block;
      border: none;
      padding: 0;
      white-space: normal;
      background: transparent;
    }
    table.bets-ledger tr.parlay-row > td:nth-child(1) {
      grid-area: date;
      font-size: 12px;
      color: var(--muted);
      font-weight: 600;
    }
    table.bets-ledger tr.parlay-row > td:nth-child(2) {
      grid-area: parlay;
      font-size: 13px;
      line-height: 1.45;
    }
    table.bets-ledger tr.parlay-row > td:nth-child(3) { grid-area: stake; }
    table.bets-ledger tr.parlay-row > td:nth-child(4) { grid-area: odds; }
    table.bets-ledger tr.parlay-row > td:nth-child(5) { display: none; }  /* Boost — rarely used, hidden on phones */
    table.bets-ledger tr.parlay-row > td:nth-child(6) {
      grid-area: result;
      text-align: right;
      font-size: 14px;
      justify-self: end;
    }
    table.bets-ledger tr.parlay-row > td:nth-child(7) { grid-area: payout; }
    /* Stake / Odds / Payout: caption + value on one row each. */
    table.bets-ledger tr.parlay-row > td:nth-child(3),
    table.bets-ledger tr.parlay-row > td:nth-child(4),
    table.bets-ledger tr.parlay-row > td:nth-child(7) {
      display: flex;
      align-items: baseline;
      gap: 8px;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      text-align: left;
    }
    table.bets-ledger tr.parlay-row > td:nth-child(3)::before,
    table.bets-ledger tr.parlay-row > td:nth-child(4)::before,
    table.bets-ledger tr.parlay-row > td:nth-child(7)::before {
      display: inline-block;
      min-width: 56px;
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 600;
    }
    table.bets-ledger tr.parlay-row > td:nth-child(3)::before { content: "Stake"; }
    table.bets-ledger tr.parlay-row > td:nth-child(4)::before { content: "Odds"; }
    table.bets-ledger tr.parlay-row > td:nth-child(7)::before { content: "Payout"; }
    table.bets-ledger tr.parlay-row > td.actions {
      grid-area: actions;
      text-align: left;
      display: flex;
      gap: 6px;
      margin-top: 6px;
      padding-top: 8px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
    }
    table.bets-ledger tr.parlay-row > td.actions button.act {
      flex: 1;
      margin: 0;
      padding: 9px 0;
      font-size: 12px;
    }
    /* Apply W/L tint at the row level (desktop's per-cell tints don't
       paint anything because td backgrounds are now transparent). */
    table.bets-ledger tr.parlay-row.result-W { background: rgba(74, 222, 128, 0.10); border-color: rgba(74, 222, 128, 0.40); }
    table.bets-ledger tr.parlay-row.result-L { background: rgba(248, 113, 113, 0.10); border-color: rgba(248, 113, 113, 0.40); }
    /* Expanded: merge summary card and detail block into one card. */
    table.bets-ledger tr.parlay-row.expanded {
      margin-bottom: 0;
      border-bottom-left-radius: 0;
      border-bottom-right-radius: 0;
    }
    table.bets-ledger tr.parlay-detail { display: block; }
    table.bets-ledger tr.parlay-detail.hidden { display: none; }
    table.bets-ledger tr.parlay-detail > td {
      display: block;
      border: 1px solid var(--border);
      border-top: none;
      border-radius: 0 0 8px 8px;
      padding: 6px 14px 10px;
      margin-bottom: 10px;
      background: rgba(0, 0, 0, 0.20);
    }
    table.bets-ledger tr.parlay-detail.result-W > td { background: rgba(74, 222, 128, 0.06); border-color: rgba(74, 222, 128, 0.40); }
    table.bets-ledger tr.parlay-detail.result-L > td { background: rgba(248, 113, 113, 0.06); border-color: rgba(248, 113, 113, 0.40); }
    /* "Show older bets" toggle and empty-state row need to render as
       full-width blocks since the table is no longer a grid of cells. */
    table.bets-ledger tr.bets-older-toggle { display: block; }
    table.bets-ledger tr.bets-older-toggle > td {
      display: block;
      border: none;
      padding: 6px 0 12px;
      text-align: center;
      background: transparent;
    }
    table.bets-ledger tr.bets-older-row.older-hidden { display: none; }
    table.bets-ledger td.empty-msg {
      display: block;
      border: none;
      text-align: center;
      padding: 24px 8px;
    }
    /* Tighten leg detail rows: stack live status under the pitcher
       name so name + O/U + live K all fit on a phone. */
    .parlay-leg-list li {
      grid-template-columns: max-content minmax(0, 1fr) max-content;
      grid-template-areas:
        "leg name ou"
        "leg live live";
      gap: 2px 8px;
    }
    .parlay-leg-list li > :nth-child(1) { grid-area: leg; align-self: start; }
    .parlay-leg-list li > :nth-child(2) { grid-area: name; }
    .parlay-leg-list li > :nth-child(3) { grid-area: ou; text-align: right; }
    .parlay-leg-list li > :nth-child(4) { grid-area: live; font-size: 11px; }
    /* Bets toolbar: stamp + refresh button stack instead of competing. */
    .bets-toolbar { flex-wrap: wrap; gap: 6px; }
    .bets-toolbar > div { flex: 1 1 100%; display: flex; align-items: center; gap: 8px; }
    /* 5 totals cards: 3 cols fits Bets/Staked/Returned in row 1 and
       Net/ROI in row 2 without leaving a half-empty trailing row. */
    .bets-totals-card { grid-template-columns: repeat(3, 1fr); gap: 6px; }
    .bets-totals-card .report-stat { padding: 8px 10px; }
    .bets-totals-card .report-val { font-size: 16px; }
    .bets-totals-card .report-label { font-size: 10px; }
    .bets-totals-card .report-sub { font-size: 10px; }
    /* Reorder bets panel on phones: bets list first (with the live-K
       toolbar above it, since they're functionally tied), then totals
       and heatmap below, then the add-bet form at the bottom. DOM
       order stays the same so handlers + accessibility tree are
       unchanged — only visual order shifts. */
    #bets-panel { display: flex; flex-direction: column; }
    #bets-panel > .bets-quickstatus     { order: 0; display: block; margin-bottom: 10px; }
    #bets-panel > .bets-quickstatus:empty { display: none; }
    #bets-panel > .bets-toolbar         { order: 1; }
    #bets-panel > .bets-cards           { order: 2; }
    #bets-panel > .bankroll-card        { order: 3; margin-top: 14px; }
    #bets-panel > .bets-totals-card     { order: 4; margin-top: 8px; }
    #bets-panel > .totals-card-secondary { order: 5; }
    #bets-panel > .cal-wrap             { order: 6; }
    #bets-panel > .bets-form-card       { order: 7; }
  }
"""


def _force_refresh_status_html() -> str:
    """Force re-fetch button — lives in the header's status-row, NOT in
    the brand-actions cluster. Reason: the cluster also holds the green
    Refresh button, and accidental phone taps were a real concern. The
    status-row sits on the opposite side of the header on desktop and
    stacks below the brand area on phone — clear physical separation."""
    force_svg = (
        '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
    )
    force_confirm = (
        "Force re-fetch lines from The Odds API? "
        "Use when books have repriced mid-day. Continue?"
    )
    return (
        f'<form class="force-refresh-form" action="/refresh" method="post" '
        f'onsubmit="if (!confirm({force_confirm!r})) return false; document.body.classList.add(&#x27;loading&#x27;);">'
        f'<input type="hidden" name="force" value="1">'
        f'<button type="submit" id="force-refresh-btn" class="force-refresh-btn" '
        f'aria-label="Force re-fetch lines from Odds API" title="Force re-fetch lines from Odds API">'
        f'{force_svg}</button></form>'
    )


def _action_buttons_html() -> str:
    """Header utility cluster — sits in the top-right of the new header
    layout (right of the logo block). Returns three icon buttons:
      - theme-btn   — sun/moon SVG, toggles light/dark via [data-theme]
      - refresh-btn — circular green refresh action
      - admin-menu  — local-only `<details>` overflow for pipeline tools
    Status row (last-refresh · quota · health) is rendered separately
    so it can occupy a thin bottom strip of the header. Element IDs
    are preserved so existing JS click handlers attach unchanged."""
    sun_svg = (
        '<svg class="theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41'
        'M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>'
    )
    moon_svg = (
        '<svg class="theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
    )
    refresh_svg = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M21 12a9 9 0 1 1-3.5-7.1"/><polyline points="21 4 21 9 16 9"/></svg>'
    )
    settle_svg = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>'
        '<rect x="9" y="3" width="6" height="4" rx="1"/>'
        '<path d="m9 14 2 2 4-4"/></svg>'
    )
    return f"""<button type="button" id="theme-toggle" class="float-btn theme-btn" aria-label="Toggle light/dark" title="Toggle light/dark">{sun_svg}{moon_svg}</button>
    <button type="button" id="refresh-btn" class="float-btn refresh-btn" aria-label="Refresh data" title="Refresh data">{refresh_svg}</button>
    <form action="/settle" method="post" class="settle-form bets-only" onsubmit="document.body.classList.add('loading');">
      <button type="submit" class="float-btn settle-btn" aria-label="Settle yesterday" title="Settle yesterday">{settle_svg}</button>
    </form>
    <details class="admin-menu local-only">
      <summary aria-label="Local admin tools" title="Local admin tools">⋯</summary>
      <div class="admin-items">
        <form action="/refresh" method="post" onsubmit="document.body.classList.add('loading');">
          <button type="submit">Re-run pipeline</button>
        </form>
        <form action="/settle" method="post" onsubmit="document.body.classList.add('loading');">
          <button type="submit">Settle yesterday</button>
        </form>
        <form action="/push" method="post" onsubmit="document.body.classList.add('loading');">
          <button type="submit">Push to Air</button>
        </form>
      </div>
    </details>"""


def _render_js() -> str:
    """JavaScript that fetches CSVs, parses them, renders tables + Recent
    Results. Mirrors the per-row classification + sort logic that used to
    live in Python."""
    raw_base = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/output/"
    show_hitters_js = "true" if SHOW_HITTERS else "false"
    return f"""
(() => {{
  const FOCUS_MIN = {FOCUS_EDGE_MIN};
  const FOCUS_MAX = {FOCUS_EDGE_MAX};
  const INVESTIGATE = {INVESTIGATE_EDGE};
  const MIN_LINE_FOR_FOCUS = {MIN_LINE_FOR_FOCUS};
  const RAW_BASE = "{raw_base}";
  const SHOW_HITTERS = {show_hitters_js};

  function baseUrl() {{
    const h = location.hostname;
    if (h === "localhost" || h === "127.0.0.1" || h === "") return "./";
    if (/\.ts\.net$/i.test(h)) return "./";  // Air's Flask serves CSVs same-origin
    return RAW_BASE;
  }}

  function dateInChicago(offsetDays = 0) {{
    const d = new Date();
    d.setUTCDate(d.getUTCDate() + offsetDays);
    const fmt = new Intl.DateTimeFormat("en-CA", {{
      timeZone: "America/Chicago",
      year: "numeric", month: "2-digit", day: "2-digit",
    }});
    return fmt.format(d);
  }}

  async function fetchCSV(url) {{
    // Cache-bust every request: iOS Safari (especially PWA standalone)
    // ignores cache: "no-cache" when responses lack strong validators,
    // so the Refresh button silently served stale CSVs. A unique query
    // string per call sidesteps every cache layer (browser, CDN) without
    // changing what the server returns — Flask matches on path, query
    // string is ignored. cache: "no-store" belt-and-suspenders for the
    // rare engine that respects it.
    const sep = url.includes("?") ? "&" : "?";
    const r = await fetch(url + sep + "_t=" + Date.now(), {{ cache: "no-store" }});
    if (!r.ok) return null;
    return await r.text();
  }}

  function parseCSV(text) {{
    if (!text) return [];
    const lines = text.replace(/\\r/g, "").split("\\n").filter(l => l.length);
    if (lines.length < 1) return [];
    const headers = lines[0].split(",");
    return lines.slice(1).map(line => {{
      const values = splitCSVLine(line);
      const obj = {{}};
      headers.forEach((h, i) => {{ obj[h] = values[i] !== undefined ? values[i] : ""; }});
      return obj;
    }});
  }}

  // Minimal RFC4180-ish split: handles quoted fields containing commas.
  function splitCSVLine(line) {{
    const out = [];
    let cur = "";
    let inQ = false;
    for (let i = 0; i < line.length; i++) {{
      const c = line[i];
      if (c === '"') {{
        if (inQ && line[i+1] === '"') {{ cur += '"'; i++; }}
        else inQ = !inQ;
      }} else if (c === "," && !inQ) {{
        out.push(cur); cur = "";
      }} else {{
        cur += c;
      }}
    }}
    out.push(cur);
    return out;
  }}

  function f(v) {{
    if (v === "" || v === null || v === undefined) return null;
    const n = parseFloat(v);
    return isNaN(n) ? null : n;
  }}

  // pickEdge — returns the calibrated edge (cal_edge_v2) if present,
  // else falls back to raw `edge` for backward compat with pre-Platt
  // historical settled rows (pre-2026-05-11). All pick classification,
  // sorting, filtering, and bucketing routes through this. Path C bet
  // criterion is applied against pickEdge(r), not r.edge.
  function pickEdge(r) {{
    const cal = f(r.cal_edge_v2);
    return cal !== null ? cal : f(r.edge);
  }}

  function classify(edge) {{
    if (edge === null) return "noline";
    const a = Math.abs(edge);
    if (a >= INVESTIGATE) return "investigate";
    if (a >= FOCUS_MIN && a <= FOCUS_MAX) return "focus";
    return "noise";
  }}

  // Role-mismatch gate: book lines below MIN_LINE_FOR_FOCUS signal an
  // opener / reliever / spot appearance, but our model uses
  // season-average BF so it treats every row as a full start. Returns
  // false → the row is skipped from focus picks and parlay suggestions
  // even if its calibrated edge lands in the focus band. The row still
  // shows in the slate table (just not as a bet recommendation).
  function isBettableFocus(r) {{
    const e = pickEdge(r);
    if (e === null || classify(e) !== "focus") return false;
    const line = f(r.line);
    return line !== null && line >= MIN_LINE_FOR_FOCUS;
  }}

  function label(cls, dir) {{
    if (cls === "focus" && dir) return `Bet <strong>${{dir.toUpperCase()}}</strong>`;
    if (cls === "investigate" && dir) return `Verify <strong>${{dir.toUpperCase()}}</strong>?`;
    if (cls === "noline") return "No line";
    return "—";
  }}

  function dash(v) {{
    if (v === "" || v === null || v === undefined) return "—";
    return escapeHTML(String(v));
  }}

  // Format a 0..1 probability as a 1-decimal percent, with the same
  // null/empty behavior as dash(). Use this for any cell whose column
  // header carries a "%" — never let a raw decimal leak under a % label.
  function pct1(v) {{
    const n = f(v);
    if (n === null) return "—";
    return (n * 100).toFixed(1) + "%";
  }}

  // Parlay math — independent-leg approximation across games. Two
  // starters in the *same* game share K-environment (umpire zone,
  // weather, wind, scorekeeper) so independence breaks down there;
  // renderParlaySuggestions filters same-game combos before scoring.
  function americanToDecimal(odds) {{
    const o = f(odds);
    // o === 0 is meaningless ("0 American") — bad CSV, treat as no price.
    if (o === null || o === 0) return null;
    return o > 0 ? o / 100 + 1 : 100 / Math.abs(o) + 1;
  }}
  function decimalToAmerican(dec) {{
    // dec === 1 (no payout) would divide by zero in the underdog branch.
    if (dec === null || !isFinite(dec) || dec <= 1) return null;
    if (dec >= 2) return Math.round((dec - 1) * 100);
    return Math.round(-100 / (dec - 1));
  }}
  function combos(arr, k) {{
    const out = [];
    const helper = (start, current) => {{
      if (current.length === k) {{ out.push(current.slice()); return; }}
      for (let i = start; i < arr.length; i++) {{
        current.push(arr[i]);
        helper(i + 1, current);
        current.pop();
      }}
    }};
    helper(0, []);
    return out;
  }}
  // Convert a focus-pick row into a normalized leg, or null if it can't
  // be priced (missing odds on the picked side, missing novig, etc.).
  function pickLegFromRow(r) {{
    const edge = pickEdge(r);
    if (edge === null) return null;
    const dir = edge > 0 ? "over" : "under";
    const odds = dir === "over" ? f(r.over_odds) : f(r.under_odds);
    const dec = americanToDecimal(odds);
    if (dec === null) return null;
    const pOver = f(r.p_over);
    const novigOver = f(r.novig_over);
    if (pOver === null || novigOver === null) return null;
    const hitProb = dir === "over" ? pOver : 1 - pOver;
    const novigP = dir === "over" ? novigOver : 1 - novigOver;
    const pidNum = parseInt(r.pitcher_id, 10);
    const gpkNum = parseInt(r.game_pk, 10);
    const lj = r.opp_lineup_json;
    const lineupPending = !lj || lj === "[]" || lj === "";
    return {{
      pitcher: r.pitcher || "",
      pitcher_id: isNaN(pidNum) ? null : pidNum,
      game_pk: isNaN(gpkNum) ? null : gpkNum,
      line: r.line,
      dir,
      odds,
      decOdds: dec,
      hitProb,
      novigP,
      edge,
      gameTimeISO: r.game_datetime_utc || "",
      lineupPending,
    }};
  }}
  function evaluateParlay(legs) {{
    const dec = legs.reduce((acc, l) => acc * l.decOdds, 1);
    const hit = legs.reduce((acc, l) => acc * l.hitProb, 1);
    const hasNovig = legs.every(l => l.novigP !== null && l.novigP !== undefined);
    const novig = hasNovig ? legs.reduce((acc, l) => acc * l.novigP, 1) : null;
    return {{
      legs,
      combinedAmer: decimalToAmerican(dec),
      combinedDec: dec,
      combinedHit: hit,
      combinedNovig: novig,
      combinedEdge: novig === null ? null : hit - novig,
      // EV per $1 staked (same convention as ev_over / ev_under).
      ev: hit * (dec - 1) - (1 - hit),
    }};
  }}

  function escapeHTML(s) {{
    return s.replace(/[&<>"']/g, c => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }}[c]));
  }}

  // Format MLB Stats API gameDate (UTC ISO, e.g. "2026-05-01T17:10:00Z")
  // to Central time, e.g. "12:10 PM CT". Always labels "CT" rather than
  // CDT/CST so the column reads consistently year-round.
  const _CT_FMT = new Intl.DateTimeFormat("en-US", {{
    timeZone: "America/Chicago",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }});
  function formatGameTime(iso) {{
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return _CT_FMT.format(d) + " CT";
  }}

  // "vs Opp" when the pitcher is the home team (opp bats top of innings),
  // "@ Opp" when away (opp bats bottom). Lets the user infer whether the
  // pitcher is on the mound in top vs bottom of each inning at a glance.
  // Falls back to "vs " for legacy rows missing is_home so older settled
  // CSVs render unchanged.
  function oppPrefix(r) {{
    const v = r ? r.is_home : null;
    if (v === false || v === "False" || v === "false" || v === 0 || v === "0") return "@ ";
    return "vs ";
  }}

  // Full MLB team name → 3-letter code, used in tight card layouts (Today's
  // Picks) where the full name eats too much horizontal room. Falls through
  // to the original string if we don't recognize the team.
  const _TEAM_ABBR = {{
    "Arizona Diamondbacks": "ARI", "Athletics": "ATH",
    "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU",
    "Kansas City Royals": "KC", "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN",
    "New York Mets": "NYM", "New York Yankees": "NYY",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
  }};
  function teamAbbr(name) {{
    if (!name) return "";
    return _TEAM_ABBR[name] || name;
  }}

  // Compact game time for tight card layouts: "1:10p" instead of "1:10 PM CT".
  // CT is implied (whole site is CT-anchored) and the lowercase a/p saves
  // two characters per slot — meaningful when packing 7+ cards across.
  function formatGameTimeShort(iso) {{
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    const s = _CT_FMT.format(d);
    return s.replace(" AM", "a").replace(" PM", "p");
  }}

  // Compact "Bottom 5th" → "B5" for narrow live-cell slots. Strips the
  // ordinal suffix off the inning ("5th" → "5") and replaces the verbose
  // half-inning state with one letter ("Top"→T, "Bottom"→B). "Mid"/"End"
  // keep three letters since they're rare and need to be readable.
  function compactInning(state, ord) {{
    if (!ord) return "Live";
    const m = String(ord).match(/^(\\d+)/);
    const num = m ? m[1] : ord;
    let prefix = "";
    if (state === "Top") prefix = "T";
    else if (state === "Bottom") prefix = "B";
    else if (state === "Middle") prefix = "Mid ";
    else if (state === "End") prefix = "End ";
    return prefix + num;
  }}

  // Render the time-cell HTML for one slate row. Returns {{html, locked}}.
  // Three states:
  //   1) Live  — "● Top 5 · 4K"  (game in progress, K count if known)
  //   2) Final — "Final · 7K"    (game over, K count if known)
  //   3) Sched — "7:10 PM CT (in 47m)" + "urgent" class under 30 min
  // 'locked' = bet window is closed (game started); caller adds a CSS
  // class to demote the whole row.
  function renderGameTimeCell(iso, live) {{
    if (!iso) return {{ html: "—", locked: false }};
    const d = new Date(iso);
    if (isNaN(d.getTime())) return {{ html: "—", locked: false }};
    const timeStr = _CT_FMT.format(d) + " CT";

    if (live && live.status === "Live" && live.done) {{
      // Pitcher was pulled — Ks are locked even though the game is still
      // going, so render like Final but with a "Pulled" label so the user
      // knows the over/under decision is callable now.
      const ksStr = (live.ks !== null && live.ks !== undefined)
        ? `<span class="live-ks">${{live.ks}}K</span>` : "—";
      return {{
        html: `<span class="final-text">Pulled</span> · ${{ksStr}}`,
        locked: true,
      }};
    }}
    if (live && live.status === "Live") {{
      const inning = live.current_inning
        ? escapeHTML(compactInning(live.inning_state, live.current_inning))
        : "Live";
      const ksStr = (live.ks !== null && live.ks !== undefined)
        ? `<span class="live-ks">${{live.ks}}K</span>` : "";
      return {{
        html: `<span class="live-dot" title="${{escapeHTML(live.detailed || "Live")}}"></span><span class="live-text">${{inning}}</span>${{ksStr}}`,
        locked: true,
      }};
    }}
    if (live && live.status === "Final") {{
      const ksStr = (live.ks !== null && live.ks !== undefined) ? `${{live.ks}}K` : "—";
      return {{
        html: `<span class="final-text">Final</span> · <span class="live-ks">${{ksStr}}</span>`,
        locked: true,
      }};
    }}

    const diffMs = d.getTime() - Date.now();
    if (diffMs < 0) {{
      // Past first pitch but no live data yet (delayed status update,
      // or pre-game scheduled-vs-actual lag). Treat as locked.
      return {{
        html: `${{timeStr}} <span class="time-started">(started)</span>`,
        locked: true,
      }};
    }}
    const diffMin = Math.round(diffMs / 60000);
    let rel = "";
    if (diffMin === 0) rel = "starting now";
    else if (diffMin < 60) rel = `in ${{diffMin}}m`;
    else if (diffMin < 60 * 24) {{
      const h = Math.floor(diffMin / 60);
      const m = diffMin % 60;
      rel = m ? `in ${{h}}h ${{m}}m` : `in ${{h}}h`;
    }}
    const urgent = diffMin <= 30 ? " urgent" : "";
    const relHTML = rel ? `<span class="time-rel${{urgent}}">${{rel}}</span>` : "";
    return {{ html: `${{timeStr}} ${{relHTML}}`, locked: false }};
  }}

  // Fetch live K + game status for the slate's pitchers directly from
  // the public MLB Stats API. No auth, no proxy needed — works on the
  // public URL and locally. Returns Map<pitcher_id, liveData>.
  // One /schedule call + one /boxscore per in-progress-or-final game.
  async function fetchLiveKsPublic(slateRows, dateISO) {{
    const byPid = new Map();
    const gameIds = new Set();
    for (const r of slateRows) {{
      const pid = parseInt(r.pitcher_id, 10);
      const gpk = parseInt(r.game_pk, 10);
      if (isNaN(pid) || isNaN(gpk)) continue;
      byPid.set(pid, gpk);
      gameIds.add(gpk);
    }}
    if (!gameIds.size) return new Map();

    let schedJson;
    try {{
      const r = await fetch(
        `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${{dateISO}}&hydrate=linescore`,
        {{ cache: "no-cache" }},
      );
      if (!r.ok) return new Map();
      schedJson = await r.json();
    }} catch (e) {{
      return new Map();
    }}

    const statusByGpk = new Map();
    for (const dateBlock of (schedJson.dates || [])) {{
      for (const game of (dateBlock.games || [])) {{
        const gpk = game.gamePk;
        if (!gpk) continue;
        const status = game.status || {{}};
        const ls = game.linescore || {{}};
        statusByGpk.set(gpk, {{
          status: status.abstractGameState || "Preview",
          detailed: status.detailedState || "",
          current_inning: ls.currentInningOrdinal || null,
          inning_state: ls.inningState || null,
        }});
      }}
    }}

    // Boxscore only for games that aren't Preview — saves traffic and
    // dodges 404s on games that haven't begun yet.
    const interesting = [...gameIds].filter(gpk => {{
      const s = statusByGpk.get(gpk);
      return s && s.status !== "Preview";
    }});
    const boxes = await Promise.all(interesting.map(async gpk => {{
      try {{
        const r = await fetch(
          `https://statsapi.mlb.com/api/v1/game/${{gpk}}/boxscore`,
          {{ cache: "no-cache" }},
        );
        if (!r.ok) return [gpk, null];
        return [gpk, await r.json()];
      }} catch (e) {{ return [gpk, null]; }}
    }}));
    const boxByGpk = new Map(boxes);

    const out = new Map();
    for (const [pid, gpk] of byPid) {{
      const status = statusByGpk.get(gpk) || {{ status: "Preview" }};
      const result = {{
        ks: null,
        done: false,
        status: status.status,
        detailed: status.detailed || "",
        current_inning: status.current_inning,
        inning_state: status.inning_state,
        pitches: null,
        ip: null,
      }};
      const box = boxByGpk.get(gpk);
      if (box) {{
        const key = `ID${{pid}}`;
        for (const side of ["home", "away"]) {{
          const team = (box.teams && box.teams[side]) || {{}};
          const players = team.players || {{}};
          if (players[key]) {{
            const stats = players[key].stats && players[key].stats.pitching;
            if (stats) {{
              const ks = stats.strikeOuts;
              if (ks !== undefined && ks !== null && ks !== "") {{
                const n = parseInt(ks, 10);
                if (!isNaN(n)) result.ks = n;
              }}
              const rawP = stats.numberOfPitches != null
                ? stats.numberOfPitches : stats.pitchesThrown;
              if (rawP !== undefined && rawP !== null && rawP !== "") {{
                const np = parseInt(rawP, 10);
                if (!isNaN(np)) result.pitches = np;
              }}
              if (stats.inningsPitched !== undefined && stats.inningsPitched !== null && stats.inningsPitched !== "") {{
                result.ip = String(stats.inningsPitched);
              }}
            }}
            // pitchers[] is ordered by appearance; if our pitcher isn't
            // the last entry, they've been pulled and their Ks are locked.
            const pitchers = team.pitchers || [];
            if (pitchers.length && pitchers.includes(pid) && pitchers[pitchers.length - 1] !== pid) {{
              result.done = true;
            }}
            break;
          }}
        }}
      }}
      out.set(pid, result);
    }}
    return out;
  }}

  // Walk the rendered pitcher tab and refresh every live-aware cell
  // (table-row time cells + hero-card live stats). Pure DOM read/write,
  // no fetches — called once when fetchLiveKsPublic resolves and again
  // on a 60s tick so countdowns stay accurate and rows flip locked the
  // moment first pitch passes.
  let _liveByPid = new Map();

  // Open-parlay index for the pitcher tab's hero badges. Populated from
  // /api/bets (local-only — public URL gets nothing, badges silently
  // disappear). Keyed by pitcher_id so renderHeroPickCard can look up
  // every open parlay this pitcher is a leg in.
  //
  // _betsByPid: Map<pitcher_id, Array of leg-context objects (label,
  //   parlayId, ou, line, legCount, otherLegs, site, stake, odds,
  //   payout, isFreeEntry).
  // _openParlays: ordered list of open parlays so labels (P1, P2, …)
  //   stay stable across renders within a single page load.
  let _betsByPid = new Map();
  let _openParlays = [];

  function indexBetsByPitcher(state) {{
    _betsByPid = new Map();
    _openParlays = [];
    if (!state || !Array.isArray(state.bets)) return;
    // Open = result not yet recorded. Settled W/L bets are intentionally
    // excluded — the card's own banner already shows HIT/MISS, and
    // historical bets clutter today's slate. Sort by date ASC then id
    // so P-labels are deterministic across re-renders.
    const open = state.bets
      .filter(b => b.result === null && Array.isArray(b.legs) && b.legs.length > 0)
      .sort((a, b) => {{
        const da = a.date || ""; const db = b.date || "";
        if (da !== db) return da < db ? -1 : 1;
        const ia = a.id || ""; const ib = b.id || "";
        return ia < ib ? -1 : (ia > ib ? 1 : 0);
      }});
    open.forEach((b, i) => {{
      const label = `P${{i + 1}}`;
      _openParlays.push({{
        id: b.id, label, legs: b.legs,
        site: b.site || "", stake: b.stake || 0, odds: b.odds || 0,
        isFreeEntry: !!b.free_entry,
      }});
      for (const leg of b.legs) {{
        const pid = leg.pitcher_id;
        if (!pid) continue;
        if (!_betsByPid.has(pid)) _betsByPid.set(pid, []);
        _betsByPid.get(pid).push({{
          parlayId: b.id,
          label,
          legCount: b.legs.length,
          ou: leg.ou,
          line: leg.line,
          otherLegs: b.legs.filter(l => l !== leg),
          site: b.site || "",
          stake: b.stake || 0,
          odds: b.odds || 0,
          isFreeEntry: !!b.free_entry,
        }});
      }}
    }});
  }}

  // Fetch + index. Errors swallowed: the bets API is local-only, so on
  // the public URL the fetch 404s and badges just don't render. Call
  // before first pitcher-tab paint so the initial cards already include
  // badges.
  async function fetchBetsForPitcherTab() {{
    if (!isBets()) return null;
    try {{
      const r = await fetch("/api/bets", {{ cache: "no-cache" }});
      if (!r.ok) return null;
      const state = await r.json();
      indexBetsByPitcher(state);
      return state;
    }} catch (e) {{ return null; }}
  }}

  // Live state for one bet leg given the pitcher_id whose card we're
  // rendering. Reuses legHitState — same logic as the Bets-tab leg cells
  // so the two views can never disagree. Returns "hit"/"miss"/null.
  function betLegState(pid, ou, line) {{
    const live = _liveByPid.get(pid);
    if (!live) return null;
    const lineToUse = (line === null || line === undefined || line === "")
      ? live.line : line;
    return legHitState(live.ks, lineToUse, ou, live.status, live.done);
  }}

  // Build a multi-line tooltip string for one bet badge. Plain text with
  // newline separators — modern browsers render newlines in title
  // attrs, and a custom hover popup is overkill for a local tool.
  function betBadgeTooltip(entry) {{
    const lines = [];
    const head = `${{entry.label}} · ${{entry.legCount}}-leg parlay${{entry.site ? " · " + entry.site : ""}}`;
    lines.push(head);
    const lineStr = (entry.line === null || entry.line === undefined || entry.line === "")
      ? "" : ` ${{entry.line}}`;
    const dirWord = entry.ou === "U" ? "UNDER" : "OVER";
    lines.push(`This leg: ${{dirWord}}${{lineStr}}`);
    if (entry.otherLegs.length) {{
      lines.push("Other legs:");
      for (const l of entry.otherLegs) {{
        const olLine = (l.line === null || l.line === undefined || l.line === "")
          ? "" : ` ${{l.line}}`;
        const olDir = l.ou === "U" ? "U" : "O";
        const olName = l.pitcher || "(pending name)";
        // Other-leg live status — gives parlay-level "is anything dead
        // already?" context without crowding the badge itself.
        const olState = l.pitcher_id ? betLegState(l.pitcher_id, l.ou, l.line) : null;
        const tag = olState === "hit" ? " ✓" : olState === "miss" ? " ✗" : "";
        lines.push(`  • ${{olName}} ${{olDir}}${{olLine}}${{tag}}`);
      }}
    }}
    if (entry.stake || entry.odds) {{
      const potential = (entry.stake && entry.odds)
        ? ` → $${{(entry.stake * entry.odds).toFixed(2)}}` : "";
      const stakeStr = entry.isFreeEntry ? "Free entry" : `$${{(entry.stake || 0).toFixed(2)}} stake`;
      lines.push(`${{stakeStr}}${{potential}}`);
    }}
    return lines.join("\\n");
  }}

  function renderBetBadge(entry, pid) {{
    const state = betLegState(pid, entry.ou, entry.line);
    const cls = state === "hit" ? "hit" : state === "miss" ? "miss" : "pending";
    const lineStr = (entry.line === null || entry.line === undefined || entry.line === "")
      ? "" : ` ${{entry.line}}`;
    const dirShort = entry.ou === "U" ? "U" : "O";
    const tip = betBadgeTooltip(entry);
    return `<span class="pick-card-bet-badge ${{cls}}" title="${{escapeHTML(tip)}}" data-parlay-id="${{escapeHTML(entry.parlayId)}}"><span class="bb-label">${{entry.label}}</span>${{dirShort}}${{escapeHTML(lineStr)}}</span>`;
  }}

  function renderBetBadgesRow(pid) {{
    if (!pid || isNaN(pid)) return "";
    const entries = _betsByPid.get(pid);
    if (!entries || !entries.length) return "";
    const badges = entries.map(e => renderBetBadge(e, pid)).join("");
    return `<div class="pick-card-bets" data-bet-badges>${{badges}}</div>`;
  }}

  // Repaint just the bet-badge rows in place (called from the same
  // 60s tick as the live cells). Cheap: an entry's HTML is small and
  // we only touch cards that actually have badges.
  function repaintBetBadges() {{
    document.querySelectorAll(".pick-card[data-pitcher-id]").forEach(card => {{
      const pid = parseInt(card.dataset.pitcherId, 10);
      if (isNaN(pid)) return;
      const existing = card.querySelector("[data-bet-badges]");
      const html = renderBetBadgesRow(pid);
      if (html) {{
        if (existing) {{
          existing.outerHTML = html;
        }} else {{
          // No existing row — inject after the pitcher line so badges
          // land in the same slot as on initial render.
          const pitcherLine = card.querySelector(".pick-card-pitcher");
          if (pitcherLine) pitcherLine.insertAdjacentHTML("afterend", html);
        }}
      }} else if (existing) {{
        // Bets removed since last paint — drop the row.
        existing.remove();
      }}
    }});
  }}

  // 60s auto-poll for live MLB stats on the pitcher tab. Self-stops
  // once every tracked game is Final; skipped while the browser tab is
  // hidden so we don't hammer the public MLB API in a background tab.
  let _pitcherLivePollTimer = null;
  let _pitcherLivePollRows = [];
  let _pitcherLivePollDate = "";
  function stopPitcherLivePoll() {{
    if (_pitcherLivePollTimer) {{
      clearInterval(_pitcherLivePollTimer);
      _pitcherLivePollTimer = null;
    }}
  }}
  async function pitcherLivePollTick() {{
    if (document.hidden) return;
    if (!_pitcherLivePollRows.length || !_pitcherLivePollDate) return;
    try {{
      const byPid = await fetchLiveKsPublic(_pitcherLivePollRows, _pitcherLivePollDate);
      _liveByPid = byPid;
      repaintGameTimeCells();
      // Stop once nothing is Preview/Live anymore — every game has
      // gone Final, so further polls would be wasted traffic.
      const vals = [..._liveByPid.values()];
      const stillActive = vals.some(v => v && (v.status === "Preview" || (v.status === "Live" && !v.done)));
      if (vals.length && !stillActive) stopPitcherLivePoll();
    }} catch (e) {{ /* swallow — next tick retries */ }}
  }}
  async function startPitcherLivePoll(rows, dateISO) {{
    stopPitcherLivePoll();
    _pitcherLivePollRows = rows;
    _pitcherLivePollDate = dateISO;
    await pitcherLivePollTick();
    _pitcherLivePollTimer = setInterval(pitcherLivePollTick, 60000);
  }}
  function repaintGameTimeCells() {{
    document.querySelectorAll("td.gametime[data-game-iso]").forEach(td => {{
      const iso = td.dataset.gameIso || "";
      const pid = parseInt(td.dataset.pitcherId, 10);
      const live = isNaN(pid) ? null : _liveByPid.get(pid);
      const cell = renderGameTimeCell(iso, live);
      td.innerHTML = cell.html;
      const tr = td.closest("tr");
      if (tr) tr.classList.toggle("row-locked", cell.locked);
    }});
    document.querySelectorAll(".pick-card[data-pitcher-id]").forEach(card => {{
      const iso = card.dataset.gameIso || "";
      const pid = parseInt(card.dataset.pitcherId, 10);
      const line = card.dataset.line || "";
      const dir = card.dataset.dir || "";
      const live = isNaN(pid) ? null : _liveByPid.get(pid);
      const cell = renderHeroLive(iso, live, line, dir);
      const valEl = card.querySelector(".card-live-val");
      const labelEl = card.querySelector(".card-live-label");
      if (valEl) {{
        valEl.className = `pick-card-stat-val card-live-val ${{cell.cls}}`;
        valEl.innerHTML = cell.html;
      }}
      if (labelEl) labelEl.textContent = cell.label;
      card.classList.toggle("locked", cell.locked);
      // Pulse line — only visible while the pitcher is actively
      // throwing. Hidden pre-game, post-pull, and after Final.
      const pulseEl = card.querySelector("[data-pulse]");
      if (pulseEl) {{
        const pulseHTML = renderHeroPulse(live);
        if (pulseHTML) {{
          pulseEl.innerHTML = pulseHTML;
          pulseEl.style.display = "";
        }} else {{
          pulseEl.innerHTML = "";
          pulseEl.style.display = "none";
        }}
      }}
      // Outcome flips the card to its solid HIT/MISS fill + shows the
      // top banner. Toggle both classes off first so we never end up
      // with both.
      card.classList.remove("hit", "miss");
      if (cell.outcome) card.classList.add(cell.outcome);
      const chip = card.querySelector("[data-outcome-chip]");
      if (chip) {{
        if (cell.outcome) {{
          chip.className = `pick-card-banner ${{cell.outcome}}`;
          chip.textContent = verdictLabel(cell.outcome);
          chip.style.display = "";
        }} else {{
          chip.className = "pick-card-banner";
          chip.style.display = "none";
          chip.textContent = "";
        }}
      }}
    }});
    // Bet badges share the same 60s tick — repaint after the live cells
    // so a leg flipping HIT/MISS changes the badge color in lockstep
    // with the card's main banner.
    repaintBetBadges();
  }}

  function sortKey(r, projField) {{
    const edge = pickEdge(r);
    const cls = classify(edge);
    const clsRank = {{ focus: 0, investigate: 1, noise: 2, noline: 3 }}[cls];
    const edgeRank = edge === null ? 0 : -Math.abs(edge);
    const proj = -(f(r[projField]) || 0);
    return [clsRank, edgeRank, proj];
  }}

  function sortRows(rows, projField) {{
    return rows.slice().sort((a, b) => {{
      const ka = sortKey(a, projField), kb = sortKey(b, projField);
      for (let i = 0; i < ka.length; i++) {{
        if (ka[i] !== kb[i]) return ka[i] - kb[i];
      }}
      return 0;
    }});
  }}

  // Tooltip body for a projection cell — shows the v0/v1/v2/ML stack
  // so you can eyeball where the v2 number actually came from. Useful
  // while shadow-ML is riding along: see at a glance whether ML
  // disagrees with v2 and by how much.
  function projTooltip(r) {{
    const lines = [];
    const v0 = f(r.proj_ks_v0);
    const v1 = f(r.proj_ks_v1);
    const v2 = f(r.proj_ks_v2);
    const ml = f(r.proj_ks_ml);
    if (v0 !== null) lines.push(`v0 ${{v0.toFixed(2)}} — season K%`);
    if (v1 !== null) lines.push(`v1 ${{v1.toFixed(2)}} — recent-form blend`);
    if (v2 !== null) lines.push(`v2 ${{v2.toFixed(2)}} — SwStr + park`);
    if (ml !== null) lines.push(`ML ${{ml.toFixed(2)}} — XGBoost (shadow)`);
    return lines.length ? lines.join("\\n") : "";
  }}

  // Lineup status: opp_lineup_json is "[]" until lineups post (~3-4 hrs
  // pre-first-pitch). Pre-lineup projections fall back to season-avg opp K%,
  // so edge can shift meaningfully once the card lands — see Blind Spot #4
  // analysis: mean |edge drift| 0.085 in empty→filled vs 0.027 baseline.
  function lineupPendingChip(r) {{
    const lj = r.opp_lineup_json;
    if (lj && lj !== "[]" && lj !== "") return "";
    return `<span class="lineup-pending">Lineup TBD</span>`;
  }}

  function pitcherRow(r) {{
    const edge = pickEdge(r);
    const cls = classify(edge);
    const dir = edge === null || edge === 0 ? "" : (edge > 0 ? "over" : "under");
    const edgeStr = edge === null ? "—" : (edge > 0 ? "+" : "") + (edge * 100).toFixed(1) + "%";
    const proj = r.proj_ks_v2 || r.proj_ks_v1 || "";
    const projTip = projTooltip(r);
    const projAttr = projTip ? ` title="${{escapeHTML(projTip)}}"` : "";
    const overTitle = r.over_book ? ` title="Best price at ${{escapeHTML(r.over_book)}}"` : "";
    const underTitle = r.under_book ? ` title="Best price at ${{escapeHTML(r.under_book)}}"` : "";
    const novigTitle = r.n_books ? ` title="Median across ${{escapeHTML(r.n_books)}} books"` : "";

    // Time cell renders + decides locked-out for the row. _liveByPid is
    // empty on first render — repainted in place once fetchLiveKsPublic
    // resolves and again on every 60s tick.
    const iso = r.game_datetime_utc || "";
    const pidNum = parseInt(r.pitcher_id, 10);
    const live = isNaN(pidNum) ? null : _liveByPid.get(pidNum);
    const cell = renderGameTimeCell(iso, live);
    const rowCls = `row-${{cls}}` + (dir ? ` dir-${{dir}}` : "") + (cell.locked ? " row-locked" : "");
    const tagCls = `tag-${{cls}}` + (dir ? ` tag-dir-${{dir}}` : "");
    const isoAttr = iso ? ` data-game-iso="${{escapeHTML(iso)}}"` : "";
    const pidAttr = !isNaN(pidNum) ? ` data-pitcher-id="${{pidNum}}"` : "";

    return `<tr class="${{rowCls}}">
      <td class="player">${{escapeHTML(r.pitcher || "")}}${{lineupPendingChip(r)}}</td>
      <td>${{oppPrefix(r)}}${{escapeHTML(r.opp || "")}}</td>
      <td class="gametime"${{isoAttr}}${{pidAttr}}>${{cell.html}}</td>
      <td class="num proj-cell"${{projAttr}}>${{dash(proj)}}</td>
      <td class="num">${{dash(r.line)}}</td>
      <td class="num"${{overTitle}}>${{dash(r.over_odds)}}</td>
      <td class="num"${{underTitle}}>${{dash(r.under_odds)}}</td>
      <td class="num">${{pct1(r.p_over)}}</td>
      <td class="num"${{novigTitle}}>${{pct1(r.novig_over)}}</td>
      <td class="num edge ${{dir}}">${{edgeStr}}</td>
      <td class="badge"><span class="tag ${{tagCls}}">${{label(cls, dir)}}</span></td>
    </tr>`;
  }}

  // Outcome of a card's pick given current K count + line + game state.
  // Mirrors legHitState() on the bets tab. Returns "hit" | "miss" | null.
  // dir is "over" | "under" (the model's recommendation for this card).
  // done=true means the pitcher has been pulled — locks the count even
  // before the game goes Final.
  function pickOutcome(ks, line, dir, status, done) {{
    if (ks === null || ks === undefined) return null;
    const lineNum = parseFloat(line);
    if (isNaN(lineNum)) return null;
    if (ks > lineNum) return dir === "over" ? "hit" : "miss";
    if (status === "Final" || done) return dir === "under" ? "hit" : "miss";
    return null;
  }}

  // Color-blind redundancy: prefix HIT / MISS labels with a glyph so
  // the verdict reads correctly even if the green/red fill doesn't
  // distinguish for the reader.
  function verdictLabel(outcome) {{
    if (outcome === "hit") return "✓ HIT";
    if (outcome === "miss") return "✗ MISS";
    return "";
  }}

  // "Pulse" line beneath the hero card stats — pitches + IP. Only
  // emitted while the game is actively Live (not Preview, not Final,
  // not after the pitcher's pulled). Returns "" otherwise so the slot
  // collapses cleanly. Always rendered through the [data-pulse] anchor
  // so repaintGameTimeCells can swap it without re-rendering the card.
  function renderHeroPulse(live) {{
    if (!live || live.status !== "Live" || live.done) return "";
    const parts = [];
    if (live.pitches !== null && live.pitches !== undefined) {{
      parts.push(`<strong>${{live.pitches}}</strong> P`);
    }}
    if (live.ip) parts.push(`${{escapeHTML(String(live.ip))}} IP`);
    if (!parts.length) return "";
    return parts.join(" · ");
  }}

  // Live cell content for a hero card. Mirrors the time-cell logic in
  // renderGameTimeCell but shorter — the matchup line above already
  // shows the scheduled clock time, so the live cell just adds the
  // status delta ("in 47m", "● B5 4K", "Final · 7K"). Returns
  // {{html, label, cls, locked, outcome}} where outcome is "hit"/"miss"/null.
  function renderHeroLive(iso, live, line, dir) {{
    const lineNum = parseFloat(line);
    const outcome = live ? pickOutcome(live.ks, line, dir, live.status, live.done) : null;
    if (live && live.status === "Live" && live.done) {{
      const ksHTML = (live.ks !== null && live.ks !== undefined)
        ? `<span class="live-ks">${{live.ks}}K</span>` : "—";
      return {{
        html: `Pulled · ${{ksHTML}}`,
        label: "Pulled",
        cls: "live-final",
        locked: true,
        outcome,
      }};
    }}
    if (live && live.status === "Live") {{
      const inning = live.current_inning
        ? escapeHTML(compactInning(live.inning_state, live.current_inning))
        : "Live";
      const ksHTML = (live.ks !== null && live.ks !== undefined)
        ? `<span class="live-ks">${{live.ks}}K</span>` : "";
      // "5 of 6.5" pace cue under the value when we know both line + ks.
      const paceLabel = (!isNaN(lineNum) && live.ks !== null && live.ks !== undefined)
        ? `${{live.ks}} of ${{lineNum.toFixed(1)}}` : "Live";
      return {{
        html: `<span class="live-dot"></span>${{inning}}${{ksHTML}}`,
        label: paceLabel,
        cls: "live-now",
        locked: true,
        outcome,
      }};
    }}
    if (live && live.status === "Final") {{
      const ksHTML = (live.ks !== null && live.ks !== undefined)
        ? `<span class="live-ks">${{live.ks}}K</span>` : "—";
      return {{
        html: `Final · ${{ksHTML}}`,
        label: "Final",
        cls: "live-final",
        locked: true,
        outcome,
      }};
    }}
    // Pre-game: relative time (matchup line above already has the clock).
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d.getTime())) {{
      return {{ html: "—", label: "Status", cls: "live-pending", locked: false, outcome: null }};
    }}
    const diffMs = d.getTime() - Date.now();
    if (diffMs < 0) {{
      return {{ html: "started", label: "Status", cls: "live-pending", locked: true, outcome: null }};
    }}
    const diffMin = Math.round(diffMs / 60000);
    let rel;
    if (diffMin < 60) rel = `in ${{diffMin}}m`;
    else if (diffMin < 60 * 24) {{
      const h = Math.floor(diffMin / 60);
      const m = diffMin % 60;
      rel = m ? `in ${{h}}h ${{m}}m` : `in ${{h}}h`;
    }} else rel = "scheduled";
    return {{ html: rel, label: "Starts", cls: "live-pending", locked: false, outcome: null }};
  }}

  // Hero card for a single focus pick — surfaces the actionable info
  // (pick direction, line, edge) above the dense table.
  // Build the "Why this pick" disclosure body — one row per model
  // input with a ↑K / ↓K / • tilt indicator. Compares each value to a
  // league pivot rather than a tier label so the meaning is honest:
  // a tilt simply says "this input pushes Ks up vs. league average".
  // The card's bet direction is independent — useful when most inputs
  // tilt opposite the bet (rare, but signals model is reacting to one
  // strong factor).
  function renderHeroWhy(r) {{
    const swstr = f(r.swstr_pct);
    const recent = f(r.recent_k_pct);
    const season = f(r.season_k_pct);
    const oppK = f(r.opp_k_pct);
    const park = f(r.park_factor);
    const proj = f(r.proj_ks_v2);
    const line = f(r.line);
    const tilt = (v, mean, eps) => {{
      const e = eps == null ? 0.005 : eps;
      const d = v - mean;
      if (d > e) return {{ sym: "↑K", cls: "k-up" }};
      if (d < -e) return {{ sym: "↓K", cls: "k-down" }};
      return {{ sym: "•", cls: "k-flat" }};
    }};
    const pct1 = v => (v * 100).toFixed(1) + "%";
    const items = [];
    const pitcherK = recent !== null ? recent : season;
    if (pitcherK !== null) {{
      const t = tilt(pitcherK, 0.22);
      const lbl = recent !== null ? "Pitcher K% (recent)" : "Pitcher K% (season)";
      items.push(`<li><span class="why-label">${{lbl}}</span><span class="why-val">${{pct1(pitcherK)}}</span><span class="why-tilt ${{t.cls}}">${{t.sym}}</span></li>`);
    }}
    if (swstr !== null) {{
      const t = tilt(swstr, 0.115);
      items.push(`<li><span class="why-label">SwStr%</span><span class="why-val">${{pct1(swstr)}}</span><span class="why-tilt ${{t.cls}}">${{t.sym}}</span></li>`);
    }}
    if (oppK !== null) {{
      const t = tilt(oppK, 0.22);
      items.push(`<li><span class="why-label">Opp lineup K%</span><span class="why-val">${{pct1(oppK)}}</span><span class="why-tilt ${{t.cls}}">${{t.sym}}</span></li>`);
    }}
    if (park !== null) {{
      const t = tilt(park, 1.00, 0.01);
      items.push(`<li><span class="why-label">Park factor</span><span class="why-val">${{park.toFixed(2)}}</span><span class="why-tilt ${{t.cls}}">${{t.sym}}</span></li>`);
    }}
    if (proj !== null && line !== null) {{
      const delta = proj - line;
      const cls = delta > 0.05 ? "k-up" : delta < -0.05 ? "k-down" : "k-flat";
      const sym = delta > 0.05 ? "↑K" : delta < -0.05 ? "↓K" : "•";
      const sign = delta >= 0 ? "+" : "";
      items.push(`<li><span class="why-label">Proj vs line</span><span class="why-val">${{proj.toFixed(2)}} vs ${{line.toFixed(1)}}</span><span class="why-tilt ${{cls}}">${{sym}} ${{sign}}${{delta.toFixed(2)}}</span></li>`);
    }}
    if (!items.length) return "";
    return `<details class="pick-why">
      <summary class="pick-why-summary">Why this pick</summary>
      <ul class="pick-why-list">${{items.join("")}}</ul>
    </details>`;
  }}

  function renderHeroPickCard(r) {{
    const edge = pickEdge(r);
    if (edge === null) return "";
    const dir = edge > 0 ? "over" : "under";
    const edgeStr = (edge > 0 ? "+" : "") + (edge * 100).toFixed(1) + "%";
    const proj = r.proj_ks_v2 || r.proj_ks_v1 || "";
    const iso = r.game_datetime_utc || "";
    const pidNum = parseInt(r.pitcher_id, 10);
    const live = isNaN(pidNum) ? null : _liveByPid.get(pidNum);
    const liveCell = renderHeroLive(iso, live, r.line, dir);
    const outcomeCls = liveCell.outcome ? ` ${{liveCell.outcome}}` : "";
    const cardCls = `pick-card ${{dir}}` + (liveCell.locked ? " locked" : "") + outcomeCls;
    const isoAttr = iso ? ` data-game-iso="${{escapeHTML(iso)}}"` : "";
    const pidAttr = !isNaN(pidNum) ? ` data-pitcher-id="${{pidNum}}"` : "";
    const lineAttr = (r.line !== null && r.line !== undefined && r.line !== "")
      ? ` data-line="${{escapeHTML(String(r.line))}}"` : "";
    const dirAttr = ` data-dir="${{dir}}"`;
    const banner = liveCell.outcome
      ? `<div class="pick-card-banner ${{liveCell.outcome}}" data-outcome-chip>${{verdictLabel(liveCell.outcome)}}</div>`
      : `<div class="pick-card-banner" data-outcome-chip style="display:none;"></div>`;
    return `<div class="${{cardCls}}"${{isoAttr}}${{pidAttr}}${{lineAttr}}${{dirAttr}}>
      ${{banner}}
      <div class="pick-card-header">
        <span class="pick-card-badge ${{dir}}">${{dir.toUpperCase()}} ${{escapeHTML(r.line || "")}}</span>
        <span class="pick-card-edge ${{dir}}">${{edgeStr}} edge</span>
      </div>
      <div class="pick-card-pitcher">
        <span class="pitcher-name">${{escapeHTML(r.pitcher || "")}}</span>
        <span class="pitcher-meta">${{oppPrefix(r)}}${{escapeHTML(teamAbbr(r.opp))}} · ${{formatGameTimeShort(r.game_datetime_utc)}}${{lineupPendingChip(r)}}</span>
      </div>
      ${{renderBetBadgesRow(pidNum)}}
      <div class="pick-card-stats">
        <div class="pick-card-stat proj-cell"${{projTooltip(r) ? ` title="${{escapeHTML(projTooltip(r))}}"` : ""}}>
          <span class="pick-card-stat-label">Our Proj</span>
          <span class="pick-card-stat-val">${{dash(proj)}}</span>
        </div>
        <div class="pick-card-stat">
          <span class="pick-card-stat-label">Our %</span>
          <span class="pick-card-stat-val">${{(() => {{
            const p = f(r.p_over);
            if (p === null) return "—";
            const conf = dir === "over" ? p : 1 - p;
            return (conf * 100).toFixed(1) + "%";
          }})()}}</span>
        </div>
        <div class="pick-card-stat">
          <span class="pick-card-stat-label card-live-label">${{escapeHTML(liveCell.label)}}</span>
          <span class="pick-card-stat-val card-live-val ${{liveCell.cls}}">${{liveCell.html}}</span>
        </div>
      </div>
      <div class="pick-card-pulse" data-pulse${{renderHeroPulse(live) ? "" : ' style="display:none"'}}>${{renderHeroPulse(live)}}</div>
      ${{renderHeroWhy(r)}}
    </div>`;
  }}

  function renderHeroPicks(rows) {{
    const focus = rows.filter(isBettableFocus)
      .sort((a, b) => Math.abs(pickEdge(b)) - Math.abs(pickEdge(a)));

    if (!focus.length) {{
      return `<section class="picks-hero">
        <div class="picks-hero-title">Today's Picks</div>
        <div class="picks-empty">No actionable picks today — model edge is in the noise band on every line. See the full table below for context.</div>
      </section>`;
    }}

    return `<section class="picks-hero">
      <div class="picks-hero-title">
        Today's Picks <span class="picks-hero-count">${{focus.length}} actionable</span>
      </div>
      <div class="picks-grid">
        ${{focus.map(renderHeroPickCard).join("")}}
      </div>
    </section>`;
  }}

  // Build a card showing one parlay combination — leg list + combined
  // payout, hit %, edge, EV. Coloring follows the EV sign so the user
  // can scan for the "+EV" cards at a glance.
  function renderParlayCard(p) {{
    const legsHTML = p.legs.map(l => {{
      const dirCls = l.dir === "over" ? "over" : "under";
      const lineStr = l.line === undefined || l.line === null || l.line === "" ? "" : ` ${{escapeHTML(String(l.line))}}`;
      const time = l.gameTimeISO ? `<span class="parlay-leg-time">${{formatGameTimeShort(l.gameTimeISO)}}</span>` : "";
      const tbd = l.lineupPending ? `<span class="lineup-pending">TBD</span>` : "";
      return `<div class="parlay-leg">
        <span class="parlay-leg-dir ${{dirCls}}">${{l.dir.toUpperCase()}}${{lineStr}}</span>
        <span class="parlay-leg-name">${{escapeHTML(l.pitcher)}}${{tbd}}</span>
        ${{time}}
      </div>`;
    }}).join("");
    const evCls = p.ev > 0.02 ? "pos" : p.ev < -0.02 ? "neg" : "flat";
    const edgeCls = p.combinedEdge === null ? "" : (p.combinedEdge > 0 ? "pos" : p.combinedEdge < 0 ? "neg" : "flat");
    const amerStr = p.combinedAmer === null
      ? "—"
      : (p.combinedAmer >= 0 ? "+" : "") + p.combinedAmer;
    const evStr = (p.ev >= 0 ? "+" : "") + p.ev.toFixed(2);
    const edgePct = p.combinedEdge === null
      ? "—"
      : (p.combinedEdge >= 0 ? "+" : "") + (p.combinedEdge * 100).toFixed(1) + "%";
    // Compact, JSON-safe leg shape for the form pre-populator. Direction
    // gets translated to the form's O/U convention.
    const formLegs = p.legs.map(l => ({{
      pitcher: l.pitcher,
      pitcher_id: l.pitcher_id,
      line: l.line,
      ou: l.dir === "over" ? "O" : "U",
    }}));
    // The whole card is the click target on the Bets URL — tapping it
    // pre-populates the Bets-tab form with these legs. On the public URL
    // the data attr is harmless and the cursor/hover affordance stays
    // off (gated in CSS by html.is-bets).
    //
    // Lineup gating: pre-lineup edge can drift meaningfully once cards
    // post (see Blind Spot #4). When any leg's opp lineup is still TBD,
    // we paint the border red and omit `data-legs` — the click handler
    // gates on that attr, so the card becomes un-clickable until every
    // leg has a confirmed lineup, at which point it goes green.
    const pendingLegs = p.legs.filter(l => l.lineupPending);
    const lineupCls = pendingLegs.length ? "lineup-blocked" : "lineup-ready";
    const legsAttr = escapeHTML(JSON.stringify(formLegs));
    const tip = pendingLegs.length
      ? `Waiting on lineups: ${{pendingLegs.map(l => l.pitcher).join(", ")}}`
      : "Add this parlay to your bets";
    const dataAttr = pendingLegs.length ? "" : ` data-legs='${{legsAttr}}'`;
    const overlapBadge = p.overlapsTop2Leg
      ? `<div class="parlay-overlap-badge" title="Shares ${{escapeHTML(p.overlapsTop2Leg)}} with the top 2-leg ticket. 2026-05-15 audit: overlapping 3-leg picks went -78% ROI vs disjoint +22% (small sample). Disjoint rule dropped 2026-05-16 for visibility — bet consciously.">⚠ overlap</div>`
      : "";
    return `<div class="parlay-card ${{evCls}} ${{lineupCls}}"${{dataAttr}} title="${{escapeHTML(tip)}}">
      ${{overlapBadge}}
      <div class="parlay-legs">${{legsHTML}}</div>
      <div class="parlay-stats">
        <div class="parlay-stat"><span class="parlay-stat-label">Payout</span><span class="parlay-stat-val">${{amerStr}}</span></div>
        <div class="parlay-stat"><span class="parlay-stat-label">Hit %</span><span class="parlay-stat-val">${{(p.combinedHit * 100).toFixed(1)}}%</span></div>
        <div class="parlay-stat"><span class="parlay-stat-label">Edge</span><span class="parlay-stat-val ${{edgeCls}}">${{edgePct}}</span></div>
        <div class="parlay-stat"><span class="parlay-stat-label">EV / $1</span><span class="parlay-stat-val ${{evCls}}">${{evStr}}</span></div>
      </div>
    </div>`;
  }}

  // Generate all 2-leg / 3-leg combos from focus picks, rank by EV per
  // $1, and render the top few. The DFS sites the user plays require
  // ≥ 2 legs per ticket, so this turns the model's picks into something
  // that can actually be wagered.
  //
  // Filters applied before display:
  //   - positive EV only — never suggest a negative-EV combo
  //   - one leg per game — two starters in the same game share K-environment
  //     (umpire, weather, ump zone), so leg-independence breaks down
  //   - games within a 3-hour window — wider gaps mean the later game's
  //     lineup won't post until after the first game has started, so you'd
  //     be committing to a leg without the input that drives its edge
  //   - per-pitcher appearance cap across the section — keeps the top-N
  //     from collapsing onto a single hot pitcher
  //   - (2026-05-16) the 3-leg disjoint rule was dropped for visibility.
  //     overlapping 3-legs now appear in the section but get a ⚠ badge
  //     so the audit signal is preserved without hiding the cards. See
  //     .parlay-overlap-badge style + project_path_c memory for rationale.
  function renderParlaySuggestions(rows) {{
    const PARLAY_INPUT_CAP = 8;       // cap focus pool before exploding combos
    const TOP_TWO = 5;
    const TOP_THREE = 3;
    const MAX_APPEARANCES = 1;        // any one pitcher can appear in at most
                                       // this many cards within a section
    // Skip combos whose game times span more than this — by the time the
    // first game starts, the later game's lineup still won't be posted
    // (lineups land ~3 hrs pre-game), so we'd be locking in a leg without
    // the input that drives its edge. Keep in sync with parlay_suggest.py.
    const MAX_GAP_MS = 3 * 3600 * 1000;
    const focus = rows.filter(isBettableFocus);
    if (focus.length < 2) return "";

    const legs = focus
      .map(pickLegFromRow)
      .filter(l => l !== null)
      .sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge))
      .slice(0, PARLAY_INPUT_CAP);
    if (legs.length < 2) return "";

    const uniqueGames = (combo) => {{
      const seen = new Set();
      for (const l of combo) {{
        if (l.game_pk === null || l.game_pk === undefined) continue;
        if (seen.has(l.game_pk)) return false;
        seen.add(l.game_pk);
      }}
      return true;
    }};

    const gameTimesWithinWindow = (combo) => {{
      let lo = Infinity, hi = -Infinity;
      for (const l of combo) {{
        if (!l.gameTimeISO) continue;
        const t = Date.parse(l.gameTimeISO);
        if (isNaN(t)) continue;
        if (t < lo) lo = t;
        if (t > hi) hi = t;
      }}
      if (lo === Infinity || hi === -Infinity) return true;
      return hi - lo <= MAX_GAP_MS;
    }};

    // Greedy: walk EV-sorted combos, skip any whose pitchers already hit the
    // appearance cap. Stops at `top` selections or when the list is exhausted.
    const selectDiverse = (sorted, top, maxPer) => {{
      const counts = new Map();
      const out = [];
      for (const p of sorted) {{
        if (out.length >= top) break;
        let ok = true;
        for (const l of p.legs) {{
          const c = counts.get(l.pitcher_id) || 0;
          if (c >= maxPer) {{ ok = false; break; }}
        }}
        if (!ok) continue;
        for (const l of p.legs) {{
          counts.set(l.pitcher_id, (counts.get(l.pitcher_id) || 0) + 1);
        }}
        out.push(p);
      }}
      return out;
    }};

    const buildSection = (k, top, label, excludePids) => {{
      if (legs.length < k) return {{ html: "", picked: [] }};
      const sorted = combos(legs, k)
        .filter(uniqueGames)
        .filter(gameTimesWithinWindow)
        .filter(combo => {{
          if (!excludePids || excludePids.size === 0) return true;
          for (const l of combo) {{
            if (l.pitcher_id !== null && l.pitcher_id !== undefined
                && excludePids.has(l.pitcher_id)) return false;
          }}
          return true;
        }})
        .map(evaluateParlay)
        .filter(p => p.ev > 0)
        .sort((a, b) => b.ev - a.ev);
      const ranked = selectDiverse(sorted, top, MAX_APPEARANCES);
      if (!ranked.length) return {{ html: "", picked: ranked }};
      const html = `<div class="parlay-section">
        <div class="parlay-section-title">${{label}}</div>
        <div class="parlay-grid">${{ranked.map(renderParlayCard).join("")}}</div>
      </div>`;
      return {{ html, picked: ranked }};
    }};

    const twoResult = buildSection(2, TOP_TWO, "Top 2-leg parlays", null);
    // Disjoint rule dropped 2026-05-16 — Chad wanted visibility into all
    // possible 3-legs. Audit signal preserved by marking overlapping cards
    // in renderParlayCard (see .parlay-overlap-badge). overlapsTop2Leg
    // stores the shared pitcher's name (truthy) or "" (falsy) for templating.
    const top2LegPids = new Set();
    let top2LegByPid = {{}};
    if (twoResult.picked.length) {{
      for (const l of twoResult.picked[0].legs) {{
        if (l.pitcher_id !== null && l.pitcher_id !== undefined) {{
          top2LegPids.add(l.pitcher_id);
          top2LegByPid[l.pitcher_id] = l.pitcher;
        }}
      }}
    }}
    const threeResult = buildSection(3, TOP_THREE, "Top 3-leg parlays", null);
    for (const card of threeResult.picked) {{
      const shared = card.legs.find(l =>
        l.pitcher_id !== null && l.pitcher_id !== undefined && top2LegPids.has(l.pitcher_id)
      );
      card.overlapsTop2Leg = shared ? (top2LegByPid[shared.pitcher_id] || shared.pitcher || "shared pitcher") : "";
    }}
    // The HTML for the 3-leg section was rendered inside buildSection
    // before overlap info existed — re-render so the badge appears.
    const threeLegHtml = threeResult.picked.length
      ? `<div class="parlay-section">
          <div class="parlay-section-title">Top 3-leg parlays</div>
          <div class="parlay-grid">${{threeResult.picked.map(renderParlayCard).join("")}}</div>
        </div>`
      : "";
    const twoLeg = twoResult.html;
    if (!twoLeg && !threeLegHtml) return "";

    return `<section class="parlay-suggester">
      <div class="parlay-suggester-header">
        <h3>Parlay Suggestions</h3>
        <span class="parlay-note">Positive-EV combos · one leg per game · games within 3 hrs · capped per pitcher · ranked by EV per $1 · 3-legs marked ⚠ if they overlap the top 2-leg</span>
      </div>
      ${{twoLeg}}
      ${{threeLegHtml}}
    </section>`;
  }}

  function hitterRow(r) {{
    const edge = pickEdge(r);
    const cls = classify(edge);
    const dir = edge === null || edge === 0 ? "" : (edge > 0 ? "over" : "under");
    const edgeStr = edge === null ? "—" : (edge > 0 ? "+" : "") + (edge * 100).toFixed(1) + "%";
    const rowCls = `row-${{cls}}` + (dir ? ` dir-${{dir}}` : "");
    const tagCls = `tag-${{cls}}` + (dir ? ` tag-dir-${{dir}}` : "");
    const overTitle = r.over_book ? ` title="Best price at ${{escapeHTML(r.over_book)}}"` : "";
    const underTitle = r.under_book ? ` title="Best price at ${{escapeHTML(r.under_book)}}"` : "";
    const novigTitle = r.n_books ? ` title="Median across ${{escapeHTML(r.n_books)}} books"` : "";
    return `<tr class="${{rowCls}}">
      <td class="player">${{escapeHTML(r.hitter || "")}}</td>
      <td class="num slot">${{dash(r.slot)}}</td>
      <td>${{escapeHTML(r.team || "")}}</td>
      <td>vs ${{escapeHTML(r.opp_pitcher || "")}}</td>
      <td class="num">${{dash(r.proj_ks)}}</td>
      <td class="num">${{dash(r.line)}}</td>
      <td class="num"${{overTitle}}>${{dash(r.over_odds)}}</td>
      <td class="num"${{underTitle}}>${{dash(r.under_odds)}}</td>
      <td class="num">${{pct1(r.p_over)}}</td>
      <td class="num"${{novigTitle}}>${{pct1(r.novig_over)}}</td>
      <td class="num edge ${{dir}}">${{edgeStr}}</td>
      <td class="badge"><span class="tag ${{tagCls}}">${{label(cls, dir)}}</span></td>
    </tr>`;
  }}

  // Prefer slate-time field when present (frozen first-pipeline-run
  // snapshot) and fall back to the live/final-state field for older
  // rows that pre-date slate snapshotting.
  function slateOrLive(r, slateKey, liveKey) {{
    const s = r[slateKey];
    if (s !== undefined && s !== "" && s !== null) return s;
    return r[liveKey];
  }}

  function pitcherResultRow(r) {{
    const actual = f(r.actual_ks);
    const proj = f(r.proj_ks_v2) || f(r.proj_ks_v1);
    // Grade against slate-time line + edge — that's what we'd actually
    // have bet at. Fall back to live values for pre-snapshot history.
    const lineRaw = slateOrLive(r, "slate_line", "line");
    const line = f(lineRaw);
    const overHit = f(slateOrLive(r, "slate_over_hit", "over_hit"));
    const edge = f(slateOrLive(r, "slate_edge", "edge"));
    const cls = classify(edge);
    const dir = edge === null || edge === 0 ? "" : (edge > 0 ? "over" : "under");
    let errCls = "zero", errStr = "—";
    if (actual !== null && proj !== null) {{
      const e = actual - proj;
      if (e > 0.5) {{ errCls = "over"; errStr = `+${{e.toFixed(1)}}`; }}
      else if (e < -0.5) {{ errCls = "under"; errStr = e.toFixed(1); }}
      else {{ errCls = "zero"; errStr = (e >= 0 ? "+" : "") + e.toFixed(1); }}
    }}

    // "Our Pick" cell — mirrors slate-table tag styling so the user can
    // see at a glance what we recommended yesterday morning.
    const pickTagCls = `tag-${{cls}}` + (dir ? ` tag-dir-${{dir}}` : "");
    const pickCell = `<td><span class="tag ${{pickTagCls}}">${{label(cls, dir)}}</span></td>`;

    // "Result" cell — verdict for focus picks (HIT/MISS), informational
    // OVER hit/UNDER hit for everything else. No-line stays muted.
    let resultCell = '<td class="muted">—</td>';
    if (line === null) {{
      resultCell = '<td class="muted">no line</td>';
    }} else if (overHit !== null) {{
      const overWon = overHit >= 1;
      if (cls === "focus" && dir) {{
        const hit = (dir === "over" && overWon) || (dir === "under" && !overWon);
        resultCell = hit
          ? '<td class="hit">✓ HIT</td>'
          : '<td class="miss">✗ MISS</td>';
      }} else {{
        resultCell = overWon
          ? '<td class="muted">OVER hit</td>'
          : '<td class="muted">UNDER hit</td>';
      }}
    }}
    const projCell = r.proj_ks_v2 || r.proj_ks_v1;
    return `<tr>
      <td class="player">${{escapeHTML(r.pitcher || "")}}</td>
      <td>${{oppPrefix(r)}}${{escapeHTML(r.opp || "")}}</td>
      <td class="num">${{dash(projCell)}}</td>
      <td class="num">${{actual !== null ? Math.round(actual) : "—"}}</td>
      <td class="num error ${{errCls}}">${{errStr}}</td>
      <td class="num">${{dash(lineRaw)}}</td>
      ${{pickCell}}
      ${{resultCell}}
    </tr>`;
  }}

  function hitterResultRow(r) {{
    const actual = f(r.actual_ks);
    const proj = f(r.proj_ks);
    const line = f(r.line);
    const overHit = f(r.over_hit);
    let errCls = "zero", errStr = "—";
    if (actual !== null && proj !== null) {{
      const e = actual - proj;
      if (e > 0.3) {{ errCls = "over"; errStr = `+${{e.toFixed(1)}}`; }}
      else if (e < -0.3) {{ errCls = "under"; errStr = e.toFixed(1); }}
      else {{ errCls = "zero"; errStr = (e >= 0 ? "+" : "") + e.toFixed(1); }}
    }}
    let resultCell = '<td class="muted">—</td>';
    if (line === null) resultCell = '<td class="muted">no line</td>';
    else if (overHit !== null) resultCell = overHit >= 1
      ? '<td class="hit">OVER hit</td>'
      : '<td class="miss">UNDER hit</td>';
    return `<tr>
      <td class="player">${{escapeHTML(r.hitter || "")}}</td>
      <td>${{escapeHTML(r.team || "")}}</td>
      <td class="num">${{dash(r.proj_ks)}}</td>
      <td class="num">${{actual !== null ? Math.round(actual) : "—"}}</td>
      <td class="num error ${{errCls}}">${{errStr}}</td>
      <td class="num">${{dash(r.line)}}</td>
      ${{resultCell}}
    </tr>`;
  }}

  // Try fetching today's CSV; fall back up to 3 days if not yet posted.
  // Returns raw `text` alongside parsed rows so callers (slate repoll)
  // can do a cheap text-equality check without re-parsing.
  async function fetchTodaysCSV(prefix) {{
    for (let i = 0; i <= 3; i++) {{
      const d = dateInChicago(-i);
      const text = await fetchCSV(baseUrl() + `${{prefix}}_${{d}}.csv`);
      if (text) return {{ date: d, rows: parseCSV(text), text }};
    }}
    return {{ date: null, rows: [], text: "" }};
  }}

  // Most recent settled CSV — yesterday or earlier.
  async function fetchMostRecentSettled(prefix) {{
    for (let i = 1; i <= 14; i++) {{
      const d = dateInChicago(-i);
      const text = await fetchCSV(baseUrl() + `${{prefix}}_${{d}}_settled.csv`);
      if (text) {{
        const rows = parseCSV(text).filter(r => f(r.actual_ks) !== null);
        if (rows.length) return {{ date: d, rows }};
      }}
    }}
    return {{ date: null, rows: [] }};
  }}

  // Track record: pull last N days of settled CSVs in parallel and
  // distill each focus pick (the ones we actually recommended) into
  // {{date, dir, won, pnl}}. Slate-time fields preferred — they reflect
  // the line/odds we'd actually have bet. Falls back to live fields for
  // any pre-snapshot history.
  //
  // Returns {{focus, all, settled}} where:
  //   focus    — focus-classified picks only (drives summary + sparkline)
  //   all      — every graded pick with an edge value (drives edge buckets)
  //   settled  — every settled row regardless of focus (drives calibration)
  async function fetchTrackRecord(maxDays = 14) {{
    const fetches = [];
    for (let i = 1; i <= maxDays; i++) {{
      const d = dateInChicago(-i);
      fetches.push(
        fetchCSV(baseUrl() + `pitcher_ks_${{d}}_settled.csv`)
          .then(text => ({{ d, text }}))
      );
    }}
    const results = await Promise.all(fetches);
    const focus = [];
    const all = [];
    const settled = [];
    for (const {{ d, text }} of results) {{
      if (!text) continue;
      const rows = parseCSV(text);
      for (const r of rows) {{
        const actual = f(r.actual_ks);
        if (actual === null) continue;
        const proj = f(r.proj_ks_v2) !== null
          ? f(r.proj_ks_v2)
          : f(r.proj_ks_v1);
        if (proj !== null) {{
          settled.push({{
            date: d,
            pitcher: r.pitcher || "",
            proj,
            actual,
          }});
        }}
        // Prefer calibrated cal_edge_v2 (slate-time when present) — same
        // signal the live dashboard's pickEdge() uses for classification.
        // Raw slate_edge/edge is the fallback for pre-Platt historical
        // rows (pre-2026-05-11) that lack cal_edge_v2.
        const calEdge = f(slateOrLive(r, "slate_cal_edge_v2", "cal_edge_v2"));
        const rawEdge = f(slateOrLive(r, "slate_edge", "edge"));
        const edge = calEdge !== null ? calEdge : rawEdge;
        if (edge === null) continue;
        const overHit = f(slateOrLive(r, "slate_over_hit", "over_hit"));
        if (overHit === null) continue;
        const dir = edge > 0 ? "over" : "under";
        const won = (dir === "over" && overHit >= 1) ||
                    (dir === "under" && overHit < 1);
        const pnlField = dir === "over"
          ? slateOrLive(r, "slate_over_pnl", "over_pnl")
          : slateOrLive(r, "slate_under_pnl", "under_pnl");
        const pnl = f(pnlField);
        const pick = {{
          date: d, pitcher: r.pitcher || "", dir, won,
          edge,
          pnl: pnl === null ? 0 : pnl,
        }};
        all.push(pick);
        // isBettableFocus parity: focus band + line gate (openers /
        // relievers with line < MIN_LINE_FOR_FOCUS are graded into `all`
        // but excluded from the focus track record).
        if (classify(edge) !== "focus") continue;
        const line = f(slateOrLive(r, "slate_line", "line"));
        if (line === null || line < MIN_LINE_FOR_FOCUS) continue;
        focus.push(pick);
      }}
    }}
    return {{ focus, all, settled }};
  }}

  // Pull the snapshotted parlay-suggester output (top 5 two-leg + top 3
  // three-leg per day) plus the per-day grading. Each card already has
  // its predicted hit prob, predicted EV, parlay_hit (0/1 or "" for
  // un-graded scratches), and realized_pnl in 1u-flat units.
  async function fetchParlayTrackRecord(maxDays = 14) {{
    const fetches = [];
    for (let i = 1; i <= maxDays; i++) {{
      const d = dateInChicago(-i);
      fetches.push(
        fetchCSV(baseUrl() + `parlay_suggestions_${{d}}_settled.csv`)
          .then(text => ({{ d, text }}))
      );
    }}
    const results = await Promise.all(fetches);
    const all = [];
    for (const {{ d, text }} of results) {{
      if (!text) continue;
      const rows = parseCSV(text);
      for (const r of rows) {{
        const hit = f(r.parlay_hit);
        if (hit === null) continue;  // un-graded (scratched leg)
        const pnl = f(r.realized_pnl);
        const predicted = f(r.combined_hit);
        const ev = f(r.ev);
        all.push({{
          date: d,
          section: r.section || "",
          legCount: parseInt(r.leg_count, 10) || 0,
          won: hit >= 1,
          pnl: pnl === null ? 0 : pnl,
          predicted: predicted === null ? 0 : predicted,
          ev: ev === null ? 0 : ev,
        }});
      }}
    }}
    return {{ all }};
  }}

  // Bucket picks by absolute edge magnitude. Reveals whether the edge
  // calc is actually predictive — high-edge bucket should outperform
  // low. Buckets cover both OVER and UNDER picks; an UNDER with edge
  // -8% sits in the same 5–10% bucket as an OVER with edge +8%.
  function renderEdgeBuckets(allPicks) {{
    if (!allPicks.length) return "";
    const buckets = [
      {{ label: "0–2%",  lo: 0,    hi: 0.02 }},
      {{ label: "2–5%",  lo: 0.02, hi: 0.05 }},
      {{ label: "5–10%", lo: 0.05, hi: 0.10 }},
      {{ label: "10%+",  lo: 0.10, hi: Infinity }},
    ];
    const rows = buckets.map(b => {{
      const ps = allPicks.filter(p => {{
        const ae = Math.abs(p.edge);
        return ae >= b.lo && ae < b.hi;
      }});
      const n = ps.length;
      const hits = ps.filter(p => p.won).length;
      const units = ps.reduce((s, p) => s + p.pnl, 0);
      const hitPct = n ? (hits / n * 100).toFixed(0) + "%" : "—";
      const roi = n ? (units / n * 100) : null;
      const roiStr = roi === null ? "—" : (roi >= 0 ? "+" : "") + roi.toFixed(1) + "%";
      const uStr = (units >= 0 ? "+" : "") + units.toFixed(2);
      const uCls = units >= 0 ? "pos" : "neg";
      const roiCls = roi === null ? "" : (roi >= 0 ? "pos" : "neg");
      return `<tr>
        <td><strong>${{escapeHTML(b.label)}}</strong></td>
        <td class="num">${{n}}</td>
        <td class="num">${{hits}}</td>
        <td class="num">${{hitPct}}</td>
        <td class="num ${{uCls}}">${{uStr}}u</td>
        <td class="num ${{roiCls}}">${{roiStr}}</td>
      </tr>`;
    }}).join("");
    return `<div class="track-bucket-wrap">
      <div class="sparkline-title">ROI by edge bucket
        <span class="muted" style="font-weight: 400;">— is the edge calc predictive?</span>
      </div>
      <div class="table-scroll"><table class="track-buckets">
        <thead><tr>
          <th>|Edge|</th>
          <th class="num">Picks</th>
          <th class="num">Hits</th>
          <th class="num">Hit %</th>
          <th class="num">Units</th>
          <th class="num">ROI</th>
        </tr></thead>
        <tbody>${{rows}}</tbody>
      </table></div>
    </div>`;
  }}

  // Calibration scatter: projected K vs actual K, with a 45° reference
  // line. Bias is visible at a glance — points above the line = model
  // under-projecting, below = over-projecting. Dot color encodes recency
  // (older points faded) so a recent drift is spottable too.
  function renderCalibrationScatter(settled) {{
    if (settled.length < 5) return "";
    const w = 360, h = 280, padL = 36, padR = 12, padT = 18, padB = 30;
    const xs = settled.map(p => p.proj);
    const ys = settled.map(p => p.actual);
    const lo = Math.floor(Math.min(0, ...xs, ...ys));
    const hi = Math.ceil(Math.max(...xs, ...ys, 1));
    const span = (hi - lo) || 1;
    const xFor = v => padL + ((v - lo) / span) * (w - padL - padR);
    const yFor = v => h - padB - ((v - lo) / span) * (h - padT - padB);

    // Dates ordered oldest→newest so newer points get fuller opacity.
    const datesAsc = [...new Set(settled.map(p => p.date))].sort();
    const dateIdx = new Map(datesAsc.map((d, i) => [d, i]));
    const opacityFor = d => {{
      if (datesAsc.length <= 1) return 1;
      const idx = dateIdx.get(d);
      return 0.35 + 0.65 * (idx / (datesAsc.length - 1));
    }};

    const ticks = [];
    for (let v = lo; v <= hi; v += 2) {{
      ticks.push(`<line x1="${{xFor(v)}}" y1="${{padT}}" x2="${{xFor(v)}}" y2="${{h - padB}}" stroke="var(--border)" stroke-width="0.5" />`);
      ticks.push(`<line x1="${{padL}}" y1="${{yFor(v)}}" x2="${{w - padR}}" y2="${{yFor(v)}}" stroke="var(--border)" stroke-width="0.5" />`);
      ticks.push(`<text x="${{xFor(v)}}" y="${{h - padB + 14}}" class="cal-tick" text-anchor="middle">${{v}}</text>`);
      ticks.push(`<text x="${{padL - 6}}" y="${{yFor(v) + 3}}" class="cal-tick" text-anchor="end">${{v}}</text>`);
    }}

    // 45° reference line — perfect calibration sits exactly on this.
    const ref = `<line x1="${{xFor(lo)}}" y1="${{yFor(lo)}}" x2="${{xFor(hi)}}" y2="${{yFor(hi)}}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3" />`;

    const dots = settled.map(p => {{
      const cx = xFor(p.proj).toFixed(1);
      const cy = yFor(p.actual).toFixed(1);
      const op = opacityFor(p.date).toFixed(2);
      const tip = `${{p.pitcher}} ${{p.date}}: proj ${{p.proj.toFixed(2)}} → actual ${{p.actual}}`;
      return `<circle cx="${{cx}}" cy="${{cy}}" r="3.5" fill="var(--accent, #4a90e2)" fill-opacity="${{op}}"><title>${{escapeHTML(tip)}}</title></circle>`;
    }}).join("");

    // Mean residual = systematic bias. + = model under, - = model over.
    const residuals = settled.map(p => p.actual - p.proj);
    const meanRes = residuals.reduce((s, r) => s + r, 0) / residuals.length;
    const rmse = Math.sqrt(residuals.reduce((s, r) => s + r * r, 0) / residuals.length);
    const biasLabel = meanRes > 0.05 ? "model UNDER-projecting"
                    : meanRes < -0.05 ? "model OVER-projecting"
                    : "well-calibrated";
    const biasCls = meanRes > 0.05 ? "neg" : meanRes < -0.05 ? "pos" : "flat";

    return `<div class="calibration-wrap">
      <div class="sparkline-title">Calibration: projected vs actual Ks
        <span class="muted" style="font-weight: 400;">— ${{settled.length}} starts</span>
      </div>
      <div class="cal-stats">
        <span>Mean residual: <strong class="${{biasCls}}">${{meanRes >= 0 ? "+" : ""}}${{meanRes.toFixed(2)}} K</strong> (${{biasLabel}})</span>
        <span>RMSE: <strong>${{rmse.toFixed(2)}} K</strong></span>
      </div>
      <svg class="cal-svg" viewBox="0 0 ${{w}} ${{h}}" width="100%" preserveAspectRatio="xMidYMid meet" aria-label="Calibration scatter">
        ${{ticks.join("")}}
        ${{ref}}
        ${{dots}}
        <text x="${{w / 2}}" y="${{h - 4}}" class="cal-axis" text-anchor="middle">Projected Ks</text>
        <text x="10" y="${{h / 2}}" class="cal-axis" text-anchor="middle" transform="rotate(-90 10 ${{h / 2}})">Actual Ks</text>
      </svg>
    </div>`;
  }}

  // Build sparkline as inline SVG. Plots cumulative units across the
  // available days. Auto-scales the y-axis. Empty/sparse data falls
  // through to a placeholder.
  function renderSparkline(dailyUnits) {{
    if (!dailyUnits.length) return "";
    // Cumulative running total — that's the metric you actually feel
    // (vs. daily PnL which is noisy).
    const cum = [];
    let running = 0;
    for (const u of dailyUnits) {{
      running += u.units;
      cum.push({{
        date: u.date,
        cum: running,
        units: u.units,
        picks: u.picks || 0,
        hits: u.hits || 0,
      }});
    }}
    const w = 600, h = 60, pad = 4;
    const maxV = Math.max(0, ...cum.map(p => p.cum));
    const minV = Math.min(0, ...cum.map(p => p.cum));
    const range = maxV - minV || 1;
    const xStep = cum.length > 1 ? (w - pad * 2) / (cum.length - 1) : 0;
    const yFor = v => h - pad - ((v - minV) / range) * (h - pad * 2);
    const xFor = i => pad + i * xStep;
    const zeroY = yFor(0);

    const pathD = cum.map((p, i) => `${{i === 0 ? "M" : "L"}}${{xFor(i).toFixed(1)}},${{yFor(p.cum).toFixed(1)}}`).join(" ");
    const areaD = cum.length > 1
      ? `${{pathD}} L${{xFor(cum.length - 1).toFixed(1)}},${{zeroY.toFixed(1)}} L${{xFor(0).toFixed(1)}},${{zeroY.toFixed(1)}} Z`
      : "";
    const finalCls = running >= 0 ? "pos" : "neg";
    const finalLabel = `${{running >= 0 ? "+" : ""}}${{running.toFixed(2)}}u total`;

    // Per-day visible dot + invisible larger hover target. Tooltip is
    // a single absolutely-positioned div toggled on mouseover. JSON
    // payload sits in data attrs so the handler doesn't need a closure.
    const dots = cum.map((p, i) => {{
      const cx = xFor(i).toFixed(1);
      const cy = yFor(p.cum).toFixed(1);
      const losses = p.picks - p.hits;
      const dataAttrs = `data-spark-date="${{escapeHTML(p.date)}}" `
        + `data-spark-cum="${{p.cum.toFixed(2)}}" `
        + `data-spark-units="${{p.units.toFixed(2)}}" `
        + `data-spark-hits="${{p.hits}}" `
        + `data-spark-losses="${{losses}}" `
        + `data-spark-picks="${{p.picks}}"`;
      return `<circle class="sparkline-dot" cx="${{cx}}" cy="${{cy}}" r="2.5" />`
        + `<circle class="sparkline-hover-target" cx="${{cx}}" cy="${{cy}}" r="10" ${{dataAttrs}}></circle>`;
    }}).join("");

    return `<div class="sparkline-wrap" id="track-sparkline">
      <div class="sparkline-title">Cumulative units (${{cum.length}} day${{cum.length === 1 ? "" : "s"}}) — ${{finalLabel}}</div>
      <svg class="sparkline-svg" viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none">
        <line class="sparkline-axis" x1="${{pad}}" y1="${{zeroY.toFixed(1)}}" x2="${{w - pad}}" y2="${{zeroY.toFixed(1)}}" />
        ${{areaD ? `<path class="sparkline-area ${{finalCls}}" d="${{areaD}}" />` : ""}}
        <path class="sparkline-line ${{finalCls}}" d="${{pathD}}" />
        ${{dots}}
      </svg>
      <div class="sparkline-tip" id="sparkline-tip" style="display:none;"></div>
    </div>`;
  }}

  // Bind hover targets the first time the sparkline appears on screen
  // after each render. Uses event delegation so re-renders just keep
  // working without re-binding.
  document.addEventListener("mouseover", (e) => {{
    const t = e.target.closest(".sparkline-hover-target");
    if (!t) return;
    const wrap = t.closest(".sparkline-wrap");
    const tip = wrap && wrap.querySelector(".sparkline-tip");
    if (!wrap || !tip) return;
    const date = t.dataset.sparkDate;
    const cum = parseFloat(t.dataset.sparkCum);
    const units = parseFloat(t.dataset.sparkUnits);
    const hits = parseInt(t.dataset.sparkHits, 10);
    const losses = parseInt(t.dataset.sparkLosses, 10);
    const picks = parseInt(t.dataset.sparkPicks, 10);
    const dayCls = units > 0 ? "pos" : units < 0 ? "neg" : "";
    const cumCls = cum > 0 ? "pos" : cum < 0 ? "neg" : "";
    const dayStr = `${{units >= 0 ? "+" : ""}}${{units.toFixed(2)}}u`;
    const cumStr = `${{cum >= 0 ? "+" : ""}}${{cum.toFixed(2)}}u`;
    tip.innerHTML = `<strong>${{date}}</strong><br>`
      + `<span class="tip-units ${{dayCls}}">${{dayStr}}</span> · ${{hits}}W–${{losses}}L (${{picks}})<br>`
      + `Cumulative: <span class="tip-units ${{cumCls}}">${{cumStr}}</span>`;
    // Position over the hovered point (SVG uses viewBox so we read its
    // bounding rect to map back to page pixels).
    const wrapRect = wrap.getBoundingClientRect();
    const dotRect = t.getBoundingClientRect();
    tip.style.left = (dotRect.left - wrapRect.left + dotRect.width / 2) + "px";
    tip.style.top = (dotRect.top - wrapRect.top) + "px";
    tip.style.display = "block";
  }});
  document.addEventListener("mouseout", (e) => {{
    const t = e.target.closest(".sparkline-hover-target");
    if (!t) return;
    const wrap = t.closest(".sparkline-wrap");
    const tip = wrap && wrap.querySelector(".sparkline-tip");
    if (tip) tip.style.display = "none";
  }});

  // Calendar hover tooltip — used by the Bets tab calendar (.cal-cell-money).
  // The track-record body heatmap was removed once the header scoreboard
  // grew its own interactive heatmap with the same per-day detail.
  document.addEventListener("mouseover", (e) => {{
    const t = e.target.closest(".cal-cell[data-cal-date]");
    if (!t) return;
    const wrap = t.closest(".cal-wrap");
    const tip = wrap && wrap.querySelector(".cal-tip");
    if (!wrap || !tip) return;
    const date = t.dataset.calDate;
    const isMoney = t.dataset.calMoney === "1";
    if (t.dataset.calEmpty) {{
      tip.innerHTML = `<strong>${{date}}</strong><br><span class="tip-units">no ${{isMoney ? "bets" : "picks"}}</span>`;
    }} else if (isMoney) {{
      const pnl = parseFloat(t.dataset.calPnl);
      const wins = parseInt(t.dataset.calWins, 10);
      const losses = parseInt(t.dataset.calLosses, 10);
      const count = parseInt(t.dataset.calCount, 10);
      const staked = parseFloat(t.dataset.calStaked || "0");
      const cls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
      const str = `${{pnl >= 0 ? "+" : "-"}}$${{Math.abs(pnl).toFixed(2)}}`;
      const stakedStr = staked > 0 ? ` · staked $${{staked.toFixed(2)}}` : "";
      tip.innerHTML = `<strong>${{date}}</strong><br>`
        + `<span class="tip-units ${{cls}}">${{str}}</span> · ${{wins}}W–${{losses}}L (${{count}})${{stakedStr}}`;
    }} else {{
      const units = parseFloat(t.dataset.calUnits);
      const hits = parseInt(t.dataset.calHits, 10);
      const losses = parseInt(t.dataset.calLosses, 10);
      const picks = parseInt(t.dataset.calPicks, 10);
      const cls = units > 0 ? "pos" : units < 0 ? "neg" : "";
      const str = `${{units >= 0 ? "+" : ""}}${{units.toFixed(2)}}u`;
      tip.innerHTML = `<strong>${{date}}</strong><br>`
        + `<span class="tip-units ${{cls}}">${{str}}</span> · ${{hits}}W–${{losses}}L (${{picks}})`;
    }}
    const wrapRect = wrap.getBoundingClientRect();
    const cellRect = t.getBoundingClientRect();
    tip.style.left = (cellRect.left - wrapRect.left + cellRect.width / 2) + "px";
    tip.style.top = (cellRect.top - wrapRect.top) + "px";
    tip.style.display = "block";
  }});
  document.addEventListener("mouseout", (e) => {{
    const t = e.target.closest(".cal-cell[data-cal-date]");
    if (!t) return;
    const wrap = t.closest(".cal-wrap");
    const tip = wrap && wrap.querySelector(".cal-tip");
    if (tip) tip.style.display = "none";
  }});

  // Header scoreboard heatmap tooltip — same data shape as the body cal
  // cells, but the tip lives inside .scoreboard-heat (positioned over
  // the strip) so it doesn't escape the header column.
  document.addEventListener("mouseover", (e) => {{
    const t = e.target.closest(".scoreboard-heat-cell[data-heat-date]");
    if (!t) return;
    const wrap = t.closest(".scoreboard-heat");
    const tip = wrap && wrap.querySelector(".scoreboard-heat-tip");
    if (!wrap || !tip) return;
    const date = t.dataset.heatDate;
    const isMoney = t.dataset.heatMoney === "1";
    if (t.dataset.heatEmpty) {{
      tip.innerHTML = `<strong>${{date}}</strong><br><span class="tip-units">no ${{isMoney ? "bets" : "picks"}}</span>`;
    }} else if (isMoney) {{
      const pnl = parseFloat(t.dataset.heatPnl);
      const wins = parseInt(t.dataset.heatWins, 10);
      const losses = parseInt(t.dataset.heatLosses, 10);
      const count = parseInt(t.dataset.heatCount, 10);
      const staked = parseFloat(t.dataset.heatStaked || "0");
      const cls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
      const str = `${{pnl >= 0 ? "+" : "-"}}$${{Math.abs(pnl).toFixed(2)}}`;
      const stakedStr = staked > 0 ? ` · staked $${{staked.toFixed(2)}}` : "";
      tip.innerHTML = `<strong>${{date}}</strong><br>`
        + `<span class="tip-units ${{cls}}">${{str}}</span> · ${{wins}}W–${{losses}}L (${{count}})${{stakedStr}}`;
    }} else {{
      const units = parseFloat(t.dataset.heatUnits);
      const hits = parseInt(t.dataset.heatHits, 10);
      const losses = parseInt(t.dataset.heatLosses, 10);
      const picks = parseInt(t.dataset.heatPicks, 10);
      const cls = units > 0 ? "pos" : units < 0 ? "neg" : "";
      const str = `${{units >= 0 ? "+" : ""}}${{units.toFixed(2)}}u`;
      tip.innerHTML = `<strong>${{date}}</strong><br>`
        + `<span class="tip-units ${{cls}}">${{str}}</span> · ${{hits}}W–${{losses}}L (${{picks}})`;
    }}
    const wrapRect = wrap.getBoundingClientRect();
    const cellRect = t.getBoundingClientRect();
    tip.style.left = (cellRect.left - wrapRect.left + cellRect.width / 2) + "px";
    tip.style.top = (cellRect.top - wrapRect.top) + "px";
    tip.style.display = "block";
  }});
  document.addEventListener("mouseout", (e) => {{
    const t = e.target.closest(".scoreboard-heat-cell[data-heat-date]");
    if (!t) return;
    const wrap = t.closest(".scoreboard-heat");
    const tip = wrap && wrap.querySelector(".scoreboard-heat-tip");
    if (tip) tip.style.display = "none";
  }});

  // Sparkline hover: vertical guide line + snap-to-data dot + readout
  // popover above the curve. Delegated mousemove/mouseleave so it
  // survives every paintScoreboard re-render. The wrap div carries the
  // cum/dates/daily arrays + viewBox geometry as a JSON data attribute.
  function hideSparkHover(wrap) {{
    const svg = wrap.querySelector(".scoreboard-spark");
    if (!svg) return;
    const cursor = svg.querySelector(".scoreboard-spark-cursor");
    const dot = svg.querySelector(".scoreboard-spark-hover-dot");
    const readout = wrap.querySelector(".scoreboard-spark-readout");
    if (cursor) cursor.style.display = "none";
    if (dot) dot.style.display = "none";
    if (readout) readout.style.display = "none";
  }}
  function fmtSparkVal(v, isMoney) {{
    if (isMoney) {{
      return `${{v >= 0 ? "+" : "−"}}$${{Math.abs(v).toFixed(2)}}`;
    }}
    return `${{v >= 0 ? "+" : ""}}${{v.toFixed(2)}}u`;
  }}
  document.addEventListener("mousemove", (e) => {{
    const wrap = e.target.closest(".scoreboard-spark-wrap");
    if (!wrap) return;
    let cfg;
    try {{ cfg = JSON.parse(wrap.dataset.spark); }} catch (_) {{ return; }}
    if (!cfg || !cfg.cum || cfg.cum.length < 2) return;
    const svg = wrap.querySelector(".scoreboard-spark");
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const rel = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const idx = Math.round(rel * (cfg.cum.length - 1));
    const cumV = cfg.cum[idx];
    const dailyV = (cfg.daily && cfg.daily[idx] !== undefined) ? cfg.daily[idx] : 0;
    const date = (cfg.dates && cfg.dates[idx]) || "";
    const isMoney = cfg.kind === "money";
    // viewBox coords for SVG elements
    const xVB = cfg.padX + idx * cfg.step;
    const yVB = cfg.h - cfg.padY - ((cumV - cfg.minV) / (cfg.range || 1)) * (cfg.h - cfg.padY * 2);
    const cursor = svg.querySelector(".scoreboard-spark-cursor");
    const dot = svg.querySelector(".scoreboard-spark-hover-dot");
    if (cursor) {{
      cursor.setAttribute("x1", xVB.toFixed(2));
      cursor.setAttribute("x2", xVB.toFixed(2));
      cursor.style.display = "";
    }}
    if (dot) {{
      dot.setAttribute("d", `M${{xVB.toFixed(2)}},${{yVB.toFixed(2)}} L${{xVB.toFixed(2)}},${{yVB.toFixed(2)}}`);
      dot.style.display = "";
    }}
    const readout = wrap.querySelector(".scoreboard-spark-readout");
    if (readout) {{
      const cumCls = cumV > 0 ? "pos" : cumV < 0 ? "neg" : "flat";
      const dailyStr = dailyV === 0
        ? `no ${{isMoney ? "bets" : "picks"}}`
        : `daily ${{fmtSparkVal(dailyV, isMoney)}}`;
      // Two-line layout so the popover stays narrow enough not to overflow
      // when hovering the rightmost data point — the bankroll spark sits
      // flush with the viewport's right edge, so a wide tooltip used to
      // run off-screen.
      readout.innerHTML = `<strong>${{escapeHTML(date)}}</strong><br>`
        + `<span class="cum ${{cumCls}}">${{fmtSparkVal(cumV, isMoney)}}</span>`
        + ` · ${{dailyStr}}`;
      // Position in CSS px relative to the wrap, then clamp so it never
      // pokes past the wrap's left/right edge. offsetWidth is the
      // rendered tooltip width — read AFTER the innerHTML and display
      // are set so the layout is committed.
      readout.style.display = "";
      const pxX = (xVB / cfg.w) * rect.width;
      const halfW = readout.offsetWidth / 2;
      const wrapW = rect.width;
      const pad = 4;
      if (pxX + halfW > wrapW - pad) {{
        // Near right edge — pin to wrap's right.
        readout.style.left = (wrapW - pad) + "px";
        readout.style.transform = "translateX(-100%)";
      }} else if (pxX - halfW < pad) {{
        // Near left edge — pin to wrap's left.
        readout.style.left = pad + "px";
        readout.style.transform = "translateX(0)";
      }} else {{
        readout.style.left = pxX + "px";
        readout.style.transform = "translateX(-50%)";
      }}
    }}
  }});
  document.addEventListener("mouseout", (e) => {{
    const wrap = e.target.closest(".scoreboard-spark-wrap");
    if (!wrap) return;
    // Only hide when actually leaving the wrap (relatedTarget outside it).
    const to = e.relatedTarget;
    if (to && wrap.contains(to)) return;
    hideSparkHover(wrap);
  }});

  // Trend arrow comparing the most-recent half of the window to the
  // older half. Only meaningful with ~7+ picks; below that we just show
  // a flat dash to avoid noise.
  function renderTrend(recentVal, priorVal, fmt) {{
    if (recentVal === null || priorVal === null) return "";
    const delta = recentVal - priorVal;
    const cls = delta > 0.001 ? "pos" : delta < -0.001 ? "neg" : "flat";
    const arrow = cls === "pos" ? "▲" : cls === "neg" ? "▼" : "•";
    return `<div class="track-trend ${{cls}}">${{arrow}} ${{fmt(delta)}} vs prior</div>`;
  }}

  function renderTrackRecord(track, maxDays) {{
    // Backwards compat: callers used to pass an array of focus picks.
    // Now they pass {{focus, all, settled}}. Accept either.
    const picks = Array.isArray(track) ? track : (track.focus || []);
    const allPicks = Array.isArray(track) ? track : (track.all || []);
    const settledRows = Array.isArray(track) ? [] : (track.settled || []);
    if (!picks.length) {{
      return `<section class="results-section">
        <h2>Track Record — last ${{maxDays}} days</h2>
        <p class="muted">No graded picks yet. Slate snapshots started 2026-05-01 — once tomorrow's settle runs, the first day's picks will populate here. Charts and trends activate once enough days accumulate.</p>
      </section>`;
    }}
    const total = picks.length;
    const hits = picks.filter(p => p.won).length;
    const hitRate = hits / total;
    const units = picks.reduce((s, p) => s + p.pnl, 0);
    const roi = units / total;

    const overs = picks.filter(p => p.dir === "over");
    const overHits = overs.filter(p => p.won).length;
    const overUnits = overs.reduce((s, p) => s + p.pnl, 0);
    const unders = picks.filter(p => p.dir === "under");
    const underHits = unders.filter(p => p.won).length;
    const underUnits = unders.reduce((s, p) => s + p.pnl, 0);

    // Trend comparison: split picks chronologically in half. With <8
    // picks the comparison is meaningless, so we hide it.
    let trendBlocks = {{ hit: "", units: "", roi: "" }};
    if (picks.length >= 8) {{
      const sortedByDate = picks.slice().sort((a, b) => a.date.localeCompare(b.date));
      const mid = Math.floor(sortedByDate.length / 2);
      const prior = sortedByDate.slice(0, mid);
      const recent = sortedByDate.slice(mid);
      const priorHit = prior.filter(p => p.won).length / prior.length;
      const recentHit = recent.filter(p => p.won).length / recent.length;
      const priorUnits = prior.reduce((s, p) => s + p.pnl, 0);
      const recentUnits = recent.reduce((s, p) => s + p.pnl, 0);
      const priorRoi = priorUnits / prior.length;
      const recentRoi = recentUnits / recent.length;
      trendBlocks.hit = renderTrend(recentHit, priorHit, d => `${{(d * 100).toFixed(0)}}pp`);
      trendBlocks.units = renderTrend(recentUnits, priorUnits, d => `${{d >= 0 ? "+" : ""}}${{d.toFixed(2)}}u`);
      trendBlocks.roi = renderTrend(recentRoi, priorRoi, d => `${{(d * 100).toFixed(1)}}pp`);
    }}

    const summaryHTML = `
      <div class="track-summary">
        <div class="track-stat"><span class="track-label">Picks</span><span class="track-val">${{total}}</span></div>
        <div class="track-stat"><span class="track-label">Hit rate</span><span class="track-val">${{(hitRate * 100).toFixed(0)}}% (${{hits}}/${{total}})</span>${{trendBlocks.hit}}</div>
        <div class="track-stat"><span class="track-label">Units (1u flat)</span><span class="track-val ${{units >= 0 ? 'pos' : 'neg'}}">${{units >= 0 ? '+' : ''}}${{units.toFixed(2)}}</span>${{trendBlocks.units}}</div>
        <div class="track-stat"><span class="track-label">ROI</span><span class="track-val ${{roi >= 0 ? 'pos' : 'neg'}}">${{roi >= 0 ? '+' : ''}}${{(roi * 100).toFixed(1)}}%</span>${{trendBlocks.roi}}</div>
      </div>`;

    // Per-day aggregation drives both the sparkline and the breakdown
    // table — compute once. Sparkline tooltips read picks/hits too.
    const byDate = {{}};
    for (const p of picks) {{
      if (!byDate[p.date]) byDate[p.date] = [];
      byDate[p.date].push(p);
    }}
    const sortedAsc = Object.keys(byDate).sort();
    const dailyUnits = sortedAsc.map(d => ({{
      date: d,
      units: byDate[d].reduce((s, p) => s + p.pnl, 0),
      picks: byDate[d].length,
      hits: byDate[d].filter(p => p.won).length,
    }}));
    const sparkHTML = renderSparkline(dailyUnits);

    // OVER/UNDER split with horizontal bar visual. Bar width reflects
    // share of total picks; color reflects direction. Stats panel on
    // right shows hit rate + units per side.
    let splitHTML = "";
    if (overs.length || unders.length) {{
      const overPct = (overs.length / total) * 100;
      const underPct = (unders.length / total) * 100;
      const overHitPct = overs.length ? (overHits / overs.length * 100).toFixed(0) : "—";
      const underHitPct = unders.length ? (underHits / unders.length * 100).toFixed(0) : "—";
      splitHTML = `<div class="sparkline-wrap" style="margin-top: 10px;">
        <div class="sparkline-title">OVER vs UNDER split</div>
        <div class="split-row">
          <span class="split-label over">OVER</span>
          <div class="split-bar"><div class="split-bar-fill over" style="width: ${{overPct.toFixed(1)}}%"></div></div>
          <div class="split-stats">
            <strong>${{overs.length}}</strong> picks · ${{overHits}}–${{overs.length - overHits}} · <strong>${{overHitPct}}%</strong> · <strong class="${{overUnits >= 0 ? 'pos' : 'neg'}}" style="color: ${{overUnits >= 0 ? 'var(--green)' : 'var(--red)'}}">${{overUnits >= 0 ? '+' : ''}}${{overUnits.toFixed(2)}}u</strong>
          </div>
        </div>
        <div class="split-row">
          <span class="split-label under">UNDER</span>
          <div class="split-bar"><div class="split-bar-fill under" style="width: ${{underPct.toFixed(1)}}%"></div></div>
          <div class="split-stats">
            <strong>${{unders.length}}</strong> picks · ${{underHits}}–${{unders.length - underHits}} · <strong>${{underHitPct}}%</strong> · <strong style="color: ${{underUnits >= 0 ? 'var(--green)' : 'var(--red)'}}">${{underUnits >= 0 ? '+' : ''}}${{underUnits.toFixed(2)}}u</strong>
          </div>
        </div>
      </div>`;
    }}

    const sortedDesc = sortedAsc.slice().reverse();
    const dayRows = sortedDesc.map(d => {{
      const ps = byDate[d];
      const h = ps.filter(p => p.won).length;
      const u = ps.reduce((s, p) => s + p.pnl, 0);
      const uCls = u >= 0 ? 'pos' : 'neg';
      const uStr = (u >= 0 ? '+' : '') + u.toFixed(2);
      return `<tr>
        <td>${{escapeHTML(d)}}</td>
        <td class="num">${{ps.length}}</td>
        <td class="num">${{h}}</td>
        <td class="num ${{uCls}}">${{uStr}}</td>
      </tr>`;
    }}).join("");

    const bucketHTML = renderEdgeBuckets(allPicks);
    const calibrationHTML = renderCalibrationScatter(settledRows);

    // Deep-dive analytics (ROI by edge bucket + calibration scatter)
    // tucked behind a twisty so the top of the section stays light.
    // Only render the twisty wrapper if at least one piece is present.
    const accuracyInner = `${{bucketHTML}}${{calibrationHTML}}`;
    const accuracyHTML = accuracyInner.trim()
      ? `<details class="twisty-wrap model-accuracy-wrap" id="model-accuracy-twisty">
          <summary>Model accuracy details</summary>
          <div class="twisty-body">${{accuracyInner}}</div>
        </details>`
      : "";

    // Cumulative units chart + OVER/UNDER split chart side-by-side as a
    // single row at the top of the section. If only one is present
    // (split needs both directions to render), drop the row wrapper so
    // the lone chart fills the section width as before.
    let chartsRowHTML = "";
    if (sparkHTML && splitHTML) {{
      chartsRowHTML = `<div class="track-charts-row">${{sparkHTML}}${{splitHTML}}</div>`;
    }} else {{
      chartsRowHTML = `${{sparkHTML}}${{splitHTML}}`;
    }}

    const dayTableHTML = `<details class="twisty-wrap track-date-wrap">
      <summary>Daily breakdown (${{sortedDesc.length}} day${{sortedDesc.length === 1 ? "" : "s"}})</summary>
      <div class="twisty-body">
        <div class="table-scroll"><table>
          <thead><tr>
            <th>Date</th>
            <th class="num">Picks</th>
            <th class="num">Hits</th>
            <th class="num">Units</th>
          </tr></thead>
          <tbody>${{dayRows}}</tbody>
        </table></div>
      </div>
    </details>`;

    return `<section class="results-section">
      <h2>Track Record — last ${{maxDays}} days</h2>
      ${{chartsRowHTML}}
      ${{dayTableHTML}}
      ${{accuracyHTML}}
      ${{summaryHTML}}
    </section>`;
  }}

  // DFS payout profiles. The primary "Units" / "ROI" numbers in the
  // Parlay Track Record cards use these fixed multipliers (since DFS
  // sites pay 3× on 2-leg / 5–6× on 3-leg regardless of leg odds), not
  // the sportsbook combined odds. Sportsbook ROI is shown alongside as
  // the "what the model implied was a fair price" baseline.
  const DFS_PROFILES = {{
    prizepicks: {{ label: "PrizePicks (3×/5×)", payouts: {{ 2: 3, 3: 5 }} }},
    underdog:   {{ label: "Underdog (3×/6×)",  payouts: {{ 2: 3, 3: 6 }} }},
  }};
  let _activeDFSProfile = "prizepicks";
  try {{
    const saved = localStorage.getItem("parlay-dfs-profile");
    if (saved && DFS_PROFILES[saved]) _activeDFSProfile = saved;
  }} catch (e) {{ /* private mode etc. */ }}
  let _parlayTrackData = null;
  let _parlayTrackDays = 14;

  function _dfsPnl(won, legCount, profile) {{
    const mult = profile.payouts[legCount];
    if (!mult) return 0;
    return won ? mult - 1 : -1;
  }}

  function renderParlayTrackCards(all, profileKey) {{
    const profile = DFS_PROFILES[profileKey] || DFS_PROFILES.prizepicks;
    const summarize = (rows, label) => {{
      if (!rows.length) return null;
      const n = rows.length;
      const hits = rows.filter(r => r.won).length;
      const hitRate = hits / n;
      const predicted = rows.reduce((s, r) => s + r.predicted, 0) / n;
      const dfsUnits = rows.reduce((s, r) => s + _dfsPnl(r.won, r.legCount, profile), 0);
      const dfsRoi = dfsUnits / n;
      const sbUnits = rows.reduce((s, r) => s + r.pnl, 0);
      const sbRoi = sbUnits / n;
      return {{ label, n, hits, hitRate, predicted, dfsUnits, dfsRoi, sbUnits, sbRoi }};
    }};
    const twoLegRows = all.filter(r => r.section === "two_leg");
    const threeLegRows = all.filter(r => r.section === "three_leg");
    const cards = [
      summarize(twoLegRows, "2-leg"),
      summarize(threeLegRows, "3-leg"),
      summarize(all, "Combined"),
    ].filter(c => c !== null);

    return cards.map(c => {{
      const unitsCls = c.dfsUnits > 0.01 ? "pos" : c.dfsUnits < -0.01 ? "neg" : "";
      const roiCls = c.dfsRoi > 0.001 ? "pos" : c.dfsRoi < -0.001 ? "neg" : "";
      const sbRoiCls = c.sbRoi > 0.001 ? "pos" : c.sbRoi < -0.001 ? "neg" : "";
      const calibDelta = c.hitRate - c.predicted;
      const calibCls = Math.abs(calibDelta) < 0.03 ? "flat"
        : (calibDelta > 0 ? "pos" : "neg");
      const calibLabel = Math.abs(calibDelta) < 0.03 ? "calibrated"
        : (calibDelta > 0 ? "model under-promised" : "model over-promised");
      return `<div class="parlay-track-card">
        <div class="parlay-track-card-header">
          <span class="parlay-track-card-label">${{c.label}}</span>
          <span class="parlay-track-card-count">${{c.n}} card${{c.n === 1 ? "" : "s"}}</span>
        </div>
        <div class="parlay-track-card-hero ${{unitsCls}}">
          ${{c.dfsUnits >= 0 ? "+" : ""}}${{c.dfsUnits.toFixed(2)}}u
        </div>
        <div class="parlay-track-card-grid">
          <div class="parlay-track-stat">
            <span class="parlay-track-stat-label">Actual hit</span>
            <span class="parlay-track-stat-val">${{(c.hitRate * 100).toFixed(0)}}%</span>
          </div>
          <div class="parlay-track-stat">
            <span class="parlay-track-stat-label">Model said</span>
            <span class="parlay-track-stat-val">${{(c.predicted * 100).toFixed(0)}}%</span>
          </div>
          <div class="parlay-track-stat">
            <span class="parlay-track-stat-label">ROI</span>
            <span class="parlay-track-stat-val ${{roiCls}}">${{c.dfsRoi >= 0 ? "+" : ""}}${{(c.dfsRoi * 100).toFixed(1)}}%</span>
          </div>
        </div>
        <div class="parlay-track-card-calib ${{calibCls}}" title="Actual minus predicted hit rate">
          ${{calibDelta >= 0 ? "+" : ""}}${{(calibDelta * 100).toFixed(1)}}pp · ${{calibLabel}}
        </div>
        <div class="parlay-track-card-sb" title="If you could bet at fair sportsbook combined odds — academic, since DFS doesn't pay this much">
          At fair sportsbook odds: <strong class="${{sbRoiCls}}">${{c.sbRoi >= 0 ? "+" : ""}}${{(c.sbRoi * 100).toFixed(1)}}% ROI</strong>
        </div>
      </div>`;
    }}).join("");
  }}

  // Parlay track record — measures the suggester end-to-end (the actual
  // product, not the individual legs). Each day's top 5 two-leg + top 3
  // three-leg cards are snapshotted at slate time by bets/parlay_suggest.py
  // and graded once leg outcomes are known. Predicted vs actual hit rate
  // tells us whether the model's EV calc is calibrated for parlays.
  function renderParlayTrackRecord(track, maxDays, betsState) {{
    const all = (track && track.all) ? track.all : [];
    _parlayTrackData = all;
    _parlayTrackDays = maxDays;
    const actualBlock = renderActualParlayBets(betsState, maxDays);
    if (!all.length) {{
      return `<section class="results-section">
        <h2>Parlay Track Record — last ${{maxDays}} days</h2>
        <p class="muted">No graded parlay suggestions yet. Each day's top 5 two-leg + top 3 three-leg cards get snapshotted at slate time and graded once all legs settle.</p>
        ${{actualBlock}}
      </section>`;
    }}
    const profileSelect = `<label class="parlay-track-profile">
      <span>Payouts</span>
      <select id="parlay-dfs-profile">
        ${{Object.entries(DFS_PROFILES).map(([k, p]) =>
          `<option value="${{k}}" ${{k === _activeDFSProfile ? "selected" : ""}}>${{p.label}}</option>`
        ).join("")}}
      </select>
    </label>`;
    return `<section class="results-section">
      <div class="parlay-track-header">
        <h2>Parlay Track Record — last ${{maxDays}} days</h2>
        ${{profileSelect}}
      </div>
      <p class="results-aux">Top 5 two-leg + top 3 three-leg cards each day, snapshotted at slate time, graded against actual K outcomes (1u flat). Units and ROI use the selected DFS payout schedule.</p>
      <div class="parlay-track-grid" id="parlay-track-grid">${{renderParlayTrackCards(all, _activeDFSProfile)}}</div>
      ${{actualBlock}}
    </section>`;
  }}

  // Real-bets parlay summary: reads the bets ledger directly (no payout
  // assumptions) and shows actual $ P&L grouped by leg count for the
  // last N days. Tailscale-only (the public URL has no /api/bets data).
  function renderActualParlayBets(betsState, maxDays) {{
    if (!betsState || !Array.isArray(betsState.bets)) return "";
    const cutoff = dateInChicago(-(maxDays - 1));
    const recent = betsState.bets.filter(b =>
      b.date && b.date >= cutoff
      && Array.isArray(b.legs) && b.legs.length >= 2
      && (b.result === "W" || b.result === "L")
    );
    if (!recent.length) {{
      return `<div class="parlay-actual-wrap bets-only">
        <h3 class="parlay-actual-title">Your actual parlay bets — last ${{maxDays}} days</h3>
        <p class="muted">No settled parlay bets in this window. Tap a suggested parlay on the Pitchers tab to start tracking real results.</p>
      </div>`;
    }}

    const summarize = (rows, label) => {{
      if (!rows.length) return null;
      const n = rows.length;
      const wins = rows.filter(b => b.result === "W").length;
      const hitRate = wins / n;
      let net = 0;
      let staked = 0;
      for (const b of rows) {{
        const stake = parseFloat(b.stake) || 0;
        const payout = parseFloat(b.payout) || 0;
        const isFree = !!b.free_entry;
        if (b.result === "W") {{
          net += payout - (isFree ? 0 : stake);
        }} else if (!isFree) {{
          net -= stake;
        }}
        if (!isFree) staked += stake;
      }}
      const roi = staked > 0 ? net / staked : 0;
      return {{ label, n, wins, hitRate, net, staked, roi }};
    }};

    const twoLeg = recent.filter(b => b.legs.length === 2);
    const threeLeg = recent.filter(b => b.legs.length === 3);
    const cards = [
      summarize(twoLeg, "2-leg"),
      summarize(threeLeg, "3-leg"),
      summarize(recent, "Combined"),
    ].filter(c => c !== null);

    const cardsHTML = cards.map(c => {{
      const netCls = c.net > 0.5 ? "pos" : c.net < -0.5 ? "neg" : "";
      const roiCls = c.roi > 0.005 ? "pos" : c.roi < -0.005 ? "neg" : "";
      const netStr = `${{c.net >= 0 ? "+" : "−"}}$${{Math.abs(c.net).toFixed(2)}}`;
      return `<div class="parlay-track-card">
        <div class="parlay-track-card-header">
          <span class="parlay-track-card-label">${{c.label}}</span>
          <span class="parlay-track-card-count">${{c.n}} bet${{c.n === 1 ? "" : "s"}}</span>
        </div>
        <div class="parlay-track-card-hero ${{netCls}}">${{netStr}}</div>
        <div class="parlay-track-card-grid">
          <div class="parlay-track-stat">
            <span class="parlay-track-stat-label">Hit rate</span>
            <span class="parlay-track-stat-val">${{(c.hitRate * 100).toFixed(0)}}%</span>
          </div>
          <div class="parlay-track-stat">
            <span class="parlay-track-stat-label">Staked</span>
            <span class="parlay-track-stat-val">$${{c.staked.toFixed(0)}}</span>
          </div>
          <div class="parlay-track-stat">
            <span class="parlay-track-stat-label">ROI</span>
            <span class="parlay-track-stat-val ${{roiCls}}">${{c.roi >= 0 ? "+" : ""}}${{(c.roi * 100).toFixed(1)}}%</span>
          </div>
        </div>
      </div>`;
    }}).join("");

    return `<div class="parlay-actual-wrap bets-only">
      <h3 class="parlay-actual-title">Your actual parlay bets — last ${{maxDays}} days</h3>
      <p class="results-aux">From your bets ledger — real $ P&L using the stake and payout you entered. No payout assumptions.</p>
      <div class="parlay-track-grid">${{cardsHTML}}</div>
    </div>`;
  }}

  // Switch DFS payout profile and repaint just the parlay cards. The
  // underlying outcomes (won/legCount) are already in memory, so this
  // is a re-summarize, not a re-fetch.
  document.addEventListener("change", (e) => {{
    if (!e.target || e.target.id !== "parlay-dfs-profile") return;
    const next = e.target.value;
    if (!DFS_PROFILES[next]) return;
    _activeDFSProfile = next;
    try {{ localStorage.setItem("parlay-dfs-profile", next); }} catch (err) {{}}
    const grid = document.getElementById("parlay-track-grid");
    if (grid && _parlayTrackData) {{
      grid.innerHTML = renderParlayTrackCards(_parlayTrackData, next);
    }}
  }});

  // ---------- Personal bet ledger (local-only tab) ----------

  // Cached today's slate (pitcher_id → row) — populated on tab open.
  // Drives the per-leg picker dropdown and the live-K lookup. Refresh
  // by reloading the tab.
  let slatePitchers = [];
  let slateById = new Map();
  let liveKsByPid = new Map();
  // ISO date the live-ks payload is for (server-supplied). Used to gate
  // auto-settle so old bets don't get re-graded against today's stats
  // when the same pitcher is pitching again.
  let liveKsDate = null;
  let liveLastFetchedAt = null;
  let _betsLivePollTimer = null;

  async function apiBets(method, body, id) {{
    const url = id ? `/api/bets/${{id}}` : "/api/bets";
    const opts = {{ method, headers: {{}} }};
    if (body !== undefined) {{
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }}
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(`${{method}} ${{url}} failed: ${{r.status}}`);
    return r.json();
  }}

  async function apiSlatePitchers() {{
    try {{
      const r = await fetch("/api/slate-pitchers", {{ cache: "no-cache" }});
      if (!r.ok) return [];
      const d = await r.json();
      return d.pitchers || [];
    }} catch (e) {{
      return [];
    }}
  }}

  async function apiBankroll() {{
    try {{
      const r = await fetch("/api/bankroll", {{ cache: "no-cache" }});
      if (!r.ok) return null;
      return await r.json();
    }} catch (e) {{
      return null;
    }}
  }}

  async function apiLiveKs(pitcherIds) {{
    if (!pitcherIds.length) return {{ date: null, results: {{}} }};
    const url = `/api/live-ks?ids=${{pitcherIds.join(",")}}`;
    try {{
      const r = await fetch(url, {{ cache: "no-cache" }});
      if (!r.ok) return {{ date: null, results: {{}} }};
      const d = await r.json();
      return {{ date: d.date || null, results: d.results || {{}} }};
    }} catch (e) {{
      return {{ date: null, results: {{}} }};
    }}
  }}

  // Returns {{user_id, display_name, has_setup}}. user_id === null means
  // not signed in. Always 200 — never throws on auth state alone.
  async function apiWhoami() {{
    try {{
      const r = await fetch("/api/whoami", {{ cache: "no-cache" }});
      if (!r.ok) return {{ user_id: null, display_name: null, has_setup: false }};
      return await r.json();
    }} catch (e) {{
      return {{ user_id: null, display_name: null, has_setup: false }};
    }}
  }}

  async function apiLogin(username, password) {{
    const r = await fetch("/api/login", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ username, password }}),
    }});
    if (!r.ok) {{
      let msg = "Sign-in failed";
      try {{ msg = (await r.json()).error || msg; }} catch (e) {{}}
      throw new Error(msg);
    }}
    return r.json();
  }}

  async function apiLogout() {{
    try {{
      await fetch("/api/logout", {{ method: "POST" }});
    }} catch (e) {{}}
  }}

  async function apiSetup(payload) {{
    const r = await fetch("/api/setup", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(payload),
    }});
    if (!r.ok) {{
      let msg = "Setup failed";
      try {{ msg = (await r.json()).error || msg; }} catch (e) {{}}
      throw new Error(msg);
    }}
    return r.json();
  }}

  function fmtMoney(n) {{
    if (n === null || n === undefined || n === "") return "—";
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    const sign = v < 0 ? "-$" : "$";
    return `${{sign}}${{Math.abs(v).toFixed(2)}}`;
  }}

  function fmtSignedMoney(n) {{
    if (n === null || n === undefined) return "—";
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    const sign = v >= 0 ? "+" : "−";
    return `${{sign}}$${{Math.abs(v).toFixed(2)}}`;
  }}

  function fmtDate(iso) {{
    if (!iso) return "—";
    const parts = iso.split("-");
    if (parts.length !== 3) return iso;
    return `${{parseInt(parts[1], 10)}}/${{parseInt(parts[2], 10)}}`;
  }}

  // Today's date in CT — used to default the form's date field.
  function todayCT() {{
    return dateInChicago(0);
  }}

  function renderBankrollCard(bk) {{
    if (!bk) return "";
    const startingStr = `$${{bk.starting.toFixed(0)}}`;
    const currentStr = `$${{bk.current.toFixed(2)}}`;
    const pct = bk.pct_of_starting;
    const pctStr = pct === null || pct === undefined ? "—" : `${{pct.toFixed(1)}}%`;
    const status = bk.status || "active";
    let cls = "ok";
    let badge = "";
    if (status === "ended") {{
      cls = "ended";
      badge = `<span class="bankroll-badge ended" title="Bankroll bust — experiment over.">ENDED</span>`;
    }} else if (status === "pause_review") {{
      cls = "pause";
      badge = `<span class="bankroll-badge pause" title="Bankroll &le; 50% drawdown — pause and review.">PAUSE&nbsp;REVIEW</span>`;
    }}
    const net = bk.current - bk.starting;
    const netCls = net > 0 ? "pos" : net < 0 ? "neg" : "";
    const netStr = `${{net >= 0 ? "+" : "-"}}$${{Math.abs(net).toFixed(2)}}`;
    const pendingStr = bk.pending_stake > 0
      ? ` · <span class="bankroll-pending">$${{bk.pending_stake.toFixed(2)}} pending</span>`
      : "";
    const tooltip = `Started ${{bk.started_at}} at ${{startingStr}}. Pause review at $${{bk.pause_at.toFixed(0)}}, end at $${{bk.end_at.toFixed(0)}}. Low $${{bk.low_water.toFixed(2)}} / High $${{bk.high_water.toFixed(2)}} (${{bk.events_count}} events).`;
    return `<div class="bankroll-card ${{cls}}" title="${{escapeHTML(tooltip)}}">
      <div class="bankroll-row">
        <div class="bankroll-label">Bankroll</div>
        <div class="bankroll-val">${{currentStr}}</div>
        <div class="bankroll-pct">${{pctStr}} of ${{startingStr}}</div>
        <div class="bankroll-net ${{netCls}}">${{netStr}}</div>
        ${{badge}}
      </div>
      <div class="bankroll-sub">started ${{escapeHTML(bk.started_at)}}${{pendingStr}}</div>
    </div>`;
  }}

  function renderBetsTotals(t) {{
    if (!t) return "";
    const netCls = t.net > 0 ? "pos" : t.net < 0 ? "neg" : "flat";
    const settledLabel = `${{t.wins}}W–${{t.losses}}L${{t.pending ? ` · ${{t.pending}} pending` : ""}}`;
    const roiStr = t.roi !== null
      ? `${{t.roi >= 0 ? "+" : ""}}${{(t.roi * 100).toFixed(1)}}%`
      : "—";
    const stakedSub = t.free_count
      ? `<div class="report-sub">${{t.paid_count}} of ${{t.count}} paid</div>` : "";
    let freeLine = "";
    if (t.free_count) {{
      const wlpBits = [];
      if (t.free_wins) wlpBits.push(`${{t.free_wins}}W`);
      if (t.free_losses) wlpBits.push(`${{t.free_losses}}L`);
      if (t.free_pending) wlpBits.push(`${{t.free_pending}}P`);
      const winningsBit = t.free_winnings > 0
        ? ` · <strong>${{fmtMoney(t.free_winnings)}}</strong> bonus`
        : "";
      freeLine = `<div class="totals-card-secondary">
        <strong>Free entries:</strong> ${{t.free_count}} ticket${{t.free_count === 1 ? "" : "s"}} · ${{wlpBits.join("–") || "all pending"}}${{winningsBit}}
        <span class="muted">(not counted toward staked; winnings flow into Net / ROI)</span>
      </div>`;
    }}
    // Per-site breakdown row — only renders if at least one bet has a
    // site tag. Each cell is count · WL record · net · ROI so you can
    // see at a glance whether one site is actually paying off
    // differently from the others.
    let bySiteLine = "";
    const sites = t.by_site || {{}};
    const siteCodes = Object.keys(sites);
    if (siteCodes.length) {{
      const cells = siteCodes.map(code => {{
        const s = sites[code];
        const wlpBits = [];
        if (s.wins) wlpBits.push(`${{s.wins}}W`);
        if (s.losses) wlpBits.push(`${{s.losses}}L`);
        if (s.pending) wlpBits.push(`${{s.pending}}P`);
        const wlp = wlpBits.join("–") || "no settled";
        const sNetCls = s.net > 0 ? "pos" : s.net < 0 ? "neg" : "";
        const sRoi = s.roi !== null
          ? `${{s.roi >= 0 ? "+" : ""}}${{(s.roi * 100).toFixed(1)}}%`
          : "—";
        return `<span class="site-pnl-cell">
          <strong>${{escapeHTML(code)}}</strong> ${{s.count}} ticket${{s.count === 1 ? "" : "s"}} · ${{wlp}} · <strong class="${{sNetCls}}">${{fmtSignedMoney(s.net)}}</strong> · ${{sRoi}}
        </span>`;
      }}).join("");
      bySiteLine = `<div class="totals-card-secondary site-pnl-row">
        <strong>By site:</strong> ${{cells}}
      </div>`;
    }}
    return `<div class="bets-totals-card">
      <div class="report-stat"><div class="report-label">Bets</div><div class="report-val">${{t.count}}</div><div class="report-sub">${{settledLabel}}</div></div>
      <div class="report-stat"><div class="report-label">Staked</div><div class="report-val">${{fmtMoney(t.staked)}}</div>${{stakedSub}}</div>
      <div class="report-stat"><div class="report-label">Returned</div><div class="report-val">${{fmtMoney(t.returned)}}</div></div>
      <div class="report-stat"><div class="report-label">Net</div><div class="report-val ${{netCls === "flat" ? "" : netCls}}">${{fmtSignedMoney(t.net)}}</div><div class="report-sub">on settled</div></div>
      <div class="report-stat"><div class="report-label">ROI</div><div class="report-val">${{roiStr}}</div></div>
    </div>${{bySiteLine}}${{freeLine}}`;
  }}

  // Daily $ P&L for the Bets tab heatmap. Mirrors the totals card's
  // net rule: free-entry winnings flow into P&L (pure upside, no stake
  // to subtract), but free-entry stakes never count toward staked. So
  // a free win on a day with no paid bets shows as a green tile.
  function computeDailyBetsPnl(bets) {{
    const byDate = {{}};
    for (const b of bets) {{
      if (b.result !== "W" && b.result !== "L") continue;
      const d = b.date;
      if (!d) continue;
      if (!byDate[d]) byDate[d] = {{ date: d, pnl: 0, staked: 0, wins: 0, count: 0 }};
      const stake = parseFloat(b.stake) || 0;
      const payout = parseFloat(b.payout) || 0;
      const isFree = !!b.free_entry;
      if (b.result === "W") {{
        byDate[d].pnl += payout - (isFree ? 0 : stake);
        byDate[d].wins += 1;
      }} else {{
        byDate[d].pnl += isFree ? 0 : -stake;
      }}
      if (!isFree) byDate[d].staked += stake;
      byDate[d].count += 1;
    }}
    return Object.values(byDate);
  }}

  // Bets-tab heatmap. Same shape as the header-scoreboard heat strip,
  // but each tile shows the $
  // P&L as text inside; cells are wider to fit the amount. Window ends
  // on today (last `maxDays` days inclusive); days with no settled
  // paid bets render as muted empty tiles.
  function renderBetsCalendar(dailyPnl, maxDays) {{
    const byDate = {{}};
    for (const u of dailyPnl) byDate[u.date] = u;
    const dates = [];
    for (let i = maxDays - 1; i >= 0; i--) dates.push(dateInChicago(-i));
    const maxAbs = Math.max(0.01, ...dailyPnl.map(u => Math.abs(u.pnl)));
    const cells = dates.map(d => {{
      const dateLabel = fmtDate(d);
      const u = byDate[d];
      if (!u) {{
        return `<div class="cal-cell cal-cell-money cal-empty"
          data-cal-date="${{escapeHTML(d)}}"
          data-cal-empty="1"
          data-cal-money="1">
          <div class="cal-money-amt">—</div>
          <div class="cal-money-date">${{escapeHTML(dateLabel)}}</div>
        </div>`;
      }}
      const sign = u.pnl > 0 ? "pos" : u.pnl < 0 ? "neg" : "flat";
      const intensity = u.pnl === 0 ? 0.30 : Math.max(0.30, Math.abs(u.pnl) / maxAbs);
      const losses = u.count - u.wins;
      const amtStr = `${{u.pnl >= 0 ? "+" : "-"}}$${{Math.abs(u.pnl).toFixed(2)}}`;
      return `<div class="cal-cell cal-cell-money ${{sign}}" style="--cell-i: ${{intensity.toFixed(2)}};"
        data-cal-date="${{escapeHTML(d)}}"
        data-cal-money="1"
        data-cal-pnl="${{u.pnl.toFixed(2)}}"
        data-cal-staked="${{u.staked.toFixed(2)}}"
        data-cal-wins="${{u.wins}}"
        data-cal-losses="${{losses}}"
        data-cal-count="${{u.count}}">
        <div class="cal-money-amt">${{amtStr}}</div>
        <div class="cal-money-date">${{escapeHTML(dateLabel)}}</div>
      </div>`;
    }}).join("");
    return `<div class="cal-wrap">
      <div class="sparkline-title">Daily P&L heatmap (last ${{dates.length}} day${{dates.length === 1 ? "" : "s"}})</div>
      <div class="cal-grid cal-grid-money">${{cells}}</div>
      <div class="cal-legend">
        <span>Loss</span>
        <span class="cal-cell neg" style="--cell-i: 1.00;"></span>
        <span class="cal-cell neg" style="--cell-i: 0.50;"></span>
        <span class="cal-legend-spacer"></span>
        <span class="cal-cell cal-empty"></span>
        <span>no settled bets</span>
        <span class="cal-legend-spacer"></span>
        <span class="cal-cell pos" style="--cell-i: 0.50;"></span>
        <span class="cal-cell pos" style="--cell-i: 1.00;"></span>
        <span>Win</span>
      </div>
      <div class="cal-tip" style="display:none;"></div>
    </div>`;
  }}

  // Compact summary of all legs in a parlay — what shows in the
  // collapsed table row. "Gausman O · Ober U" up to 3 legs, then
  // "Gausman O · Ober U · Skenes O · +1 more" if longer.
  function legsSummary(legs) {{
    if (!legs || !legs.length) return "—";
    const labels = legs.slice(0, 3).map(l => {{
      const ouCls = l.ou === "O" ? "over" : "under";
      const lineStr = l.line !== null && l.line !== undefined && l.line !== ""
        ? ` ${{parseFloat(l.line).toFixed(1)}}` : "";
      return `<span class="parlay-leg-name">${{escapeHTML(l.pitcher || "?")}}</span> <span class="parlay-leg-ou ${{ouCls}}">${{l.ou}}${{escapeHTML(lineStr)}}</span>`;
    }}).join(" · ");
    const extra = legs.length > 3 ? ` <span class="muted">+${{legs.length - 3}} more</span>` : "";
    return labels + extra;
  }}

  // "Placed at" chip — surfaces what the model said about this leg the
  // moment the bet was created (proj/edge/line/odds/EV), plus a delta
  // arrow vs the current slate. Returns "" for legs without a capture
  // (pre-capture bets, or legs without a pitcher_id). Comparing the EV at
  // submission to current EV answers "was this a good bet at the time?"
  // independent of the eventual win/loss.
  function legPlacedAtHTML(leg) {{
    if (!leg || !leg.slate_captured_at) return "";
    const evRaw = leg.slate_ev_per_dollar;
    const ev = (evRaw === null || evRaw === undefined || evRaw === "") ? null : parseFloat(evRaw);
    if (ev === null || isNaN(ev)) return "";
    const evCls = ev > 0.02 ? "pos" : ev < -0.02 ? "neg" : "flat";
    const evStr = (ev >= 0 ? "+" : "") + (ev * 100).toFixed(1) + "%";

    // Drift vs current. 0.02 EV/$1 = 2 percentage points; below that the
    // line basically hasn't moved, so render a flat → instead of an arrow.
    let deltaHTML = "";
    let driftTip = "";
    const cur = (leg.pitcher_id && slateById) ? slateById.get(leg.pitcher_id) : null;
    if (cur) {{
      const curEvRaw = leg.ou === "O" ? cur.ev_over : cur.ev_under;
      const curEv = (curEvRaw === null || curEvRaw === undefined) ? null : parseFloat(curEvRaw);
      if (curEv !== null && !isNaN(curEv)) {{
        const delta = curEv - ev;
        const absD = Math.abs(delta);
        let arrow = "→", cls = "flat";
        if (absD >= 0.02) {{
          arrow = delta > 0 ? "↗" : "↘";
          cls = delta > 0 ? "pos" : "neg";
        }}
        deltaHTML = ` <span class="leg-delta ${{cls}}">${{arrow}}</span>`;
        const curStr = (curEv >= 0 ? "+" : "") + (curEv * 100).toFixed(1) + "%";
        driftTip = ` → now ${{curStr}}`;
      }}
    }}

    // Tooltip carries the full bet-time snapshot — proj, edge, line@odds,
    // book — so hovering a card reconstructs the dashboard state at
    // submission without needing a separate detail view.
    const tipBits = [];
    const edgeNum = parseFloat(leg.slate_edge);
    if (!isNaN(edgeNum)) {{
      const edgeStr = (edgeNum >= 0 ? "+" : "") + (edgeNum * 100).toFixed(1) + "%";
      tipBits.push(`Placed at: EV ${{evStr}}, edge ${{edgeStr}}${{driftTip}}`);
    }} else {{
      tipBits.push(`Placed at: EV ${{evStr}}${{driftTip}}`);
    }}
    const projNum = parseFloat(leg.slate_proj_ks_v2);
    if (!isNaN(projNum)) tipBits.push(`Model proj ${{projNum.toFixed(2)}} K`);
    if (leg.slate_line !== null && leg.slate_line !== undefined && leg.slate_line !== "") {{
      const lineStr = parseFloat(leg.slate_line).toFixed(1);
      const odds = leg.slate_odds;
      const oddsStr = (odds === null || odds === undefined || odds === "") ? "" : ` @ ${{odds > 0 ? "+" + odds : odds}}`;
      const book = leg.slate_book ? ` (${{leg.slate_book}})` : "";
      tipBits.push(`Line ${{lineStr}}${{oddsStr}}${{book}}`);
    }}
    if (leg.slate_pinned) tipBits.push("Pinned to pre-game slate");
    const tip = escapeHTML(tipBits.join(" · "));
    return `<span class="leg-placed-at ${{evCls}}" title="${{tip}}">${{evStr}}${{deltaHTML}}</span>`;
  }}

  function renderBetCard(b, extraCls = "") {{
    let payoutCls = "zero", payoutStr = "—";
    if (b.payout !== null && b.payout !== undefined) {{
      const v = parseFloat(b.payout);
      if (!isNaN(v)) {{
        payoutStr = fmtMoney(v);
        payoutCls = v > 0 ? "pos" : "zero";
      }}
    }}
    let actions = "";
    if (b.result === null) {{
      actions = `<button class="act win" data-action="win">Mark Win</button>
                 <button class="act lose" data-action="lose">Mark Loss</button>
                 <button class="act" data-action="edit">Edit</button>
                 <button class="act del" data-action="delete">Delete</button>`;
    }} else {{
      actions = `<button class="act" data-action="edit">Edit</button>
                 <button class="act" data-action="reopen">Reopen</button>
                 <button class="act del" data-action="delete">Delete</button>`;
    }}
    const legCount = (b.legs || []).length;
    const legsLabel = `${{legCount}}-leg parlay`;
    const freeBadge = b.free_entry ? '<span class="free-badge" title="Free entry — not counted toward staked; winnings still flow into Net / ROI">FREE</span>' : "";
    const siteBadge = b.site ? `<span class="site-badge" title="Placed on ${{escapeHTML(b.site)}}">${{escapeHTML(b.site)}}</span>` : "";
    const stakeDisplay = b.free_entry
      ? `<span class="muted" title="Free entry — not counted toward staked">${{fmtMoney(b.stake)}}</span>`
      : fmtMoney(b.stake);
    const oddsStr = b.odds ? parseFloat(b.odds).toFixed(2) : "—";
    const boostBadge = b.boost ? `<span class="bet-card-boost">${{escapeHTML(b.boost)}}</span>` : "";
    const resultCls = b.result === "W" ? " result-W" : b.result === "L" ? " result-L" : "";
    const cardCls = "bet-card" + (extraCls ? " " + extraCls : "") + resultCls;
    const legItems = (b.legs || []).map((l, i) => {{
      const ouCls = l.ou === "O" ? "over" : "under";
      const lineStr = l.line !== null && l.line !== undefined && l.line !== ""
        ? parseFloat(l.line).toFixed(1) : "—";
      return `<li data-pitcher-id="${{l.pitcher_id || ""}}" data-ou="${{l.ou}}" data-line="${{l.line === null || l.line === undefined ? "" : l.line}}">
        <span class="muted">Leg ${{i + 1}}</span>
        <span class="parlay-leg-name">${{escapeHTML(l.pitcher || "?")}}</span>
        <span class="parlay-leg-ou ${{ouCls}}">${{l.ou}} ${{lineStr}}</span>
        ${{legPlacedAtHTML(l)}}
        <span class="live-cell">${{l.pitcher_id ? "—" : '<span class="muted" style="font-size:11px;">(no live data)</span>'}}</span>
      </li>`;
    }}).join("");
    return `<div class="${{cardCls}}" data-id="${{escapeHTML(b.id)}}" data-date="${{escapeHTML(b.date || "")}}">
      <div class="bet-card-header">
        <div class="bet-card-meta">
          <span class="bet-card-date">${{escapeHTML(fmtDate(b.date))}}</span>
          <span class="bet-card-legcount">${{legsLabel}}</span>
          ${{siteBadge}}
          ${{freeBadge}}
          ${{boostBadge}}
        </div>
        <div class="bet-card-money">
          <span class="bet-card-stake">${{stakeDisplay}}</span>
          <span class="bet-card-arrow">→</span>
          <span class="bet-card-payout ${{payoutCls}}">${{payoutStr}}</span>
          <span class="bet-card-odds">@${{oddsStr}}</span>
        </div>
      </div>
      <ol class="parlay-leg-list">${{legItems}}</ol>
      <div class="bet-card-actions">${{actions}}</div>
    </div>`;
  }}

  // Build one slate-pitcher option. Stores pitcher_id, line, and
  // model recommendation as data-* attributes so the change handler
  // can populate them into the leg without re-fetching.
  function pitcherOptionHTML(p, selectedId) {{
    const sel = (p.pitcher_id && String(p.pitcher_id) === String(selectedId)) ? " selected" : "";
    const ctxBits = [];
    if (p.line !== null) ctxBits.push(`L ${{p.line}}`);
    if (p.our_pick_label && p.our_pick_label !== "—") ctxBits.push(p.our_pick_label);
    const ctx = ctxBits.length ? ` — ${{ctxBits.join(" · ")}}` : "";
    return `<option value="${{p.pitcher_id}}" data-line="${{p.line === null ? "" : p.line}}" data-pick-class="${{escapeHTML(p.our_pick_class || "")}}" data-pick-dir="${{escapeHTML(p.our_pick_dir || "")}}" data-pick-label="${{escapeHTML(p.our_pick_label || "")}}" data-name="${{escapeHTML(p.pitcher)}}"${{sel}}>${{escapeHTML(p.pitcher)}} ${{oppPrefix(p)}}${{escapeHTML(p.opp)}}${{escapeHTML(ctx)}}</option>`;
  }}

  // Renders the leg-input rows inside the form. Called whenever the
  // leg-count selector changes. Preserves any pitcher/ou values the
  // user already typed in the surviving legs.
  function renderLegInputs(legCount, existingLegs) {{
    const rows = [];
    const opts = slatePitchers.map(p => pitcherOptionHTML(p)).join("");
    for (let i = 0; i < legCount; i++) {{
      const ex = existingLegs[i] || {{ pitcher: "", ou: "O", pitcher_id: null, line: null }};
      const overActive = (ex.ou || "O") === "O" ? "active" : "";
      const underActive = (ex.ou || "O") === "U" ? "active" : "";
      // Decide initial picker state. Three cases:
      //   1. pitcher_id matches today's slate → preselect that slate option
      //   2. pitcher_id present but not in today's slate (typical when
      //      editing a past-date bet): inject a self-only "off-slate"
      //      option so the id is preserved on save
      //   3. only a freeform name (no id) → custom-input mode
      const isOffSlate = !!(ex.pitcher && ex.pitcher_id && !slateById.has(ex.pitcher_id));
      const isCustom = !!(ex.pitcher && !ex.pitcher_id);
      const placeholderSel = (!ex.pitcher) ? " selected" : "";
      const customSel = isCustom ? " selected" : "";
      const offSlateOpt = isOffSlate
        ? `<option value="${{ex.pitcher_id}}" data-line="${{ex.line === null || ex.line === undefined ? "" : ex.line}}" data-name="${{escapeHTML(ex.pitcher || "")}}" selected>${{escapeHTML(ex.pitcher)}} (off-slate)</option>`
        : "";
      const selectedOpts = slatePitchers.map(p => pitcherOptionHTML(p, ex.pitcher_id)).join("");
      const customClass = isCustom ? "" : "hidden";
      const ctxLine = ex.pitcher_id && slateById.has(ex.pitcher_id)
        ? legContextHTML(slateById.get(ex.pitcher_id))
        : "";
      const lineVal = ex.line !== null && ex.line !== undefined ? ex.line : "";
      // Mark line as "overridden" (yellow border) when it differs from
      // what the slate currently lists for the same pitcher.
      const slateLine = ex.pitcher_id && slateById.has(ex.pitcher_id)
        ? slateById.get(ex.pitcher_id).line : null;
      const isOverridden = lineVal !== "" && slateLine !== null
        && parseFloat(lineVal) !== parseFloat(slateLine);
      const lineCls = isOverridden ? "leg-line-input overridden" : "leg-line-input";
      rows.push(`<div class="bets-leg-row" data-leg-index="${{i}}">
        <span class="bets-leg-label">Leg ${{i + 1}}</span>
        <div class="leg-picker">
          <select class="pitcher-select" data-line="${{ex.line === null || ex.line === undefined ? "" : ex.line}}" data-pitcher-id="${{ex.pitcher_id || ""}}" data-name="${{escapeHTML(ex.pitcher || "")}}">
            <option value=""${{placeholderSel}}>— Select pitcher —</option>
            <option value="custom"${{customSel}}>[Type custom name]</option>
            <option disabled>──────────</option>
            ${{offSlateOpt}}
            ${{selectedOpts}}
          </select>
          <input class="pitcher-custom ${{customClass}}" type="text" value="${{isCustom ? escapeHTML(ex.pitcher || "") : ""}}" placeholder="Custom pitcher name" />
          <div class="leg-context">${{ctxLine}}</div>
        </div>
        <input class="${{lineCls}}" type="number" step="0.5" value="${{lineVal}}" placeholder="Line" title="DFS line (auto-fills from sportsbook on pitcher select; override if your DFS site differs)" />
        <div class="ou-toggle" data-leg-ou>
          <button type="button" class="${{overActive}} over" data-ou="O">O</button>
          <button type="button" class="${{underActive}} under" data-ou="U">U</button>
        </div>
      </div>`);
    }}
    return rows.join("");
  }}

  // Inline helper: pretty model-context line under the picker.
  function legContextHTML(p) {{
    if (!p) return "";
    const cls = p.our_pick_class || "";
    const lineStr = p.line !== null ? `Line ${{p.line}}` : "no line yet";
    const odds = [];
    if (p.over_odds !== null) odds.push(`O ${{p.over_odds > 0 ? "+" : ""}}${{p.over_odds}}`);
    if (p.under_odds !== null) odds.push(`U ${{p.under_odds > 0 ? "+" : ""}}${{p.under_odds}}`);
    const oddsStr = odds.length ? ` · ${{odds.join(" / ")}}` : "";
    const pickStr = p.our_pick_label && p.our_pick_label !== "—" ? ` · model: ${{p.our_pick_label}}` : "";
    return `<span class="${{cls === "focus" ? p.our_pick_dir : cls === "investigate" ? "investigate" : ""}}">${{lineStr}}${{oddsStr}}${{pickStr}}</span>`;
  }}

  // Snapshot the current form state — used when the leg-count changes
  // (so we don't lose typed-in selections) and when saving.
  function readFormLegs() {{
    const legs = [];
    document.querySelectorAll(".bets-leg-row").forEach(row => {{
      const sel = row.querySelector(".pitcher-select");
      const customInput = row.querySelector(".pitcher-custom");
      const lineInput = row.querySelector(".leg-line-input");
      const ouBtn = row.querySelector(".ou-toggle button.active");
      const ou = ouBtn ? ouBtn.dataset.ou : "O";
      const value = sel.value;
      let leg = {{ pitcher: "", ou, pitcher_id: null, line: null }};
      if (value === "custom") {{
        leg.pitcher = customInput.value.trim();
      }} else if (value && value !== "") {{
        const opt = sel.options[sel.selectedIndex];
        leg.pitcher = opt.dataset.name || "";
        leg.pitcher_id = parseInt(value, 10);
      }}
      // The line input wins regardless of pitcher source. Auto-fill
      // happens on pitcher select; user can override.
      const lineRaw = lineInput.value.trim();
      if (lineRaw !== "") {{
        const parsed = parseFloat(lineRaw);
        if (!isNaN(parsed)) leg.line = parsed;
      }}
      legs.push(leg);
    }});
    return legs;
  }}

  // Mobile-only quick-status strip: one row per still-pending bet,
  // one chip per leg. Each chip shows the pitcher's last name plus
  // K count + pitch count when available; row colors reflect the
  // legHitState verdict (green/red/yellow/gray). The strip is hidden
  // by CSS on desktop, so this code runs unconditionally.
  function qsLastName(full) {{
    if (!full) return "?";
    const parts = String(full).trim().split(/\s+/);
    let last = parts[parts.length - 1] || full;
    if (last.length > 8) last = last.slice(0, 8);
    return last;
  }}

  function renderQuickStatus(state) {{
    const open = (state.bets || []).filter(b =>
      b.result === null || b.result === undefined || b.result === ""
    ).sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    if (!open.length) {{
      return '<div class="bets-quickstatus" id="bets-quickstatus"></div>';
    }}
    const rows = open.map(b => {{
      const legCount = (b.legs || []).length;
      const chips = (b.legs || []).map(l => {{
        const name = qsLastName(l.pitcher);
        const lineAttr = (l.line === null || l.line === undefined) ? "" : l.line;
        return `<span class="bets-qs-chip" data-pitcher-id="${{l.pitcher_id || ''}}" data-ou="${{l.ou || ''}}" data-line="${{lineAttr}}">
          <span class="qs-name">${{escapeHTML(name)}}</span>
          <span class="qs-body">—</span>
        </span>`;
      }}).join("");
      return `<div class="bets-quickstatus-row" data-qs-bet-id="${{escapeHTML(b.id)}}">
        <span class="qs-meta">${{legCount}}L</span>
        ${{chips}}
        <span class="qs-chevron" aria-hidden="true">›</span>
      </div>`;
    }}).join("");
    return `<div class="bets-quickstatus" id="bets-quickstatus">${{rows}}</div>`;
  }}

  function paintQuickStatus() {{
    const root = document.getElementById("bets-quickstatus");
    if (!root) return;
    root.querySelectorAll(".bets-qs-chip[data-pitcher-id]").forEach(chip => {{
      const pid = parseInt(chip.dataset.pitcherId, 10);
      const ou = chip.dataset.ou || "O";
      const line = chip.dataset.line ? parseFloat(chip.dataset.line) : null;
      const body = chip.querySelector(".qs-body");
      chip.classList.remove("hit", "miss", "live");
      chip.removeAttribute("title");
      // Pitcher's wagered side + line (e.g. "U7.5"). Prefixed onto every
      // body so the user can read current Ks against what they bet
      // without expanding the parlay row.
      const ouLine = (line !== null && !isNaN(line))
        ? `${{ou}}${{line.toFixed(1)}}` : "";
      const withLine = (s) => (ouLine && s) ? `${{ouLine}} · ${{s}}` : (ouLine || s || "—");
      if (!pid) {{
        if (body) body.textContent = withLine("");
        return;
      }}
      const live = liveKsByPid.get(pid);
      if (!live) {{
        if (body) body.textContent = withLine("");
        return;
      }}
      const ks = live.ks;
      const verdict = legHitState(ks, line, ou, live.status, live.done);
      const ksTxt = (ks !== null && ks !== undefined) ? `${{ks}}K` : "";
      const pTxt = (live.pitches !== null && live.pitches !== undefined) ? `${{live.pitches}}P` : "";
      let cls = "";
      let txt;
      let title = "";
      if (verdict === "hit") {{
        cls = "hit";
        txt = withLine(ksTxt ? `${{ksTxt}} ✓` : "✓");
        title = live.current_inning ? `Hit · ${{live.current_inning}}` : "Hit";
      }} else if (verdict === "miss") {{
        cls = "miss";
        txt = withLine(ksTxt ? `${{ksTxt}} ✗` : "✗");
        title = live.current_inning ? `Miss · ${{live.current_inning}}` : "Miss";
      }} else if (live.status === "Live") {{
        cls = "live";
        const parts = [ksTxt, pTxt].filter(Boolean).join(" · ");
        const inning = live.current_inning ? ` ${{live.current_inning}}` : "";
        const stat = parts ? `${{parts}}${{inning}}` : `Live${{inning}}`;
        txt = withLine(stat);
        title = `Live${{live.current_inning ? " · " + live.current_inning : ""}}`;
      }} else if (live.status === "Preview") {{
        const t = live.first_pitch
          ? new Date(live.first_pitch).toLocaleTimeString("en-US", {{ hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }})
          : "TBD";
        txt = withLine(t);
        title = "Scheduled";
      }} else if (live.status === "Final") {{
        txt = withLine(ksTxt || "Final");
        title = "Final";
      }} else {{
        txt = withLine(live.detailed || live.status || "");
        title = live.detailed || live.status || "—";
      }}
      if (cls) chip.classList.add(cls);
      if (body) body.textContent = txt;
      if (title) chip.title = title;
    }});
  }}

  function renderBetsTab(state) {{
    const bets = state.bets || [];
    const totals = state.totals;
    const sorted = bets.slice().sort((a, b) => {{
      if (a.date !== b.date) return (b.date || "").localeCompare(a.date || "");
      return 0;
    }});
    const today = todayCT();

    // Default to 2 legs (DFS-site minimum). Form has no current bet
    // being edited at first render — that's a separate code path.
    const defaultLegCount = 2;
    const legOptions = [2, 3, 4, 5, 6]
      .map(n => `<option value="${{n}}">${{n}}-leg</option>`).join("");

    const formHTML = `<div class="bets-form-card" id="bets-form">
      <div class="bets-form-title" id="bf-title">Add a parlay</div>
      <div class="bets-form-top">
        <div class="bets-field"><label>Date</label><input id="bf-date" value="${{today}}" placeholder="YYYY-MM-DD"></div>
        <div class="bets-field">
          <label>Legs</label>
          <select id="bf-legcount" style="background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 5px; padding: 7px 9px; font-family: inherit; font-size: 13px;">
            ${{legOptions}}
          </select>
        </div>
      </div>
      <div class="bets-leg-rows" id="bf-legs">
        ${{renderLegInputs(defaultLegCount, [])}}
      </div>
      <div class="bets-combined-panel" id="bf-combined">
        <div class="bets-combined-stats">
          <div class="parlay-stat"><span class="parlay-stat-label">Payout</span><span class="parlay-stat-val" id="bfc-payout">—</span></div>
          <div class="parlay-stat"><span class="parlay-stat-label">Hit %</span><span class="parlay-stat-val" id="bfc-hit">—</span></div>
          <div class="parlay-stat"><span class="parlay-stat-label">Edge</span><span class="parlay-stat-val" id="bfc-edge">—</span></div>
          <div class="parlay-stat"><span class="parlay-stat-label">EV / $1</span><span class="parlay-stat-val" id="bfc-ev">—</span></div>
          <div class="parlay-stat"><span class="parlay-stat-label">If hit</span><span class="parlay-stat-val" id="bfc-profit">—</span></div>
        </div>
        <div class="bets-combined-hint" id="bfc-hint">Pick pitchers + O/U on each leg to see live payout, hit %, edge, and EV.</div>
      </div>
      <div class="bets-form-bottom">
        <div class="bets-field">
          <label>Site</label>
          <div class="site-toggle" id="bf-site">
            <button type="button" data-site="PP" class="active">PP</button>
            <button type="button" data-site="UD">UD</button>
            <button type="button" data-site="DK">DK</button>
          </div>
        </div>
        <div class="bets-field"><label>Stake</label><input id="bf-stake" type="number" step="0.01" placeholder="10.00"></div>
        <div class="bets-field"><label>Odds</label><input id="bf-odds" type="number" step="0.01" placeholder="2.40"></div>
        <div class="bets-field">
          <label title="Why this stake size? Tags the bet for later analysis (does focus actually outperform default?). Free entries auto-tag as 'free'.">Reason</label>
          <select id="bf-reason" style="background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 5px; padding: 7px 9px; font-family: inherit; font-size: 13px;">
            <option value="default">default (1u)</option>
            <option value="focus">focus (2u)</option>
            <option value="boost">boost</option>
            <option value="other">other</option>
          </select>
        </div>
        <div class="bets-field"><label>Boost (free text)</label><input id="bf-boost" placeholder="+30%"></div>
      </div>
      <div class="bets-form-actions">
        <button id="bf-save" type="button">Save bet</button>
        <button id="bf-cancel" type="button" style="background: transparent; color: var(--muted); border: 1px solid var(--border); display: none;">Cancel edit</button>
        <label title="No money put up — bet won't add to Staked. Winnings (if it hits) still count toward Net / ROI.">
          <input type="checkbox" id="bf-free-entry">
          Free entry (no stake; winnings still count)
        </label>
        <span id="bf-msg" class="bets-form-msg"></span>
      </div>
    </div>`;

    // Show today's bets in detail; anything else (older settled or
    // forgotten older-pending) is hidden behind the "Show older" toggle.
    const todayChi = dateInChicago(0);
    const isToday = (b) => b.date === todayChi;
    const recent = sorted.filter(isToday);
    const older = sorted.filter(b => !isToday(b));

    let cardsBody;
    if (!sorted.length) {{
      cardsBody = `<p class="empty-msg">No bets recorded yet. Add your first one below.</p>`;
    }} else {{
      const recentHTML = recent.length
        ? recent.map(b => renderBetCard(b)).join("")
        : `<p class="empty-msg">No bets for today yet.</p>`;
      const olderHTML = older.map(b => renderBetCard(b, "bets-older-row older-hidden")).join("");
      const olderLabel = `Show ${{older.length}} older bet${{older.length === 1 ? "" : "s"}}`;
      const toggleRow = older.length
        ? `<div class="bets-older-divider"><button type="button" id="bets-older-btn" class="bets-older-btn" data-state="hidden">${{olderLabel}}</button></div>`
        : "";
      cardsBody = recentHTML + toggleRow + olderHTML;
    }}

    const toolbar = `<div class="bets-toolbar">
      <div></div>
      <div>
        <span class="live-stamp" id="live-stamp">live K not yet fetched</span>
        <button type="button" class="refresh-live" id="refresh-live">↻ Refresh live</button>
      </div>
    </div>`;

    const dailyPnl = computeDailyBetsPnl(sorted);
    const heatmapHTML = renderBetsCalendar(dailyPnl, 14);

    return `${{renderUserChip(state.me)}}
      ${{renderQuickStatus(state)}}
      ${{toolbar}}
      <div class="bets-cards" id="bets-cards">${{cardsBody}}</div>
      ${{renderBankrollCard(state.bankroll)}}
      ${{renderBetsTotals(totals)}}
      ${{heatmapHTML}}
      ${{formHTML}}`;
  }}

  function renderUserChip(me) {{
    if (!me || !me.user_id) return "";
    const name = escapeHTML(me.display_name || me.user_id);
    return `<div class="user-chip">
      <span class="user-chip-name">Signed in as <strong>${{name}}</strong></span>
      <a href="#" id="logout-link" class="user-chip-logout">Sign out</a>
    </div>`;
  }}

  // Event delegation: one click handler on the panel handles all
  // per-row buttons (W/L/Edit/Reopen/Delete/Save/Cancel) and the
  // form's Save bet button. Re-fetches and re-renders after each
  // mutation — simpler than incremental DOM updates and the dataset
  // is tiny.
  async function loadBetsTab() {{
    const panel = document.getElementById("bets-panel");
    if (!panel) return;
    panel.innerHTML = '<p class="muted">Loading…</p>';

    // Auth gate: figure out whether to show login form, setup wizard,
    // or the normal bets UI. Cheap call (always 200), so we always
    // hit it — handles cases where the session expired since last load.
    let me;
    try {{
      me = await apiWhoami();
    }} catch (e) {{
      panel.innerHTML = `<p class="empty-msg">Bets API unavailable. Make sure the Flask server is running.</p>`;
      return;
    }}

    if (!me.user_id) {{
      renderLoginForm(panel);
      return;
    }}
    if (!me.has_setup) {{
      renderSetupWizard(panel, me);
      return;
    }}

    try {{
      // Fetch bets + slate + bankroll in parallel so the dropdown is
      // populated and the bankroll card has data by the time we render.
      const [state, pitchers, bankroll] = await Promise.all([
        apiBets("GET"),
        apiSlatePitchers(),
        apiBankroll(),
      ]);
      state.bankroll = bankroll;
      state.me = me;
      slatePitchers = pitchers;
      slateById = new Map(pitchers.map(p => [p.pitcher_id, p]));
      // Keep the pitcher-tab badge index aligned with whatever the bets
      // tab just rendered. Cheap, and saves a duplicate /api/bets fetch
      // when the user toggles tabs after editing a parlay.
      indexBetsByPitcher(state);
      repaintBetBadges();
      panel.innerHTML = renderBetsTab(state);
      wireBetsHandlers(panel);
      wireLogoutHandler(panel);
      // Kick off live K refresh for any pending bets with a pitcher_id,
      // then start the 60s auto-poll so K counts update without the
      // user clicking Refresh live.
      refreshLiveKs();
      startBetsLivePoll();
    }} catch (e) {{
      panel.innerHTML = `<p class="empty-msg">Bets API unavailable. Make sure the Flask server is running.</p>`;
    }}
  }}

  // ──── Auth UI ──────────────────────────────────────────────────────

  function renderLoginForm(panel) {{
    panel.innerHTML = `
      <div class="auth-card">
        <h2 class="auth-title">Sign in</h2>
        <form id="login-form" class="auth-form">
          <label class="auth-row">
            <span>Username</span>
            <input name="username" type="text" autocomplete="username" required autofocus>
          </label>
          <label class="auth-row">
            <span>Password</span>
            <input name="password" type="password" autocomplete="current-password" required>
          </label>
          <div id="login-error" class="auth-error" hidden></div>
          <button type="submit" class="auth-submit">Sign in</button>
        </form>
      </div>
    `;
    const form = panel.querySelector("#login-form");
    const errEl = panel.querySelector("#login-error");
    form.addEventListener("submit", async (ev) => {{
      ev.preventDefault();
      errEl.hidden = true;
      const fd = new FormData(form);
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      submit.textContent = "Signing in…";
      try {{
        await apiLogin(fd.get("username"), fd.get("password"));
        await loadBetsTab();
      }} catch (e) {{
        errEl.textContent = e.message || "Sign-in failed";
        errEl.hidden = false;
        submit.disabled = false;
        submit.textContent = "Sign in";
      }}
    }});
  }}

  function renderSetupWizard(panel, me) {{
    const name = me.display_name || me.user_id;
    panel.innerHTML = `
      <div class="auth-card auth-wizard">
        <h2 class="auth-title">Welcome, ${{escapeHTML(name)}}</h2>
        <p class="auth-sub">A few quick questions to set up your bankroll. You can change all of this later.</p>
        <form id="setup-form" class="auth-form">
          <label class="auth-row">
            <span>Display name</span>
            <input name="display_name" type="text" value="${{escapeHTML(name)}}" required>
          </label>
          <label class="auth-row">
            <span>Starting bankroll ($)</span>
            <input name="starting_bankroll" type="number" min="1" step="1" value="300" required>
          </label>
          <label class="auth-row">
            <span>Pause-review threshold ($)</span>
            <input name="pause_at" type="number" min="0" step="1" value="150" required>
            <small class="auth-hint">If your bankroll drops to this, time to step back and review. Default = 50% of starting.</small>
          </label>
          <label class="auth-row">
            <span>End threshold ($)</span>
            <input name="end_at" type="number" min="0" step="1" value="0" required>
            <small class="auth-hint">Bust line — experiment over.</small>
          </label>
          <div class="auth-row-pair">
            <label class="auth-row">
              <span>1 unit ($)</span>
              <input name="stake_1u" type="number" min="0.5" step="0.5" value="5" required>
            </label>
            <label class="auth-row">
              <span>2 units ($)</span>
              <input name="stake_2u" type="number" min="0.5" step="0.5" value="10" required>
            </label>
          </div>
          <label class="auth-row">
            <span>Pushover user key (optional)</span>
            <input name="pushover_user_key" type="text" placeholder="leave blank to skip notifications">
            <small class="auth-hint">Personal Pushover user key for bet-settled, pulled-starter, and parlay alerts. Get one at pushover.net.</small>
          </label>

          <div class="auth-rules">
            <h3>Bump rules — read before confirming</h3>
            <p><strong>1 unit (default):</strong> standard stake on most picks.</p>
            <p><strong>2 units (bump):</strong> only when at least one of these is true:</p>
            <ul>
              <li><em>All-focus parlay</em> — every leg in the 0.05–0.15 edge band on the dashboard.</li>
              <li><em>Targeted boost</em> — site offers a price improvement on this specific market.</li>
              <li><em>Material info</em> — late lineup/weather/scratch info the slate couldn't have known.</li>
            </ul>
            <p><strong>Veto rules — even if a trigger fires, stay at 1u when:</strong></p>
            <ul>
              <li>Today's bankroll is below yesterday's (no chasing losses)</li>
              <li>Parlay has 3+ legs (variance too high to double up)</li>
              <li>Bankroll is in the bottom third of starting</li>
            </ul>
            <label class="auth-ack">
              <input type="checkbox" name="rules_acknowledged" required>
              <span>I understand the bump rules and will follow them</span>
            </label>
          </div>

          <div id="setup-error" class="auth-error" hidden></div>
          <button type="submit" class="auth-submit">Save and continue</button>
        </form>
      </div>
    `;
    const form = panel.querySelector("#setup-form");
    const errEl = panel.querySelector("#setup-error");
    form.addEventListener("submit", async (ev) => {{
      ev.preventDefault();
      errEl.hidden = true;
      const fd = new FormData(form);
      const payload = {{
        display_name: fd.get("display_name"),
        starting_bankroll: fd.get("starting_bankroll"),
        pause_at: fd.get("pause_at"),
        end_at: fd.get("end_at"),
        stake_1u: fd.get("stake_1u"),
        stake_2u: fd.get("stake_2u"),
        pushover_user_key: fd.get("pushover_user_key") || "",
        rules_acknowledged: !!fd.get("rules_acknowledged"),
      }};
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      submit.textContent = "Saving…";
      try {{
        await apiSetup(payload);
        await loadBetsTab();
      }} catch (e) {{
        errEl.textContent = e.message || "Setup failed";
        errEl.hidden = false;
        submit.disabled = false;
        submit.textContent = "Save and continue";
      }}
    }});
  }}

  function wireLogoutHandler(panel) {{
    const btn = panel.querySelector("#logout-link");
    if (!btn) return;
    btn.addEventListener("click", async (ev) => {{
      ev.preventDefault();
      await apiLogout();
      await loadBetsTab();
    }});
  }}

  // Collect unique pitcher_ids from currently-pending bets and fetch
  // live K status. Updates per-leg rows in place — no full re-render,
  // so the user can be expanding/scrolling without disruption.
  async function refreshLiveKs(opts) {{
    opts = opts || {{}};
    const silent = !!opts.silent;
    const stampEl = document.getElementById("live-stamp");
    const refreshBtn = document.getElementById("refresh-live");
    if (!silent) {{
      if (refreshBtn) refreshBtn.disabled = true;
      if (stampEl) stampEl.textContent = "fetching…";
    }}
    try {{
      // Collect pitcher_ids from all bets (not just pending). Settled
      // bets still benefit from live data so the user can spot a
      // mismatch between their stored W/L and what actually happened.
      // The API will return "Not in today's slate" for pitchers from
      // earlier dates — handled gracefully in the per-leg display.
      const state = await apiBets("GET");
      const pids = new Set();
      for (const b of state.bets) {{
        for (const l of (b.legs || [])) {{
          if (l.pitcher_id) pids.add(l.pitcher_id);
        }}
      }}
      const ids = [...pids];
      if (!ids.length) {{
        liveKsByPid = new Map();
        liveKsDate = null;
        if (stampEl) stampEl.textContent = "no bets with linked pitchers yet";
        if (refreshBtn) refreshBtn.disabled = false;
        return;
      }}
      const {{ date: liveDate, results }} = await apiLiveKs(ids);
      liveKsByPid = new Map(Object.entries(results).map(([k, v]) => [parseInt(k, 10), v]));
      liveKsDate = liveDate;
      // Patch each visible leg's status cell.
      paintLiveKs();
      liveLastFetchedAt = new Date();
      if (stampEl) stampEl.textContent = `updated ${{liveLastFetchedAt.toLocaleTimeString("en-US", {{ hour: "numeric", minute: "2-digit", second: "2-digit" }})}}`;
    }} catch (e) {{
      if (stampEl && !silent) stampEl.textContent = "fetch failed";
    }} finally {{
      if (refreshBtn) refreshBtn.disabled = false;
    }}
  }}

  // 60s auto-poll for the bets tab. Mirrors the pitcher tab pattern:
  // self-stops once every linked pitcher has gone Final, paused while
  // the browser tab is hidden. Manual refresh-button clicks still work
  // at any time and don't conflict — refreshLiveKs is idempotent.
  function stopBetsLivePoll() {{
    if (_betsLivePollTimer) {{
      clearInterval(_betsLivePollTimer);
      _betsLivePollTimer = null;
    }}
  }}
  function startBetsLivePoll() {{
    stopBetsLivePoll();
    _betsLivePollTimer = setInterval(async () => {{
      if (document.hidden) return;
      await refreshLiveKs({{ silent: true }});
      const vals = [...liveKsByPid.values()];
      const stillActive = vals.some(v => v && (v.status === "Preview" || (v.status === "Live" && !v.done)));
      if (vals.length && !stillActive) stopBetsLivePoll();
    }}, 60000);
  }}

  // Decide if a leg's outcome is mathematically settled given current
  // K count + line + game status. Returns:
  //   "hit"  — leg won, locked in
  //   "miss" — leg lost, locked in
  //   null   — still pending (need more data or game to finish)
  //
  // Key insight: Ks can only increase. So once ks > line, the decision
  // is locked: OVER bets WIN (can't go back), UNDER bets LOSE (can't
  // un-record Ks). The opposite cases (over not yet reached, under
  // still alive) need the game to finish before we know.
  function legHitState(ks, line, ou, status, done) {{
    if (ks === null || ks === undefined || line === null || line === undefined) {{
      return null;
    }}
    if (ks > line) {{
      // OVER won, UNDER busted — locked in regardless of status
      return ou === "O" ? "hit" : "miss";
    }}
    if (status === "Final" || done) {{
      // K count locked (game over OR pitcher pulled) and ≤ line:
      // OVER missed, UNDER held.
      return ou === "U" ? "hit" : "miss";
    }}
    return null;  // pending
  }}

  // Build "AWAY 4 @ HOME 2" tooltip from live payload — used on the
  // inning badge so hover reveals the game score. Returns "" when we
  // don't have both scores yet (pre-game, or boxscore lag).
  function scoreTooltip(live) {{
    if (!live) return "";
    const hs = live.home_score, as = live.away_score;
    if (hs === null || hs === undefined || as === null || as === undefined) return "";
    const home = teamAbbr(live.home_team) || "HOME";
    const away = teamAbbr(live.away_team) || "AWAY";
    return `${{away}} ${{as}} @ ${{home}} ${{hs}}`;
  }}

  // Render the live status string + class for one leg given the
  // {{ks, line, status, ...}} payload from /api/live-ks.
  function liveStatusHTML(leg, live) {{
    if (!live) {{
      return `<span class="live-status">—</span>`;
    }}
    const status = live.status;
    const ks = live.ks;
    const pitches = live.pitches;
    const pitchTag = (pitches !== null && pitches !== undefined)
      ? ` <span class="live-pitches">${{pitches}} P</span>` : "";
    // Prefer the bet's recorded line — that's what the user wagered
    // against on the DFS site, which can differ from the sportsbook
    // line cached in the slate.
    const line = (leg.line !== null && leg.line !== undefined)
      ? leg.line : live.line;
    const scoreTip = scoreTooltip(live);
    const scoreAttr = scoreTip ? ` title="${{escapeHTML(scoreTip)}}"` : "";

    // Mid-game lock-in: if the math is already settled, show the
    // verdict immediately without waiting for Final.
    const hitState = legHitState(ks, line, leg.ou, status, live.done);
    if (hitState) {{
      const cls = `live-status ${{hitState}}`;
      const verdict = hitState === "hit" ? "✓" : "✗";
      // Tag the badge with where in the game it locked in. Compact
      // inning form ("B5") keeps the suffix short enough that the row
      // doesn't wrap inside the card layout.
      let inningTag = "";
      const compact = live.current_inning
        ? compactInning(live.inning_state, live.current_inning)
        : "";
      if (live.done && compact) {{
        inningTag = ` <span class="muted" style="font-size:10px;"${{scoreAttr}}>(pulled ${{escapeHTML(compact)}})</span>`;
      }} else if (status === "Live" && compact) {{
        inningTag = ` <span class="muted" style="font-size:10px;"${{scoreAttr}}>(${{escapeHTML(compact)}})</span>`;
      }}
      return `<span class="${{cls}}"><span class="live-ks">${{ks}} K</span>${{pitchTag}}<span class="live-badge"${{scoreAttr}}>${{verdict}}</span>${{inningTag}}</span>`;
    }}

    let cls = "live-status";
    let badge = "";
    let body = "";
    if (status === "Preview") {{
      const pitch = live.first_pitch
        ? new Date(live.first_pitch).toLocaleTimeString("en-US", {{ hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }})
        : "TBD";
      cls += " preview";
      badge = `<span class="live-badge">Sched ${{escapeHTML(pitch)}}</span>`;
      body = "";
    }} else if (status === "Live" && live.done) {{
      // Pitcher pulled but game still going — Ks locked. Reached when
      // the leg has no line (legHitState couldn't render a verdict).
      const inning = live.current_inning
        ? ` ${{escapeHTML(compactInning(live.inning_state, live.current_inning))}}` : "";
      cls += " preview";
      badge = `<span class="live-badge"${{scoreAttr}}>Pulled${{inning}}</span>`;
      body = ks !== null ? `<span class="live-ks">${{ks}} K</span>${{pitchTag}}` : "";
    }} else if (status === "Live") {{
      // Game in progress, math not yet settled (ks ≤ line, OVER could
      // still hit / UNDER could still hold). Compact "B5" form keeps
      // the badge short so the live cell fits on one line.
      const inning = live.current_inning
        ? compactInning(live.inning_state, live.current_inning)
        : "Live";
      cls += " live";
      badge = `<span class="live-badge"${{scoreAttr}}>${{escapeHTML(inning)}}</span>`;
      body = ks !== null ? `<span class="live-ks">${{ks}} K</span>${{pitchTag}}` : "";
    }} else if (status === "Final") {{
      // status=Final but no ks recorded — pitcher didn't pitch, scratch,
      // or stat lookup failed.
      cls += " preview";
      badge = `<span class="live-badge"${{scoreAttr}}>Final</span>`;
      body = ks !== null ? `<span class="live-ks">${{ks}} K</span>${{pitchTag}}` : "no K data";
    }} else {{
      // NotFound, Error, Unknown — pitcher not in today's slate or
      // some lookup failure.
      cls += " preview";
      const detail = live.detailed || status;
      badge = `<span class="live-badge">${{escapeHTML(detail)}}</span>`;
      body = "";
    }}
    return `<span class="${{cls}}">${{body}}${{badge}}</span>`;
  }}

  function paintLiveKs() {{
    const autoSettleQueue = [];
    document.querySelectorAll(".bet-card").forEach(card => {{
      // Per-leg cells
      const legStates = [];
      card.querySelectorAll("li[data-pitcher-id]").forEach(li => {{
        const pid = parseInt(li.dataset.pitcherId, 10);
        const ou = li.dataset.ou;
        const line = li.dataset.line ? parseFloat(li.dataset.line) : null;
        const cell = li.querySelector(".live-cell");
        const live = liveKsByPid.get(pid);
        if (cell) cell.innerHTML = liveStatusHTML({{ ou, line }}, live);
        if (live) {{
          legStates.push(legHitState(live.ks, line, ou, live.status, live.done));
        }} else {{
          // No live data — could be a leg without a pitcher_id, or fetch
          // hasn't happened yet. Treat as pending.
          legStates.push(null);
        }}
      }});
      const betId = card.dataset.id;
      // Apply a live-* tint class to the card based on the current
      // rollup verdict. Manual W/L marks override these in CSS so a
      // user-marked bet keeps its result tint. No live data → no tint.
      card.classList.remove("live-win", "live-loss", "live-pending");
      const hasLiveData = legStates.some(s => s !== null);
      const verdictCls = parlayRollupClass(legStates);
      if (hasLiveData) {{
        card.classList.add(`live-${{verdictCls}}`);
      }}
      // Queue auto-settle ONLY if verdict is definitive AND the bet's
      // date matches the date the live-ks data is for. Without this date
      // gate, an old bet whose pitcher happens to be pitching again
      // today can be re-graded against today's K count.
      const betDate = card.dataset.date || "";
      const dateMatches = liveKsDate && betDate === liveKsDate;
      if (dateMatches && (verdictCls === "win" || verdictCls === "loss")) {{
        autoSettleQueue.push({{ betId, verdict: verdictCls }});
      }}
    }});
    // Mobile-only quick-status strip uses the same liveKsByPid map —
    // repaint it here so it always stays in sync with the detail rows.
    paintQuickStatus();
    // Fire auto-settles asynchronously after painting completes.
    if (autoSettleQueue.length) maybeAutoSettle(autoSettleQueue);
  }}

  // Auto-settle: when a parlay's verdict is mathematically definitive
  // (all hit or any miss), update the bet's stored result if it doesn't
  // match. Win → result=W, payout=stake*odds. Loss → result=L, payout=0.
  // The bet.result === targetResult check is the dedupe guard — once
  // PUT succeeds, the next refresh sees the matching result and skips.
  // User can override by clicking Reopen and they'll be re-settled on
  // the following refresh (which is the desired behavior).
  let autoSettleInFlight = false;
  async function maybeAutoSettle(queue) {{
    if (autoSettleInFlight) return;
    autoSettleInFlight = true;
    try {{
      const state = await apiBets("GET");
      const byId = new Map(state.bets.map(b => [b.id, b]));
      const updated = [];
      for (const {{ betId, verdict }} of queue) {{
        const bet = byId.get(betId);
        if (!bet) continue;
        const targetResult = verdict === "win" ? "W" : "L";
        if (bet.result === targetResult) continue;
        const targetPayout = verdict === "win"
          ? +((bet.stake || 0) * (bet.odds || 0)).toFixed(2)
          : 0;
        try {{
          await apiBets("PUT", {{ result: targetResult, payout: targetPayout }}, betId);
          updated.push({{
            legs: bet.legs.map(l => l.pitcher).join(" + "),
            verdict: targetResult,
          }});
        }} catch (e) {{
          // Silent fail — user can hit Reopen + manual settle if needed.
        }}
      }}
      if (updated.length) {{
        const stampEl = document.getElementById("live-stamp");
        const summary = updated
          .map(u => `${{u.legs}} → ${{u.verdict}}`)
          .join(", ");
        if (stampEl) stampEl.textContent = `auto-settled: ${{summary}}`;
        // Defer the full re-render if the user is mid-edit in the bets
        // form — loadBetsTab() blows away the form's DOM and would
        // wipe whatever they're typing. The next refreshLiveKs tick
        // will retry, and once focus leaves the form the rerender lands.
        const form = document.getElementById("bets-form");
        const editing = form && form.contains(document.activeElement);
        if (!editing) {{
          await loadBetsTab();
        }} else if (stampEl) {{
          stampEl.textContent += " (refresh deferred — form in use)";
        }}
      }}
    }} finally {{
      autoSettleInFlight = false;
    }}
  }}

  // Aggregate per-leg HIT/MISS/pending into parlay status. Match against
  // the bet's stored result so we can warn on mismatches (e.g. user
  // marked W but a leg already busted).
  function parlayRollupClass(legStates) {{
    if (legStates.some(s => s === "miss")) return "loss";
    if (legStates.length && legStates.every(s => s === "hit")) return "win";
    return "pending";
  }}

  // The form has two modes: "add" (default — POST on save) and "edit"
  // (loaded from a bet — PUT on save). editingBetId is the discriminant.
  let editingBetId = null;

  // Site toggle (PP / UD / DK) — small button-group with one .active.
  // Default to PP when nothing is set / value is unrecognized.
  function readFormSite() {{
    const grp = document.getElementById("bf-site");
    if (!grp) return "PP";
    const active = grp.querySelector("button.active");
    return active ? (active.dataset.site || "PP") : "PP";
  }}
  function setFormSite(site) {{
    const grp = document.getElementById("bf-site");
    if (!grp) return;
    const target = (site || "PP").toUpperCase();
    const buttons = grp.querySelectorAll("button");
    let matched = false;
    buttons.forEach(b => {{
      const m = b.dataset.site === target;
      b.classList.toggle("active", m);
      if (m) matched = true;
    }});
    if (!matched && buttons.length) {{
      buttons.forEach(b => b.classList.remove("active"));
      buttons[0].classList.add("active");
    }}
  }}

  function readForm() {{
    const get = id => document.getElementById(id).value.trim();
    return {{
      date: get("bf-date"),
      legs: readFormLegs(),
      stake: get("bf-stake"),
      odds: get("bf-odds"),
      boost: get("bf-boost"),
      site: readFormSite(),
      free_entry: document.getElementById("bf-free-entry").checked,
      stake_reason: get("bf-reason"),
    }};
  }}

  // Translate a form leg (pitcher_id + ou + line) into the leg shape
  // evaluateParlay() consumes. Returns null when the leg can't be priced
  // (no pitcher selected, custom name not on slate, missing odds for the
  // chosen direction). Line override falls through unchanged — we still
  // use the slate's p_over / novig as the closest-available estimate;
  // hint text below the panel flags this so the user knows.
  function buildLegFromForm(formLeg) {{
    if (!formLeg.pitcher_id) return null;
    const slate = slateById.get(formLeg.pitcher_id);
    if (!slate) return null;
    const dir = formLeg.ou === "U" ? "under" : "over";
    const odds = dir === "over" ? slate.over_odds : slate.under_odds;
    const dec = americanToDecimal(odds);
    if (dec === null) return null;
    const pOver = slate.p_over;
    if (pOver === null || pOver === undefined) return null;
    const novigOver = slate.novig_over;
    return {{
      pitcher: slate.pitcher,
      pitcher_id: formLeg.pitcher_id,
      line: formLeg.line !== null ? formLeg.line : slate.line,
      dir,
      odds,
      decOdds: dec,
      hitProb: dir === "over" ? pOver : 1 - pOver,
      novigP: (novigOver === null || novigOver === undefined)
        ? null
        : (dir === "over" ? novigOver : 1 - novigOver),
      edge: 0,
    }};
  }}

  // Recompute the live "Combined" panel from the current form state.
  // Called on any leg-row change (pitcher select, line input, O/U
  // toggle, leg-count). Auto-fills the Odds field with the parlay
  // decimal when all legs price — but only if the user hasn't manually
  // overridden it (tracked via dataset.autoFilled).
  function recomputeCombined() {{
    const panel = document.getElementById("bf-combined");
    if (!panel) return;
    const formLegs = readFormLegs();
    const legCount = formLegs.length;
    const legs = formLegs.map(buildLegFromForm).filter(l => l !== null);
    const priced = legs.length;

    const setVal = (id, txt, cls) => {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = txt;
      el.classList.remove("pos", "neg");
      if (cls) el.classList.add(cls);
    }};
    panel.classList.remove("pos", "neg");

    const hint = document.getElementById("bfc-hint");
    const oddsEl = document.getElementById("bf-odds");
    const stakeEl = document.getElementById("bf-stake");

    if (priced < legCount || priced < 2) {{
      setVal("bfc-payout", "—");
      setVal("bfc-hit", "—");
      setVal("bfc-edge", "—");
      setVal("bfc-ev", "—");
      setVal("bfc-profit", "—");
      if (hint) {{
        const lacking = legCount - priced;
        hint.textContent = priced === 0
          ? "Pick pitchers + O/U on each leg to see live payout, hit %, edge, and EV."
          : `${{priced}} of ${{legCount}} legs priced — ${{lacking}} more to compute combined stats.`;
      }}
      // Don't clear odds the user manually entered.
      if (oddsEl && oddsEl.dataset.autoFilled === "true") {{
        oddsEl.value = "";
        delete oddsEl.dataset.autoFilled;
      }}
      return;
    }}

    const p = evaluateParlay(legs);
    const evCls = p.ev > 0.02 ? "pos" : p.ev < -0.02 ? "neg" : "";
    const edgeCls = p.combinedEdge === null
      ? ""
      : (p.combinedEdge > 0.005 ? "pos" : p.combinedEdge < -0.005 ? "neg" : "");

    if (evCls === "pos") panel.classList.add("pos");
    else if (evCls === "neg") panel.classList.add("neg");

    setVal(
      "bfc-payout",
      p.combinedAmer === null
        ? "—"
        : (p.combinedAmer >= 0 ? "+" : "") + p.combinedAmer,
    );
    setVal("bfc-hit", (p.combinedHit * 100).toFixed(1) + "%");
    setVal(
      "bfc-edge",
      p.combinedEdge === null
        ? "—"
        : (p.combinedEdge >= 0 ? "+" : "") + (p.combinedEdge * 100).toFixed(1) + "%",
      edgeCls
    );
    setVal("bfc-ev", (p.ev >= 0 ? "+" : "") + p.ev.toFixed(2), evCls);

    const stake = parseFloat(stakeEl ? stakeEl.value : "");
    if (!isNaN(stake) && stake > 0) {{
      const profit = stake * (p.combinedDec - 1);
      setVal("bfc-profit", "+$" + profit.toFixed(2));
    }} else {{
      setVal("bfc-profit", "—");
    }}

    // Auto-fill Odds (decimal) when empty or matches a previous auto-fill.
    if (oddsEl) {{
      const target = p.combinedDec.toFixed(2);
      if (oddsEl.value.trim() === "" || oddsEl.dataset.autoFilled === "true") {{
        oddsEl.value = target;
        oddsEl.dataset.autoFilled = "true";
      }}
    }}

    // Hint when any leg's line in the form differs from slate (combined
    // hit prob still uses slate-line probability — flag the limitation).
    if (hint) {{
      const overrides = formLegs.filter((fl, i) => {{
        if (!fl.pitcher_id) return false;
        const sl = slateById.get(fl.pitcher_id);
        if (!sl || sl.line === null || sl.line === undefined) return false;
        return fl.line !== null && parseFloat(fl.line) !== parseFloat(sl.line);
      }});
      hint.textContent = overrides.length
        ? `Heads up: ${{overrides.length}} leg${{overrides.length === 1 ? "" : "s"}} use a custom line — Hit %/Edge still computed at the slate's line.`
        : "Live from today's slate · independent legs · auto-fills the Odds field.";
    }}
  }}

  function clearForm() {{
    document.getElementById("bf-date").value = todayCT();
    document.getElementById("bf-stake").value = "";
    document.getElementById("bf-odds").value = "";
    document.getElementById("bf-boost").value = "";
    document.getElementById("bf-free-entry").checked = false;
    const reasonEl = document.getElementById("bf-reason");
    if (reasonEl) reasonEl.value = "default";
    setFormSite("PP");
    document.getElementById("bf-legcount").value = "2";
    document.getElementById("bf-legs").innerHTML = renderLegInputs(2, []);
    setFormMode("add");
  }}

  function loadBetIntoForm(bet) {{
    document.getElementById("bf-date").value = bet.date || "";
    document.getElementById("bf-stake").value = bet.stake !== null ? bet.stake : "";
    document.getElementById("bf-odds").value = bet.odds !== null ? bet.odds : "";
    document.getElementById("bf-boost").value = bet.boost || "";
    document.getElementById("bf-free-entry").checked = !!bet.free_entry;
    const reasonEl = document.getElementById("bf-reason");
    if (reasonEl) {{
      // Free entries always tag as 'free_entry' server-side; the picker
      // shows the user-facing options only — fall back to 'default' for
      // a free entry's row, since the server handles the override.
      reasonEl.value = bet.stake_reason && bet.stake_reason !== "free_entry"
        ? bet.stake_reason
        : "default";
    }}
    setFormSite(bet.site || "PP");
    const legCount = Math.max(2, Math.min(6, (bet.legs || []).length || 2));
    document.getElementById("bf-legcount").value = String(legCount);
    document.getElementById("bf-legs").innerHTML = renderLegInputs(legCount, bet.legs || []);
    setFormMode("edit", bet.id);
    document.getElementById("bets-form").scrollIntoView({{ behavior: "smooth", block: "start" }});
  }}

  function setFormMode(mode, betId) {{
    const title = document.getElementById("bf-title");
    const saveBtn = document.getElementById("bf-save");
    const cancelBtn = document.getElementById("bf-cancel");
    if (mode === "edit") {{
      editingBetId = betId;
      title.textContent = "Editing bet";
      saveBtn.textContent = "Update bet";
      cancelBtn.style.display = "";
    }} else {{
      editingBetId = null;
      title.textContent = "Add a parlay";
      saveBtn.textContent = "Save bet";
      cancelBtn.style.display = "none";
    }}
  }}

  function wireBetsHandlers(panel) {{
    const saveBtn = document.getElementById("bf-save");
    const cancelBtn = document.getElementById("bf-cancel");
    const msg = document.getElementById("bf-msg");
    const legCountSel = document.getElementById("bf-legcount");
    const legsContainer = document.getElementById("bf-legs");

    // "Show older" toggle: reveal/hide bet cards from non-today dates.
    const olderBtn = document.getElementById("bets-older-btn");
    if (olderBtn) {{
      olderBtn.addEventListener("click", (e) => {{
        e.stopPropagation();
        const hide = olderBtn.dataset.state !== "hidden";
        panel.querySelectorAll(".bet-card.bets-older-row").forEach(card => {{
          card.classList.toggle("older-hidden", hide);
        }});
        olderBtn.dataset.state = hide ? "hidden" : "shown";
        const olderCount = panel.querySelectorAll(".bet-card.bets-older-row").length;
        olderBtn.textContent = hide
          ? `Show ${{olderCount}} older bet${{olderCount === 1 ? "" : "s"}}`
          : `Hide older bet${{olderCount === 1 ? "" : "s"}}`;
      }});
    }}

    // Leg-count change: re-render leg inputs while preserving any
    // pitcher names / O/U toggles already filled in.
    if (legCountSel) {{
      legCountSel.addEventListener("change", () => {{
        const existing = readFormLegs();
        const n = parseInt(legCountSel.value, 10);
        legsContainer.innerHTML = renderLegInputs(n, existing);
        recomputeCombined();
      }});
    }}

    // O/U toggle clicks (event delegation in the form).
    if (legsContainer) {{
      legsContainer.addEventListener("click", (e) => {{
        const btn = e.target.closest(".ou-toggle button");
        if (!btn) return;
        const toggle = btn.parentElement;
        toggle.querySelectorAll("button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        recomputeCombined();
      }});

      // Pitcher-select change: toggle custom input visibility, fill
      // model-context line, auto-fill line input.
      legsContainer.addEventListener("change", (e) => {{
        const sel = e.target.closest(".pitcher-select");
        if (!sel) return;
        const row = sel.closest(".bets-leg-row");
        const custom = row.querySelector(".pitcher-custom");
        const ctx = row.querySelector(".leg-context");
        const lineInput = row.querySelector(".leg-line-input");
        const value = sel.value;
        if (value === "custom") {{
          custom.classList.remove("hidden");
          custom.focus();
          ctx.innerHTML = "";
        }} else if (value === "") {{
          custom.classList.add("hidden");
          ctx.innerHTML = "";
        }} else {{
          custom.classList.add("hidden");
          const pid = parseInt(value, 10);
          const p = slateById.get(pid);
          ctx.innerHTML = legContextHTML(p);
          // Auto-fill line from slate (only if user hasn't typed one
          // already — preserve manual override on re-select).
          if (p && p.line !== null && lineInput.value.trim() === "") {{
            lineInput.value = p.line;
            lineInput.classList.remove("overridden");
          }}
          // Auto-set O/U toggle to match the model's recommendation —
          // helpful default but user can override.
          if (p && p.our_pick_dir) {{
            const target = p.our_pick_dir === "over" ? "O" : "U";
            row.querySelectorAll(".ou-toggle button").forEach(b => {{
              b.classList.toggle("active", b.dataset.ou === target);
            }});
          }}
        }}
        recomputeCombined();
      }});

      // Line-input changes: visually mark when value differs from the
      // slate's line for the selected pitcher.
      legsContainer.addEventListener("input", (e) => {{
        const lineInput = e.target.closest(".leg-line-input");
        if (!lineInput) return;
        const row = lineInput.closest(".bets-leg-row");
        const sel = row.querySelector(".pitcher-select");
        const value = sel.value;
        if (!value || value === "custom" || value === "") {{
          lineInput.classList.remove("overridden");
          recomputeCombined();
          return;
        }}
        const pid = parseInt(value, 10);
        const p = slateById.get(pid);
        if (p && p.line !== null && lineInput.value.trim() !== "") {{
          const diff = parseFloat(lineInput.value) !== parseFloat(p.line);
          lineInput.classList.toggle("overridden", diff);
        }} else {{
          lineInput.classList.remove("overridden");
        }}
        recomputeCombined();
      }});
    }}

    // Stake input drives "Profit if hit" in the Combined panel — but
    // also flag manual Odds edits so we stop auto-filling once the user
    // takes ownership of that field.
    const stakeEl = document.getElementById("bf-stake");
    if (stakeEl) stakeEl.addEventListener("input", () => recomputeCombined());
    const oddsEl = document.getElementById("bf-odds");
    if (oddsEl) {{
      oddsEl.addEventListener("input", () => {{
        // Once the user types, stop auto-filling. Their value wins.
        delete oddsEl.dataset.autoFilled;
      }});
    }}

    // Site toggle (PP / UD / DK): mark the clicked button .active.
    const siteGrp = document.getElementById("bf-site");
    if (siteGrp) {{
      siteGrp.addEventListener("click", (e) => {{
        const btn = e.target.closest("button[data-site]");
        if (!btn) return;
        siteGrp.querySelectorAll("button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      }});
    }}

    // Initial paint after first render — handles the case where the
    // user arrived by tapping a suggested parlay card with pre-populated
    // legs.
    recomputeCombined();

    if (saveBtn) {{
      saveBtn.addEventListener("click", async () => {{
        msg.classList.remove("error");
        msg.textContent = "";
        const data = readForm();
        const goodLegs = data.legs.filter(l => l.pitcher);
        if (goodLegs.length < 2) {{
          msg.classList.add("error");
          msg.textContent = "Each parlay needs at least 2 legs with a pitcher name.";
          return;
        }}
        if (goodLegs.length !== data.legs.length) {{
          msg.classList.add("error");
          msg.textContent = "Fill in all leg pitcher names, or reduce the leg count.";
          return;
        }}
        if (!data.stake || !data.odds) {{
          msg.classList.add("error");
          msg.textContent = "Stake and odds are required.";
          return;
        }}
        try {{
          if (editingBetId) {{
            await apiBets("PUT", data, editingBetId);
            msg.textContent = "Updated.";
          }} else {{
            await apiBets("POST", data);
            msg.textContent = "Saved.";
          }}
          // Re-render rebuilds the form HTML in add-mode (title/button
          // text + empty inputs), but editingBetId is module-scoped and
          // survives. Without this reset, the next save silently PUTs
          // over the just-edited bet — i.e. "second bet replaces first".
          editingBetId = null;
          await loadBetsTab();
        }} catch (e) {{
          msg.classList.add("error");
          msg.textContent = "Save failed.";
        }}
      }});
    }}

    if (cancelBtn) {{
      cancelBtn.addEventListener("click", () => clearForm());
    }}

    const refreshLiveBtn = document.getElementById("refresh-live");
    if (refreshLiveBtn) {{
      refreshLiveBtn.addEventListener("click", () => refreshLiveKs());
    }}

    // Panel-level click handler is attached to the panel element itself
    // (not its children, which get replaced each render). If we attach
    // it on every wireBetsHandlers call, listeners stack and toggling
    // happens N times per click — even N = no visible effect. Guard
    // with a one-time flag so it only attaches once for the page life.
    if (panel.dataset.clickAttached === "true") return;
    panel.dataset.clickAttached = "true";

    panel.addEventListener("click", async (e) => {{
      // Tap on the mobile quick-status strip: reveal actions on the
      // matching card and scroll it into view so the user can drill in
      // without thumb-scrolling through the full list.
      const qsRow = e.target.closest(".bets-quickstatus-row");
      if (qsRow) {{
        const id = qsRow.dataset.qsBetId;
        const card = panel.querySelector(`.bet-card[data-id="${{id}}"]`);
        if (card) {{
          card.classList.add("expanded-actions");
          card.scrollIntoView({{ behavior: "smooth", block: "start" }});
        }}
        return;
      }}
      // Action buttons take priority — don't toggle expand on button clicks.
      const btn = e.target.closest("button.act");
      if (!btn) {{
        // Click anywhere on a bet card toggles its action drawer.
        const card = e.target.closest(".bet-card");
        if (card) card.classList.toggle("expanded-actions");
        return;
      }}
      const card = btn.closest(".bet-card");
      const id = card.dataset.id;
      const action = btn.dataset.action;

      if (action === "delete") {{
        if (!confirm("Delete this bet?")) return;
        await apiBets("DELETE", undefined, id);
        await loadBetsTab();
        return;
      }}

      if (action === "win" || action === "lose") {{
        // Quick settle: auto-compute payout from stake × odds for win,
        // 0 for loss. User can edit afterwards if a boost adjusts it.
        const state = await apiBets("GET");
        const bet = state.bets.find(b => b.id === id);
        if (!bet) return;
        const payout = action === "win"
          ? +((bet.stake || 0) * (bet.odds || 0)).toFixed(2)
          : 0;
        await apiBets("PUT", {{
          result: action === "win" ? "W" : "L",
          payout: payout,
        }}, id);
        await loadBetsTab();
        return;
      }}

      if (action === "reopen") {{
        await apiBets("PUT", {{ result: null, payout: null }}, id);
        await loadBetsTab();
        return;
      }}

      if (action === "edit") {{
        const state = await apiBets("GET");
        const bet = state.bets.find(b => b.id === id);
        if (bet) loadBetIntoForm(bet);
        return;
      }}
    }});
  }}

  function counts(rows) {{
    const c = {{ focus: 0, investigate: 0, noise: 0, noline: 0 }};
    for (const r of rows) c[classify(pickEdge(r))]++;
    return c;
  }}

  function settledTitle(d) {{
    if (!d) return "Recent Results";
    const today = dateInChicago(0);
    const fmt = new Intl.DateTimeFormat("en-US", {{
      timeZone: "America/Chicago",
      weekday: "long", year: "numeric", month: "long", day: "numeric"
    }});
    // Parse YYYY-MM-DD safely (avoid local-tz off-by-one)
    const [yy, mm, dd] = d.split("-").map(Number);
    const dateObj = new Date(Date.UTC(yy, mm - 1, dd, 12));
    const label = fmt.format(dateObj);
    if (d === today) return `Today's Results — ${{label}}`;
    // Yesterday in CT?
    const yesterday = dateInChicago(-1);
    if (d === yesterday) return `Yesterday's Results — ${{label}}`;
    return `Most Recent Results — ${{label}}`;
  }}

  function avg(arr) {{ return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0; }}

  function renderPitcherTab(target) {{
    const slate = target.slate;
    const settled = target.settled;
    const trackPicks = target.trackPicks || [];
    const trackDays = target.trackDays || 14;
    const hasRows = slate.rows.length > 0;
    const sorted = hasRows ? sortRows(slate.rows, "proj_ks_v2") : [];
    const cnt = counts(slate.rows);

    const slateBody = hasRows
      ? sorted.map(pitcherRow).join("")
      : `<tr><td colspan="11" class="empty-msg">No pitcher projections yet for today. <a href="https://github.com/{REPO}/actions" style="color: var(--green);">Trigger a pipeline run</a> from the Actions tab, or click Re-run pipeline locally.</td></tr>`;

    const heroSection = hasRows ? renderHeroPicks(slate.rows) : "";
    const parlaySection = hasRows ? renderParlaySuggestions(slate.rows) : "";

    let resultsSection = "";
    if (settled.rows.length) {{
      const errs = settled.rows
        .map(r => f(r.error_v2) || f(r.error_v1))
        .filter(x => x !== null);
      const mae = errs.length ? avg(errs.map(Math.abs)) : 0;
      const bias = errs.length ? avg(errs) : 0;
      const sortedSettled = settled.rows.slice().sort((a, b) => {{
        const ea = f(a.error_v2) || f(a.error_v1) || 0;
        const eb = f(b.error_v2) || f(b.error_v1) || 0;
        return Math.abs(eb) - Math.abs(ea);
      }});

      // Distill the day's actionable picks (focus band) into W-L-units
      // for the report-card header. Must match what Today's Picks
      // surfaces — i.e. isBettableFocus: calibrated cal_edge_v2 in the
      // focus band AND line >= MIN_LINE_FOR_FOCUS (gates out
      // openers/relievers). Falls back to raw edge for pre-Platt
      // historical rows (pre-2026-05-11) that lack cal_edge_v2.
      const dayPicks = [];
      for (const r of settled.rows) {{
        const calEdge = f(slateOrLive(r, "slate_cal_edge_v2", "cal_edge_v2"));
        const rawEdge = f(slateOrLive(r, "slate_edge", "edge"));
        const edge = calEdge !== null ? calEdge : rawEdge;
        if (edge === null) continue;
        if (classify(edge) !== "focus") continue;
        const line = f(slateOrLive(r, "slate_line", "line"));
        if (line === null || line < MIN_LINE_FOR_FOCUS) continue;
        const overHit = f(slateOrLive(r, "slate_over_hit", "over_hit"));
        if (overHit === null) continue;
        const dir = edge > 0 ? "over" : "under";
        const won = (dir === "over" && overHit >= 1) ||
                    (dir === "under" && overHit < 1);
        const pnlField = dir === "over"
          ? slateOrLive(r, "slate_over_pnl", "over_pnl")
          : slateOrLive(r, "slate_under_pnl", "under_pnl");
        const pnl = f(pnlField);
        dayPicks.push({{ won, pnl: pnl === null ? 0 : pnl }});
      }}
      const wins = dayPicks.filter(p => p.won).length;
      const losses = dayPicks.length - wins;
      const dayUnits = dayPicks.reduce((s, p) => s + p.pnl, 0);
      const dayHitRate = dayPicks.length ? wins / dayPicks.length : null;

      let reportCardHTML = "";
      if (dayPicks.length) {{
        const unitsCls = dayUnits > 0.05 ? "pos" : dayUnits < -0.05 ? "neg" : "flat";
        const headlineCls = `headline ${{unitsCls === "pos" ? "" : unitsCls}}`.trim();
        reportCardHTML = `<div class="report-card">
          <div class="report-stat ${{headlineCls}}">
            <div class="report-label">Record</div>
            <div class="report-val">${{wins}}–${{losses}}</div>
            <div class="report-sub">${{dayPicks.length}} actionable pick${{dayPicks.length === 1 ? "" : "s"}}</div>
          </div>
          <div class="report-stat">
            <div class="report-label">Units (1u flat)</div>
            <div class="report-val ${{unitsCls === "flat" ? "" : unitsCls}}">${{dayUnits >= 0 ? "+" : ""}}${{dayUnits.toFixed(2)}}u</div>
            <div class="report-sub">at slate-time prices</div>
          </div>
          <div class="report-stat">
            <div class="report-label">Hit Rate</div>
            <div class="report-val">${{(dayHitRate * 100).toFixed(0)}}%</div>
            <div class="report-sub">on actionable picks</div>
          </div>
        </div>`;
      }} else {{
        reportCardHTML = `<div class="results-aux">No actionable picks were graded for this day (no focus-band edges with a slate-time line).</div>`;
      }}

      const auxLine = `Model accuracy across <strong>${{settled.rows.length}}</strong> starts: MAE <strong>${{mae.toFixed(2)}}</strong> · bias <strong>${{(bias >= 0 ? "+" : "") + bias.toFixed(2)}}</strong>`;

      resultsSection = `<section class="results-section">
        <h2>${{settledTitle(settled.date)}}</h2>
        ${{reportCardHTML}}
        <p class="results-aux">${{auxLine}}</p>
        <details class="twisty-wrap results-detail-wrap">
          <summary>Per-pitcher detail (${{sortedSettled.length}} start${{sortedSettled.length === 1 ? "" : "s"}})</summary>
          <div class="twisty-body">
            <div class="table-scroll"><table>
              <thead><tr>
                <th>Pitcher</th><th>Opponent</th>
                <th class="num" title="Model projection">Proj</th>
                <th class="num" title="Actual strikeouts">Actual</th>
                <th class="num" title="Actual minus projected">Off By</th>
                <th class="num" title="Sportsbook line that day">Line</th>
                <th title="What our model recommended at slate time">Our Pick</th>
                <th title="HIT/MISS shown for actionable picks; otherwise just which side won">Result</th>
              </tr></thead>
              <tbody>${{sortedSettled.map(pitcherResultRow).join("")}}</tbody>
            </table></div>
          </div>
        </details>
      </section>`;
    }} else {{
      resultsSection = `<section class="results-section"><h2>Recent Results</h2><p class="muted">No settled days yet.</p></section>`;
    }}

    const trackSection = renderTrackRecord(trackPicks, trackDays);
    const parlayTrack = target.parlayTrack || {{ all: [] }};
    const parlayTrackSection = renderParlayTrackRecord(parlayTrack, trackDays, target.bets);
    return {{ html: pitcherTabHTML(heroSection, parlaySection, slateBody, resultsSection + trackSection + parlayTrackSection, cnt), cnt }};
  }}

  function renderHitterTab(target) {{
    const slate = target.slate;
    const settled = target.settled;
    const hasRows = slate.rows.length > 0;
    const sorted = hasRows ? sortRows(slate.rows, "proj_ks") : [];
    const cnt = counts(slate.rows);

    const slateBody = hasRows
      ? sorted.map(hitterRow).join("")
      : `<tr><td colspan="12" class="empty-msg">No hitter projections yet — needs confirmed lineups (typically posted 2–3 hrs before first pitch).</td></tr>`;

    let resultsSection = "";
    if (settled.rows.length) {{
      const errs = settled.rows.map(r => f(r.error)).filter(x => x !== null);
      const mae = errs.length ? avg(errs.map(Math.abs)) : 0;
      const bias = errs.length ? avg(errs) : 0;
      const hits = settled.rows.map(r => f(r.over_hit)).filter(h => h !== null);
      const overHitCount = hits.filter(h => h >= 1).length;
      const overRate = hits.length ? overHitCount / hits.length : null;
      const sortedSettled = settled.rows.slice().sort((a, b) => {{
        return Math.abs(f(b.error) || 0) - Math.abs(f(a.error) || 0);
      }}).slice(0, 40);
      const summary = [
        `<strong>${{sortedSettled.length}}</strong> top-error hitters`,
        `MAE <strong>${{mae.toFixed(2)}}</strong>`,
        `bias <strong>${{(bias >= 0 ? "+" : "") + bias.toFixed(2)}}</strong>`,
      ];
      if (overRate !== null) {{
        summary.push(`OVER hit <strong>${{(overRate * 100).toFixed(0)}}%</strong> (${{overHitCount}}/${{hits.length}} lines)`);
      }}
      resultsSection = `<section class="results-section">
        <h2>${{settledTitle(settled.date)}}</h2>
        <p class="muted">${{summary.join(" &middot; ")}}</p>
        <div class="table-scroll"><table>
          <thead><tr>
            <th>Hitter</th><th>Team</th>
            <th class="num">Proj</th><th class="num">Actual</th>
            <th class="num">Off By</th><th class="num">Line</th>
            <th>Result</th>
          </tr></thead>
          <tbody>${{sortedSettled.map(hitterResultRow).join("")}}</tbody>
        </table></div>
      </section>`;
    }} else {{
      resultsSection = `<section class="results-section"><h2>Recent Results</h2><p class="muted">No settled hitter days yet.</p></section>`;
    }}

    return {{ html: hitterTabHTML(slateBody, resultsSection), cnt }};
  }}

  function pitcherTabHTML(heroSection, parlaySection, slateBody, resultsSection, cnt) {{
    const hiddenCount = (cnt && (cnt.noise + cnt.noline)) || 0;
    const visibleCount = (cnt && (cnt.focus + cnt.investigate)) || 0;
    // Toggle button is hidden when there are no noise/noline rows to
    // toggle — avoids a "Show 0 more" no-op control.
    const toolbar = hiddenCount
      ? `<div class="slate-toolbar">
          <span>Showing <strong>${{visibleCount}}</strong> actionable pitcher${{visibleCount === 1 ? "" : "s"}}.</span>
          <button type="button" id="noise-toggle">Show ${{hiddenCount}} noise / no-line</button>
        </div>`
      : "";
    const totalPitchers = visibleCount + hiddenCount;
    const slateCountLabel = totalPitchers
      ? `<span class="slate-table-count">${{totalPitchers}} pitcher${{totalPitchers === 1 ? "" : "s"}}</span>`
      : "";
    return `<div class="daily-decision">
      ${{heroSection}}
      ${{parlaySection}}
    </div>
    <details class="tag-help">
      <summary>What do the pick tags mean?</summary>
      <div class="legend-row">
        <span class="tag tag-focus tag-dir-over">Bet <strong>OVER</strong></span>
        <span class="tag tag-focus tag-dir-under">Bet <strong>UNDER</strong></span>
        <span>moderate edge (5%–15%) — actionable</span>
      </div>
      <div class="legend-row">
        <span class="tag tag-investigate">Verify <strong>OVER</strong>?</span>
        <span class="tag tag-investigate">Verify <strong>UNDER</strong>?</span>
        <span>extreme edge (≥ 20%) — model probably wrong</span>
      </div>
      <div class="legend-row">
        <span class="tag tag-noline">No line</span>
        <span>book hasn't posted, or game already started</span>
      </div>
    </details>
    <details class="twisty-wrap slate-table-wrap" id="slate-table-twisty">
      <summary>Browse all pitchers ${{slateCountLabel}}</summary>
      <div class="slate-table-body">
        ${{toolbar}}
        <div class="table-scroll"><table>
          <thead><tr>
            <th>Pitcher</th><th>Opponent</th>
            <th title="First pitch in Central time">Time</th>
            <th class="num" title="Model projection (v2)">Our Proj</th>
            <th class="num" title="Sportsbook over/under line">Book Line</th>
            <th class="num" title="Best OVER price across all US books">Over Odds</th>
            <th class="num" title="Best UNDER price across all US books">Under Odds</th>
            <th class="num" title="Our Poisson P(over)">Our Over %</th>
            <th class="num" title="Median no-vig P(over) across books">Book Over %</th>
            <th class="num" title="Our Over % minus Book Over %">Edge</th>
            <th title="Pick recommendation">Pick</th>
          </tr></thead>
          <tbody>${{slateBody}}</tbody>
        </table></div>
      </div>
    </details>
    ${{resultsSection}}`;
  }}

  function hitterTabHTML(slateBody, resultsSection) {{
    return `<div class="legend">
      <div class="legend-row">
        <span class="tag tag-focus tag-dir-over">Bet <strong>OVER</strong></span>
        <span class="tag tag-focus tag-dir-under">Bet <strong>UNDER</strong></span>
        <span>moderate edge (5%–15%) — actionable pick</span>
      </div>
      <div class="legend-row">
        <span class="tag tag-investigate">Verify <strong>OVER</strong>?</span>
        <span class="tag tag-investigate">Verify <strong>UNDER</strong>?</span>
        <span>extreme edge (≥ 20%) — model probably wrong</span>
      </div>
      <div class="legend-row">
        <span class="tag tag-noline">No line</span>
        <span>book hasn't posted batter K market for this player</span>
      </div>
    </div>
    <div class="table-scroll"><table>
      <thead><tr>
        <th>Hitter</th>
        <th class="num" title="Batting-order slot">Slot</th>
        <th>Team</th><th>Matchup</th>
        <th class="num">Our Proj</th>
        <th class="num">Book Line</th>
        <th class="num">Over Odds</th>
        <th class="num">Under Odds</th>
        <th class="num">Our Over %</th>
        <th class="num">Book Over %</th>
        <th class="num">Edge</th>
        <th>Pick</th>
      </tr></thead>
      <tbody>${{slateBody}}</tbody>
    </table></div>
    ${{resultsSection}}`;
  }}

  // Slate repoll tracks the last-seen CSV text so the 60s tick can
  // skip re-renders when nothing has changed.
  let _lastSlateText = "";

  async function loadAndRender(opts) {{
    const silent = opts && opts.silent;
    const btn = document.getElementById("refresh-btn");
    if (!silent) {{
      if (btn) {{ btn.disabled = true; btn.classList.add("loading"); }}
      document.body.classList.add("loading");
    }}

    const TRACK_DAYS = 14;
    try {{
      const fetches = [
        fetchTodaysCSV("pitcher_ks"),
        fetchMostRecentSettled("pitcher_ks"),
        fetchTrackRecord(TRACK_DAYS),
        // Bets index drives the per-card parlay badges. Local-only —
        // fetchBetsForPitcherTab no-ops on the public URL so badges just
        // don't render. Awaited alongside slate so badges land on first paint.
        fetchBetsForPitcherTab(),
        // Suggested-parlay performance: snapshotted server-side at slate
        // time and graded once leg outcomes settle.
        fetchParlayTrackRecord(TRACK_DAYS),
      ];
      if (SHOW_HITTERS) {{
        fetches.push(
          fetchTodaysCSV("hitter_ks"),
          fetchMostRecentSettled("hitter_ks"),
        );
      }}
      const results = await Promise.all(fetches);
      const [pSlate, pSettled, pTrack, pBets, pParlayTrack, hSlate, hSettled] = results;
      _lastSlateText = pSlate.text || "";

      const pTab = renderPitcherTab({{
        slate: pSlate, settled: pSettled,
        trackPicks: pTrack, trackDays: TRACK_DAYS,
        parlayTrack: pParlayTrack,
        bets: pBets,
      }});
      const pPanel = document.getElementById("pitcher-panel");
      if (pPanel) pPanel.innerHTML = pTab.html;
      // Wire noise toggle (and apply persisted preference) immediately
      // after the toolbar lands in the DOM. Must run before any await
      // so the initial paint already reflects the user's default.
      wireNoiseToggle();
      wireSlateTableToggle();
      const pCounts = document.getElementById("pitcher-counts");
      if (pCounts) pCounts.textContent =
        `(${{pTab.cnt.focus}} focus / ${{pTab.cnt.investigate}} verify)`;

      // Header scoreboard — model + bankroll twin panel. Reuses the
      // track-record fetch (model side) and the same /api/bets state
      // that drives parlay badges (bankroll side, Tailscale-only).
      paintScoreboard(pTrack, pBets);

      // Live K + game-status overlay — fires after the table is on
      // screen so the initial paint isn't blocked on the MLB API. Once
      // the data arrives, repaintGameTimeCells() patches each row in
      // place (no full re-render). startPitcherLivePoll then keeps it
      // ticking every 60s until every game is Final.
      stopPitcherLivePoll();
      if (pSlate.rows.length && pSlate.date) {{
        startPitcherLivePoll(pSlate.rows, pSlate.date);
      }}

      if (SHOW_HITTERS && hSlate) {{
        const hTab = renderHitterTab({{ slate: hSlate, settled: hSettled }});
        const hPanel = document.getElementById("hitter-panel");
        if (hPanel) hPanel.innerHTML = hTab.html;
        const hCounts = document.getElementById("hitter-counts");
        if (hCounts) hCounts.textContent =
          `(${{hTab.cnt.focus}} focus / ${{hTab.cnt.investigate}} verify)`;
      }}

      const stamp = new Date().toLocaleString("en-US", {{
        timeZone: "America/Chicago",
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
      }});
      const lr = document.getElementById("last-refresh");
      if (lr) lr.innerHTML = `Last fetched <strong>${{stamp}} CT</strong>`;
    }} catch (e) {{
      console.error(e);
      const lr = document.getElementById("last-refresh");
      if (lr) lr.textContent = "Refresh failed — check your connection.";
    }} finally {{
      if (!silent) {{
        document.body.classList.remove("loading");
        if (btn) {{ btn.disabled = false; btn.classList.remove("loading"); }}
      }}
    }}
  }}

  // Slate CSV repoll — re-fetches pitcher_ks_<today>.csv on a 60s tick
  // and silently triggers loadAndRender() if the text has changed.
  // Catches the mid-day lineup post (opp_lineup_json [] → populated)
  // and any in-day line/edge revisions without the user having to pull
  // to refresh. Skipped while the tab is backgrounded.
  async function slateRepollTick() {{
    if (document.hidden) return;
    if (!_lastSlateText) return;
    try {{
      const d = dateInChicago(0);
      const text = await fetchCSV(baseUrl() + `pitcher_ks_${{d}}.csv`);
      if (!text || text === _lastSlateText) return;
      await loadAndRender({{ silent: true }});
    }} catch (e) {{ /* swallow — next tick retries */ }}
  }}

  let betsLoaded = false;

  function showTab(name) {{
    document.querySelectorAll(".tab-panel").forEach(p =>
      p.classList.toggle("active", p.dataset.tab === name)
    );
    document.querySelectorAll(".segmented button").forEach(b =>
      b.classList.toggle("active", b.dataset.tab === name)
    );
    if (location.hash !== "#" + name) {{
      history.replaceState(null, "", "#" + name);
    }}
    // Lazy-load the bets ledger the first time the tab is opened.
    if (name === "bets" && !betsLoaded) {{
      betsLoaded = true;
      loadBetsTab();
    }}
  }}

  function isLocal() {{
    const h = location.hostname;
    return h === "" || h === "localhost" || h === "127.0.0.1";
  }}

  // True when bets data is reachable from this origin. Only Air's Flask
  // via Tailscale Serve (https://<host>.<tailnet>.ts.net/) qualifies.
  // Localhost dev server and the public Cloudflare URL both render as
  // public — the Bets tab is hidden so localhost previews exactly what
  // an unauthenticated visitor sees.
  function isBets() {{
    const h = location.hostname;
    return /\.ts\.net$/i.test(h);
  }}

  // Default-hide the noise + no-line rows so the eye lands on focus
  // picks first. Persist the preference via localStorage so a power
  // user who wants the full table doesn't have to click every visit.
  const _NOISE_KEY = "bets:hide-noise";
  function applyNoisePreference() {{
    let hide = true;
    try {{
      const v = localStorage.getItem(_NOISE_KEY);
      if (v === "0") hide = false;
    }} catch (e) {{ /* private mode etc. — fall back to default */ }}
    document.body.classList.toggle("hide-noise", hide);
  }}
  // ──── Header scoreboard ────────────────────────────────────────────
  // Twin "Model" + "Bankroll" panel rendered into the right half of
  // the header. Reuses data already fetched for other sections (track
  // record + /api/bets), so no extra network calls.
  function renderScoreboardSparkline(cum, sign, dates, dailies, kind) {{
    if (!cum || cum.length < 2) return "";
    const w = 220, h = 28, padX = 2, padY = 3;
    const maxV = Math.max(0, ...cum);
    const minV = Math.min(0, ...cum);
    const range = (maxV - minV) || 1;
    const step = (w - padX * 2) / (cum.length - 1);
    const yFor = v => h - padY - ((v - minV) / range) * (h - padY * 2);
    const xFor = i => padX + i * step;
    const pathD = cum.map((v, i) =>
      `${{i === 0 ? "M" : "L"}}${{xFor(i).toFixed(1)}},${{yFor(v).toFixed(1)}}`
    ).join(" ");
    const zeroY = yFor(0).toFixed(1);
    const lastX = xFor(cum.length - 1).toFixed(1);
    const lastY = yFor(cum[cum.length - 1]).toFixed(1);
    const areaD = `${{pathD}} L${{lastX}},${{zeroY}} L${{xFor(0).toFixed(1)}},${{zeroY}} Z`;
    // Hover state: cursor line + snap-to-data dot embedded in the SVG;
    // tooltip div is a sibling. The delegated mousemove handler reads
    // geometry off the data attrs and toggles visibility.
    const sparkData = JSON.stringify({{
      cum: cum.map(v => +v.toFixed(4)),
      daily: (dailies || []).map(v => +v.toFixed(4)),
      dates: dates || [],
      kind: kind || "model",
      w, h, padX, padY, minV, range, step,
    }});
    return `<div class="scoreboard-spark-wrap" data-spark='${{escapeHTML(sparkData)}}'>
      <svg class="scoreboard-spark" viewBox="0 0 ${{w}} ${{h}}" preserveAspectRatio="none">
        <rect class="scoreboard-spark-hit" x="0" y="0" width="${{w}}" height="${{h}}" fill="transparent" />
        <path class="scoreboard-spark-area ${{sign}}" d="${{areaD}}" />
        <path class="scoreboard-spark-path ${{sign}}" d="${{pathD}}" vector-effect="non-scaling-stroke" />
        <path class="scoreboard-spark-tip ${{sign}}" d="M${{lastX}},${{lastY}} L${{lastX}},${{lastY}}" vector-effect="non-scaling-stroke" />
        <line class="scoreboard-spark-cursor" x1="0" x2="0" y1="0" y2="${{h}}" style="display:none;" />
        <path class="scoreboard-spark-hover-dot ${{sign}}" d="M0,0 L0,0" vector-effect="non-scaling-stroke" style="display:none;" />
      </svg>
      <div class="scoreboard-spark-readout" style="display:none;"></div>
    </div>`;
  }}

  // Compact day-by-day heatmap that sits under each scoreboard sparkline.
  // byInfo maps "YYYY-MM-DD" → record:
  //   model:  {{ units, picks, hits }}
  //   money:  {{ pnl, count, wins, staked }}
  // Each cell carries data attrs that the hover handler reads to render
  // a tooltip identical in spirit to the body P&L heatmap. The bonus
  // tooltip element is emitted as a sibling so hover positioning stays
  // local to the heat strip.
  function renderScoreboardHeat(byInfo, days, kind) {{
    const dates = [];
    for (let i = days - 1; i >= 0; i--) dates.push(dateInChicago(-i));
    const isMoney = kind === "money";
    const valOf = u => isMoney ? u.pnl : u.units;
    const present = dates.map(d => byInfo[d]).filter(u => u);
    const maxAbs = present.length
      ? Math.max(0.01, ...present.map(u => Math.abs(valOf(u))))
      : 0.01;
    const cells = dates.map(d => {{
      const u = byInfo[d];
      const moneyAttr = ` data-heat-money="${{isMoney ? 1 : 0}}"`;
      if (!u) {{
        return `<div class="scoreboard-heat-cell flat"
          data-heat-date="${{escapeHTML(d)}}" data-heat-empty="1"${{moneyAttr}}></div>`;
      }}
      const v = valOf(u);
      const sign = v > 0 ? "pos" : v < 0 ? "neg" : "flat";
      const intensity = v === 0 ? 0.30 : Math.max(0.30, Math.abs(v) / maxAbs);
      if (isMoney) {{
        const losses = u.count - u.wins;
        return `<div class="scoreboard-heat-cell ${{sign}}" style="--cell-i: ${{intensity.toFixed(2)}};"
          data-heat-date="${{escapeHTML(d)}}"${{moneyAttr}}
          data-heat-pnl="${{u.pnl.toFixed(2)}}"
          data-heat-wins="${{u.wins}}"
          data-heat-losses="${{losses}}"
          data-heat-count="${{u.count}}"
          data-heat-staked="${{(u.staked || 0).toFixed(2)}}"></div>`;
      }}
      const losses = u.picks - u.hits;
      return `<div class="scoreboard-heat-cell ${{sign}}" style="--cell-i: ${{intensity.toFixed(2)}};"
        data-heat-date="${{escapeHTML(d)}}"${{moneyAttr}}
        data-heat-units="${{u.units.toFixed(2)}}"
        data-heat-hits="${{u.hits}}"
        data-heat-losses="${{losses}}"
        data-heat-picks="${{u.picks}}"></div>`;
    }}).join("");
    return `<div class="scoreboard-heat">${{cells}}<div class="scoreboard-heat-tip" style="display:none;"></div></div>`;
  }}

  function renderScoreboardModelCol(track) {{
    const picks = (track && track.focus) ? track.focus : [];
    const labelHTML = `<div class="scoreboard-label">Model <span class="scoreboard-window">14d</span></div>`;
    if (picks.length < 3) {{
      const have = picks.length;
      return `<div class="scoreboard-col">
        ${{labelHTML}}
        <div class="scoreboard-empty">Building track record · ${{have}} pick${{have === 1 ? "" : "s"}}. Hero stats activate at 3+.</div>
      </div>`;
    }}
    const total = picks.length;
    const hits = picks.filter(p => p.won).length;
    const hitPct = (hits / total) * 100;
    const units = picks.reduce((s, p) => s + p.pnl, 0);
    const sign = units > 0.05 ? "pos" : units < -0.05 ? "neg" : "flat";
    const heroVal = `${{units >= 0 ? "+" : ""}}${{units.toFixed(2)}}u`;

    // Trend: recent half vs prior half (units delta), only with ≥8 picks.
    let trendHTML = "";
    if (picks.length >= 8) {{
      const sorted = picks.slice().sort((a, b) => a.date.localeCompare(b.date));
      const mid = Math.floor(sorted.length / 2);
      const priorU = sorted.slice(0, mid).reduce((s, p) => s + p.pnl, 0);
      const recentU = sorted.slice(mid).reduce((s, p) => s + p.pnl, 0);
      const delta = recentU - priorU;
      const tCls = delta > 0.05 ? "pos" : delta < -0.05 ? "neg" : "flat";
      const tArrow = tCls === "pos" ? "▲" : tCls === "neg" ? "▼" : "·";
      const tStr = `${{delta >= 0 ? "+" : ""}}${{delta.toFixed(2)}}u`;
      trendHTML = `<span class="scoreboard-trend ${{tCls}}" title="Recent half vs prior half">${{tArrow}} ${{tStr}}</span>`;
    }}

    // Per-day rollup feeds both the sparkline (cumulative units) and
    // the heatmap below it (daily units + W-L counts for tooltips).
    // Sparkline walks all 14 calendar days so its x-axis lines up with
    // the heatmap cells directly below — empty days contribute 0.
    const byInfo = {{}};
    for (const p of picks) {{
      const d = p.date;
      if (!byInfo[d]) byInfo[d] = {{ units: 0, picks: 0, hits: 0 }};
      byInfo[d].units += p.pnl;
      byInfo[d].picks += 1;
      if (p.won) byInfo[d].hits += 1;
    }}
    const cum = [];
    const sparkDates = [];
    const sparkDailies = [];
    let running = 0;
    for (let i = 13; i >= 0; i--) {{
      const d = dateInChicago(-i);
      const v = byInfo[d] ? byInfo[d].units : 0;
      running += v;
      cum.push(running);
      sparkDates.push(d);
      sparkDailies.push(v);
    }}
    const sparkHTML = renderScoreboardSparkline(cum, sign, sparkDates, sparkDailies, "model");
    const heatHTML = renderScoreboardHeat(byInfo, 14, "model");

    return `<div class="scoreboard-col">
      ${{labelHTML}}
      <div class="scoreboard-hero ${{sign}}">${{heroVal}}</div>
      ${{sparkHTML}}
      ${{heatHTML}}
      <div class="scoreboard-supporting">
        <span><strong>${{hitPct.toFixed(0)}}%</strong> hit</span>
        <span>· <strong>${{total}}</strong> pick${{total === 1 ? "" : "s"}}</span>
        ${{trendHTML}}
      </div>
    </div>`;
  }}

  function renderScoreboardBankrollCol(betsState) {{
    const labelHTML = `<div class="scoreboard-label">Bankroll <span class="scoreboard-window">14d</span></div>`;
    // Container is bets-only — hides on the public URL via global CSS.
    if (!betsState || !Array.isArray(betsState.bets)) {{
      return `<div class="scoreboard-col bets-only">
        ${{labelHTML}}
        <div class="scoreboard-empty">No bets data available on this URL.</div>
      </div>`;
    }}
    const cutoff = dateInChicago(-13);
    const recentSettled = betsState.bets.filter(b =>
      (b.result === "W" || b.result === "L") && b.date && b.date >= cutoff
    );
    if (!recentSettled.length) {{
      return `<div class="scoreboard-col bets-only">
        ${{labelHTML}}
        <div class="scoreboard-empty">Awaiting first settled bet in this 14-day window.</div>
      </div>`;
    }}

    let net = 0;
    let wins = 0;
    for (const b of recentSettled) {{
      const stake = parseFloat(b.stake) || 0;
      const payout = parseFloat(b.payout) || 0;
      const isFree = !!b.free_entry;
      if (b.result === "W") {{
        net += payout - (isFree ? 0 : stake);
        wins += 1;
      }} else if (!isFree) {{
        net -= stake;
      }}
    }}
    const hitPct = (wins / recentSettled.length) * 100;
    const sign = net > 0.5 ? "pos" : net < -0.5 ? "neg" : "flat";
    const heroVal = `${{net >= 0 ? "+" : "−"}}$${{Math.abs(net).toFixed(0)}}`;

    // Trend: recent half vs prior half ($ delta), only with ≥8 bets.
    let trendHTML = "";
    if (recentSettled.length >= 8) {{
      const sorted = recentSettled.slice().sort((a, b) => a.date.localeCompare(b.date));
      const mid = Math.floor(sorted.length / 2);
      const halfNet = arr => arr.reduce((s, b) => {{
        const stake = parseFloat(b.stake) || 0;
        const payout = parseFloat(b.payout) || 0;
        const isFree = !!b.free_entry;
        if (b.result === "W") return s + (payout - (isFree ? 0 : stake));
        return s - (isFree ? 0 : stake);
      }}, 0);
      const priorN = halfNet(sorted.slice(0, mid));
      const recentN = halfNet(sorted.slice(mid));
      const delta = recentN - priorN;
      const tCls = delta > 0.5 ? "pos" : delta < -0.5 ? "neg" : "flat";
      const tArrow = tCls === "pos" ? "▲" : tCls === "neg" ? "▼" : "·";
      const tStr = `${{delta >= 0 ? "+" : "−"}}$${{Math.abs(delta).toFixed(0)}}`;
      trendHTML = `<span class="scoreboard-trend ${{tCls}}" title="Recent half vs prior half">${{tArrow}} ${{tStr}}</span>`;
    }}

    // Cumulative $ P&L over the last 14 days (one point per day, even
    // empty days, so the curve reads like an equity curve). Also keep
    // the full per-day record (pnl, wins, count, staked) keyed by date
    // so the heatmap tooltip can show the same detail as the body
    // calendar used to.
    const dailyPnl = computeDailyBetsPnl(recentSettled);
    const byInfo = {{}};
    for (const u of dailyPnl) byInfo[u.date] = u;
    const cum = [];
    const sparkDates = [];
    const sparkDailies = [];
    let running = 0;
    for (let i = 13; i >= 0; i--) {{
      const d = dateInChicago(-i);
      const v = byInfo[d] ? byInfo[d].pnl : 0;
      running += v;
      cum.push(running);
      sparkDates.push(d);
      sparkDailies.push(v);
    }}
    const sparkHTML = renderScoreboardSparkline(cum, sign, sparkDates, sparkDailies, "money");
    const heatHTML = renderScoreboardHeat(byInfo, 14, "money");

    return `<div class="scoreboard-col bets-only">
      ${{labelHTML}}
      <div class="scoreboard-hero ${{sign}}">${{heroVal}}</div>
      ${{sparkHTML}}
      ${{heatHTML}}
      <div class="scoreboard-supporting">
        <span><strong>${{hitPct.toFixed(0)}}%</strong> hit</span>
        <span>· <strong>${{recentSettled.length}}</strong> bet${{recentSettled.length === 1 ? "" : "s"}}</span>
        ${{trendHTML}}
      </div>
    </div>`;
  }}

  function paintScoreboard(track, betsState) {{
    const root = document.getElementById("header-scoreboard");
    if (!root) return;
    root.innerHTML = renderScoreboardModelCol(track) + renderScoreboardBankrollCol(betsState);
    // No JS timing needed — the .scoreboard-spark-path / -tip elements
    // carry CSS @keyframes that fire the moment the SVG renders.
  }}

  // Persist the "Browse all pitchers" twisty open/closed across reloads.
  // Default-collapsed; once a user opens it, it stays open.
  const _SLATE_TABLE_KEY = "slate-table-open";
  function wireSlateTableToggle() {{
    const t = document.getElementById("slate-table-twisty");
    if (!t) return;
    try {{
      if (localStorage.getItem(_SLATE_TABLE_KEY) === "1") t.setAttribute("open", "");
    }} catch (e) {{}}
    t.addEventListener("toggle", () => {{
      try {{ localStorage.setItem(_SLATE_TABLE_KEY, t.open ? "1" : "0"); }} catch (e) {{}}
    }});
  }}

  function wireNoiseToggle() {{
    const btn = document.getElementById("noise-toggle");
    if (!btn) return;
    const updateLabel = () => {{
      const hidden = document.body.classList.contains("hide-noise");
      const n = btn.dataset.count || (btn.textContent.match(/\\d+/) || [""])[0];
      btn.dataset.count = n;
      btn.textContent = hidden
        ? `Show ${{n}} noise / no-line`
        : `Hide ${{n}} noise / no-line`;
      btn.classList.toggle("active", !hidden);
    }};
    updateLabel();
    btn.addEventListener("click", () => {{
      const willHide = !document.body.classList.contains("hide-noise");
      document.body.classList.toggle("hide-noise", willHide);
      try {{ localStorage.setItem(_NOISE_KEY, willHide ? "1" : "0"); }} catch (e) {{}}
      updateLabel();
    }});
  }}

  function updateHeaderDate() {{
    const fmt = new Intl.DateTimeFormat("en-US", {{
      timeZone: "America/Chicago",
      weekday: "long", year: "numeric", month: "long", day: "numeric"
    }});
    const el = document.getElementById("header-date");
    if (el) el.textContent = fmt.format(new Date());
  }}

  // Cross-tab handoff from a parlay-suggester card. Switches to the
  // Bets tab (lazy-loading it on first visit), waits for slatePitchers
  // to populate so the dropdown options render, then sets the leg count
  // and pre-fills the rows with the suggested legs.
  async function handleAddParlayToBets(legsJSON) {{
    let legs;
    try {{ legs = JSON.parse(legsJSON); }} catch (e) {{ return; }}
    if (!Array.isArray(legs) || legs.length < 2) return;
    showTab("bets");
    // Poll briefly until the lazy-loaded slate populates. Cap at ~3s so
    // we don't hang forever if the API is down.
    for (let i = 0; i < 60 && !slatePitchers.length; i++) {{
      await new Promise(r => setTimeout(r, 50));
    }}
    const legCountSel = document.getElementById("bf-legcount");
    const legsContainer = document.getElementById("bf-legs");
    if (!legCountSel || !legsContainer) return;
    legCountSel.value = String(legs.length);
    legsContainer.innerHTML = renderLegInputs(legs.length, legs);
    recomputeCombined();
    const form = document.getElementById("bets-form");
    if (form) form.scrollIntoView({{ behavior: "smooth", block: "center" }});
  }}

  // Reads output/odds_api_usage.json (written by bets/odds.py after each
  // API call) and renders the header pill. Same baseUrl() pattern as the
  // CSV fetches: localhost reads via Flask static, public URL reads via
  // raw GitHub. Quietly hides the pill if the file is missing or stale.
  async function loadOddsQuota() {{
    const pill = document.getElementById("quota-pill");
    if (!pill) return;
    try {{
      const r = await fetch(baseUrl() + "odds_api_usage.json", {{ cache: "no-cache" }});
      if (!r.ok) return;
      const data = await r.json();
      const used = Number(data.used);
      const cap = Number(data.cap);
      if (!Number.isFinite(used) || !Number.isFinite(cap) || cap <= 0) return;

      const todayStr = dateInChicago(0);
      const calls = Array.isArray(data.calls) ? data.calls : [];
      let todayCalls = 0;
      let todayCost = 0;
      for (const c of calls) {{
        if (!c || !c.ts) continue;
        const d = new Date(c.ts);
        if (Number.isNaN(d.getTime())) continue;
        const local = new Intl.DateTimeFormat("en-CA", {{
          timeZone: "America/Chicago",
          year: "numeric", month: "2-digit", day: "2-digit",
        }}).format(d);
        if (local === todayStr) {{
          todayCalls += 1;
          if (Number.isFinite(Number(c.cost))) todayCost += Number(c.cost);
        }}
      }}

      const pct = used / cap;
      pill.classList.remove("warn", "danger");
      if (pct >= 0.9) pill.classList.add("danger");
      else if (pct >= 0.75) pill.classList.add("warn");

      const todaySegment = todayCalls
        ? ` <span class="quota-today">· +${{todayCost || todayCalls}} today</span>`
        : "";
      pill.innerHTML = `Odds API: <strong>${{used}}/${{cap}}</strong>${{todaySegment}}`;

      const updated = data.last_updated
        ? new Date(data.last_updated).toLocaleString("en-US", {{
            timeZone: "America/Chicago",
            month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
          }})
        : "unknown";
      pill.title =
        `Odds API quota\n` +
        `Used: ${{used}} / ${{cap}} (remaining: ${{data.remaining}})\n` +
        `Today: ${{todayCalls}} calls, ${{todayCost}} credits\n` +
        `Last updated: ${{updated}} CT`;
      pill.classList.add("visible");
    }} catch (e) {{
      console.warn("quota fetch failed", e);
    }}
  }}

  // Reads /api/health (Flask) and renders a green/yellow/red pill in
  // the actions row. Same-origin fetch, so on the public (Caddy-static)
  // deploy it 404s and the pill stays hidden — matching how Bets tab +
  // the local-only buttons scope themselves to laptop/Tailscale
  // contexts.
  // Also hidden on localhost: the watcher only runs on the Air, so the
  // laptop's /api/health always reports "Watcher hasn't run today" and
  // would just be misleading noise.
  async function loadHealth() {{
    const pill = document.getElementById("health-pill");
    if (!pill) return;
    if (isLocal()) return;
    try {{
      const r = await fetch("/api/health", {{ cache: "no-cache" }});
      if (!r.ok) return;
      const data = await r.json();
      const sources = data && data.sources ? data.sources : {{}};
      const names = Object.keys(sources);
      if (!names.length) {{
        pill.classList.remove("ok", "warn", "danger");
        pill.querySelector(".label").textContent = "Health: —";
        pill.title = "Watcher hasn't run yet today.";
        pill.classList.add("visible");
        return;
      }}

      const stale = names.filter(n => !sources[n].fresh);
      const alerted = names.filter(n => sources[n].alerted);

      pill.classList.remove("ok", "warn", "danger");
      let label;
      if (alerted.length) {{
        pill.classList.add("danger");
        label = `Alert: ${{alerted.join(" + ")}}`;
      }} else if (stale.length) {{
        pill.classList.add("warn");
        label = `Stale: ${{stale.join(" + ")}}`;
      }} else {{
        pill.classList.add("ok");
        label = "Healthy";
      }}
      pill.querySelector(".label").textContent = label;

      const lines = [];
      for (const n of names) {{
        const s = sources[n];
        const tag = s.alerted ? "ALERTED" : (s.fresh ? "fresh" : "stale");
        const retries = `${{s.retries || 0}}/${{s.retry_cap || 3}}`;
        lines.push(`${{n}}: ${{tag}} (${{s.detail || "—"}}, retries ${{retries}})`);
        if (s.last_attempt_detail) lines.push(`  last attempt: ${{s.last_attempt_detail}}`);
      }}
      const winLine = data.in_active_window
        ? "Active window (9am-9pm) — retries may fire."
        : "Off-hours — staleness recorded but retries deferred.";
      const checkedLine = data.checked_at
        ? `Last check: ${{new Date(data.checked_at).toLocaleString("en-US", {{ month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }})}}`
        : "Last check: —";
      pill.title = [checkedLine, winLine, "", ...lines].join("\\n");
      pill.classList.add("visible");
    }} catch (e) {{
      // Silent — endpoint not reachable (public static deploy has no Flask).
    }}
  }}

  // Wraps a full re-fetch + repaint. Used by visibilitychange and
  // pull-to-refresh; both call this instead of loadAndRender() directly
  // so the timestamp + side fetches (quota, health) stay in sync.
  let lastRefreshAt = 0;
  async function doSoftRefresh() {{
    lastRefreshAt = Date.now();
    try {{
      await loadAndRender();
    }} finally {{
      loadOddsQuota();
      loadHealth();
    }}
  }}

  // #1 — auto-refresh when the PWA returns to foreground. Skips if a
  // refresh just ran (debounce 30s) so rapid app-switching doesn't
  // hammer the network.
  function setupVisibilityRefresh() {{
    document.addEventListener("visibilitychange", () => {{
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastRefreshAt < 30000) return;
      doSoftRefresh();
    }});
  }}

  // #2 — custom pull-to-refresh, gated on PWA standalone mode (Safari
  // in-browser still has the native overscroll PTR; injecting ours
  // there would double-trigger). Threshold 70px, 0.5x damping so the
  // indicator feels weighty rather than rubbery.
  function setupPullToRefresh() {{
    const standalone = window.matchMedia("(display-mode: standalone)").matches
      || window.navigator.standalone === true;
    if (!standalone) return;

    const indicator = document.getElementById("ptr-indicator");
    if (!indicator) return;

    const THRESHOLD = 70;
    const DAMPING = 0.5;
    let startY = 0;
    let pulling = false;
    let pullDistance = 0;
    let refreshing = false;

    function reset() {{
      indicator.classList.remove("dragging", "ready");
      indicator.style.transform = "";
      indicator.style.opacity = "";
      pullDistance = 0;
    }}

    document.addEventListener("touchstart", (e) => {{
      if (refreshing) return;
      if (e.touches.length !== 1) return;
      // Only intercept when the page is at the top — otherwise let
      // native scroll work. window.scrollY is the cross-browser way
      // to read the document scroll offset.
      if ((window.scrollY || document.documentElement.scrollTop || 0) > 0) return;
      startY = e.touches[0].clientY;
      pulling = true;
      pullDistance = 0;
      indicator.classList.add("dragging");
    }}, {{ passive: true }});

    document.addEventListener("touchmove", (e) => {{
      if (!pulling || refreshing) return;
      const dy = e.touches[0].clientY - startY;
      if (dy <= 0) {{
        // User swiped up — abort the pull and let native scroll resume.
        pulling = false;
        reset();
        return;
      }}
      pullDistance = dy * DAMPING;
      indicator.style.transform = `translateY(${{Math.min(pullDistance, 100)}}px)`;
      indicator.style.opacity = String(Math.min(pullDistance / 40, 1));
      indicator.classList.toggle("ready", pullDistance >= THRESHOLD);
    }}, {{ passive: true }});

    document.addEventListener("touchend", () => {{
      if (!pulling) return;
      pulling = false;
      indicator.classList.remove("dragging");
      if (pullDistance >= THRESHOLD) {{
        refreshing = true;
        indicator.classList.add("refreshing");
        indicator.classList.remove("ready");
        indicator.style.transform = "translateY(40px)";
        indicator.style.opacity = "1";
        doSoftRefresh().finally(() => {{
          refreshing = false;
          indicator.classList.remove("refreshing");
          reset();
        }});
      }} else {{
        reset();
      }}
    }}, {{ passive: true }});

    document.addEventListener("touchcancel", () => {{
      if (!pulling) return;
      pulling = false;
      reset();
    }}, {{ passive: true }});
  }}

  document.addEventListener("DOMContentLoaded", () => {{
    updateHeaderDate();
    applyNoisePreference();

    // Single delegated handler for tapping a parlay-suggester card.
    // The whole card is the click target — gated to the Bets URL so
    // the form handoff doesn't run on public, where there's nowhere to
    // hand off to. Document-level delegation survives every re-render.
    document.addEventListener("click", (e) => {{
      const card = e.target.closest(".parlay-card[data-legs]");
      if (!card || !isBets()) return;
      handleAddParlayToBets(card.dataset.legs || "[]");
    }});

    document.querySelectorAll(".segmented button").forEach(b => {{
      b.addEventListener("click", () => showTab(b.dataset.tab));
    }});
    const allowed = ["pitchers"];
    if (SHOW_HITTERS) allowed.push("hitters");
    if (isBets()) allowed.push("bets");
    const initial = (location.hash || "#pitchers").slice(1);
    showTab(allowed.includes(initial) ? initial : "pitchers");

    const btn = document.getElementById("refresh-btn");
    if (btn) {{
      if (isBets()) {{
        // On Air (Tailscale): run the pipeline so lineups update from MLB
        // API. The all_covered guard in bets.main skips the Odds API after
        // the morning run, so this costs 0 credits.
        btn.addEventListener("click", () => {{
          document.body.classList.add("loading");
          btn.disabled = true;
          const f = document.createElement("form");
          f.method = "POST";
          f.action = "/refresh";
          document.body.appendChild(f);
          f.submit();
        }});
      }} else {{
        btn.addEventListener("click", loadAndRender);
      }}
    }}

    // Theme toggle. Head script already applied saved theme; this
    // syncs the button label + reacts to clicks. localStorage persists
    // across reloads; the head script reads it back next visit.
    const themeBtn = document.getElementById("theme-toggle");
    const metaTheme = document.querySelector('meta[name="theme-color"]');
    function syncThemeUI() {{
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      if (metaTheme) metaTheme.setAttribute("content", isLight ? "#ffffff" : "#0a1628");
    }}
    if (themeBtn) {{
      themeBtn.addEventListener("click", () => {{
        const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", next);
        try {{ localStorage.setItem("bets-theme", next); }} catch (e) {{}}
        syncThemeUI();
      }});
    }}
    syncThemeUI();

    loadAndRender();
    loadOddsQuota();
    loadHealth();
    lastRefreshAt = Date.now();
    setupVisibilityRefresh();
    setupPullToRefresh();
    // Watcher runs every 30 min, but a manual kickstart or a recent
    // retry can change the snapshot mid-window. 60s polling is cheap
    // (read-only file lookup) and matches the bets tab's poll cadence.
    setInterval(loadHealth, 60000);

    // Tick the gametime cells every minute so "in NN min" stays
    // accurate and a row flips to row-locked the moment first pitch
    // passes — no full re-render or refetch.
    setInterval(repaintGameTimeCells, 60000);

    // Slate CSV repoll — silent re-render when pitcher_ks_<today>.csv
    // changes. Fires the moment lineups post mid-day so Lineup TBD
    // chips disappear without a manual refresh.
    setInterval(slateRepollTick, 60000);
  }});
}})();
"""


def generate(target_date: date | None = None) -> Path | None:
    target_date = target_date or date.today()

    actions_block = _action_buttons_html()
    force_refresh = _force_refresh_status_html()
    js = _render_js()

    # Tab nav: pitchers always visible. Hitters when SHOW_HITTERS.
    # Bets is always present in the HTML but tagged local-only — the
    # CSS hides it on the public URL, the JS only allows navigation to
    # it on localhost.
    # Buttons inside the segmented nav. Visibility is gated by the
     # parent <nav class="segmented"> which is hidden everywhere except
     # the Tailscale URL (html.is-bets), so we don't need per-button
     # bets-only classes. Counts (#pitcher-counts) used to live in the
     # tab label; the JS still updates them with `if (el)` guards, so
     # dropping the span here is safe — the no-op falls through.
    pitcher_btn = '<button data-tab="pitchers" type="button">Pitchers</button>'
    hitter_btn = '<button data-tab="hitters" type="button">Hitters</button>' if SHOW_HITTERS else ""
    bets_btn = '<button data-tab="bets" type="button">Bets</button>'
    tabs_nav = "    " + "\n    ".join(b for b in (pitcher_btn, hitter_btn, bets_btn) if b)

    pitcher_panel = '<div class="tab-panel active" data-tab="pitchers" id="pitcher-panel">\n    <p class="muted">Loading…</p>\n  </div>'
    hitter_panel = '<div class="tab-panel" data-tab="hitters" id="hitter-panel">\n    <p class="muted">Loading…</p>\n  </div>' if SHOW_HITTERS else ""
    bets_panel = '<div class="tab-panel bets-only" data-tab="bets" id="bets-panel">\n    <p class="muted">Loading…</p>\n  </div>'
    panels = "  " + "\n  ".join(p for p in (pitcher_panel, hitter_panel, bets_panel) if p)

    # Note: NO date or timestamp in the shell — those are rendered client-
    # side by JS so the shell stays byte-identical across regens.
    # Otherwise every daily run would change index.html and force the
    # M1 Air to do an unnecessary `git pull` cycle.
    # Synchronous head script: tags <html> with visibility classes before
    # first paint so .local-only / .bets-only stay hidden on the public URL
    # and reveal cleanly on the right contexts (no flash).
    #   is-local → laptop's Flask (refresh/settle/push buttons)
    #   is-bets  → Air's Flask via Tailscale Serve (bets tab + add-to-bets)
    # Mirrored in baseUrl() / isBets() / isLocal() in the JS.
    # Theme init runs in the same synchronous block so the saved choice
    # is applied to <html data-theme="..."> before first paint, avoiding
    # a flash of the wrong palette on each load.
    local_check = (
        "(function(){var h=location.hostname;var d=document.documentElement;"
        "if(h===''||h==='localhost'||h==='127.0.0.1')d.classList.add('is-local');"
        "if(/\\.ts\\.net$/i.test(h))d.classList.add('is-bets');"
        "try{var t=localStorage.getItem('bets-theme');"
        "if(t==='light'||t==='dark')d.setAttribute('data-theme',t);}catch(e){}"
        "})();"
    )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>K-Edge</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230a1628'/%3E%3Ctext x='16' y='24' text-anchor='middle' font-family='system-ui,sans-serif' font-weight='900' font-size='22' fill='%235dfa7a'%3EK%3C/text%3E%3C/svg%3E">
<!-- PWA: install via Safari → Share → "Add to Home Screen". -->
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0a1628">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="K-Edge">
<script>{local_check}</script>
<style>{CSS}</style>
<script>{js}</script>
</head>
<body>
<div id="ptr-indicator" class="ptr-indicator" aria-hidden="true">
  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
    <path d="M21 12a9 9 0 1 1-3.5-7.1"/>
    <polyline points="21 4 21 9 16 9"/>
  </svg>
</div>
<header>
  <div class="brand-area">
    <h1 class="brand"><img src="/k-edge-logo.png" alt="K-Edge — MLB Strikeout Intelligence" class="brand-logo brand-logo-dark"><img src="/k-edge-logo-light.png" alt="K-Edge — MLB Strikeout Intelligence" class="brand-logo brand-logo-light"></h1>
    <div class="brand-actions">
      <nav class="segmented" role="tablist">
{tabs_nav}
      </nav>
      {actions_block}
    </div>
  </div>
  <div class="header-right">
    <aside class="header-scoreboard" id="header-scoreboard" aria-label="Performance scoreboard"></aside>
    <div class="status-row">
      <span class="last-refresh" id="last-refresh"></span>
      <span class="quota-pill" id="quota-pill" title=""></span>
      <span class="health-pill" id="health-pill" title=""><span class="dot"></span><span class="label">Health</span></span>
      {force_refresh}
    </div>
  </div>
</header>
<main>
{panels}
</main>
<footer>
  Data fetched live from {REPO}/output on each load &middot;
  <a href="https://github.com/{REPO}" style="color: var(--muted);">source</a>
</footer>
</body>
</html>
"""

    out_path = OUTPUT_DIR / "index.html"
    out_path.write_text(doc)
    print(f"Wrote dashboard shell → {out_path}")
    return out_path


def main() -> None:
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        target = date.today()
    generate(target)


if __name__ == "__main__":
    main()
