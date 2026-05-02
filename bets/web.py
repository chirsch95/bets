"""Static HTML dashboard generator (client-side rendered).

Generates a thin HTML shell with embedded JavaScript. The JS fetches CSV
files at page-load:
  - On localhost / 127.0.0.1: from same origin (`./...csv`) — Flask
    server serves them from output/.
  - On any other host (Netlify): from
    `https://raw.githubusercontent.com/chirsch95/bets/main/output/...csv`.

Rendering happens client-side, so committing new CSV data does NOT require
a Netlify redeploy — saves build credits. Netlify only re-deploys when the
shell itself (this generated index.html) changes, governed by the
`netlify.toml` ignore rule.

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

# Edge bands. Mirror values used by the JS classifier — keep in sync.
FOCUS_EDGE_MIN = 0.05
FOCUS_EDGE_MAX = 0.15
INVESTIGATE_EDGE = 0.20


CSS = """
  :root {
    --bg: #0e1015;
    --panel: #161922;
    --text: #e6e8eb;
    --muted: #8a93a3;
    --border: #232734;
    --green: #4ade80;
    --green-bg: rgba(74, 222, 128, 0.1);
    --green-solid: #15803d;
    --red: #f87171;
    --red-bg: rgba(248, 113, 113, 0.1);
    --red-solid: #b91c1c;
    --yellow: #fbbf24;
    --yellow-bg: rgba(251, 191, 36, 0.1);
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
    padding: 24px 32px 0;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }
  header h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
  header .date { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
  .actions { display: flex; gap: 8px; padding-top: 4px; align-items: center; flex-wrap: wrap; }
  .actions form { margin: 0; }
  .actions button {
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    font-family: inherit;
  }
  .actions button:hover { background: rgba(255,255,255,0.04); }
  .actions button.primary {
    background: var(--green);
    color: #001a00;
    border-color: var(--green);
  }
  .actions button.primary:hover { filter: brightness(1.08); }
  .actions button:disabled { opacity: 0.5; cursor: wait; }
  body.loading { cursor: progress; }
  body.loading .actions button { opacity: 0.5; cursor: wait; }
  /* Hidden by default; revealed only on localhost (see head script). The
     Re-run/Settle buttons POST to Flask routes that exist only locally. */
  .local-only { display: none; }
  html.is-local .local-only { display: revert; }
  .last-refresh { color: var(--muted); font-size: 12px; margin-left: 8px; }
  .last-refresh strong { color: var(--text); font-weight: 500; }
  .tabs {
    display: flex;
    gap: 4px;
    width: 100%;
    border-bottom: 1px solid var(--border);
    margin-top: 12px;
  }
  .tabs button {
    background: transparent;
    color: var(--muted);
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    font-family: inherit;
    margin-bottom: -1px;
  }
  .tabs button:hover { color: var(--text); }
  .tabs button.active {
    color: var(--text);
    border-bottom-color: var(--green);
  }
  .tabs button .count {
    color: var(--muted);
    font-weight: 400;
    margin-left: 4px;
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
    margin-top: 8px;
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
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 10px;
  }
  .pick-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
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
  .pick-card-pitcher { font-size: 15px; font-weight: 600; margin-bottom: 1px; }
  .pick-card-matchup { font-size: 11px; color: var(--muted); margin-bottom: 7px; }
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
  .pick-card.hit .pick-card-matchup,
  .pick-card.miss .pick-card-matchup,
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
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 10px;
  }
  .parlay-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--muted);
    border-radius: 8px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .parlay-card.pos { border-left-color: var(--green); }
  .parlay-card.neg { border-left-color: var(--red); }
  .parlay-legs { display: flex; flex-direction: column; gap: 4px; }
  .parlay-leg {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: baseline;
    gap: 8px;
    font-size: 13px;
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
  .parlay-card-actions { display: flex; justify-content: flex-end; }
  .parlay-add-btn {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 4px 10px;
    font: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .parlay-add-btn:hover { color: var(--green); border-color: var(--green); }
  /* Live "Combined" panel inside the Bets-tab parlay editor — recomputes
     on every leg change. Mirrors the suggester card stats so the math
     shows up identically when you arrive via "+ Add to bets". */
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
  /* Bets tab — local-only personal ledger. */
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
  table.bets-ledger td.result.W { color: var(--green); font-weight: 600; }
  table.bets-ledger td.result.L { color: var(--red); font-weight: 600; }
  table.bets-ledger td.result.pending { color: var(--muted); }
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
  /* Parlay form (Phase 1) — leg-count selector + N leg rows. */
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
  .bets-form-bottom {
    display: grid;
    grid-template-columns: 100px 100px 1fr;
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
    grid-template-columns: 24px 1fr 60px;
    gap: 8px;
    padding: 2px 0;
  }
  .parlay-leg-ou.over { color: var(--green); font-weight: 600; }
  .parlay-leg-ou.under { color: var(--red); font-weight: 600; }
  .parlay-leg-name { color: var(--text); }
  tr.parlay-detail td { padding-top: 0; padding-bottom: 8px; background: rgba(0,0,0,0.15); }
  tr.parlay-detail.hidden { display: none; }
  tr.parlay-row { cursor: pointer; }
  tr.parlay-row:hover td { background: rgba(255, 255, 255, 0.02); }
  tr.parlay-row.expanded td { background: rgba(74, 222, 128, 0.04); }
  /* Phase 2: Pitcher picker (select + custom-text fallback). */
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
  /* Phase 3: live K display in expanded parlay detail. */
  .parlay-leg-list li {
    grid-template-columns: 24px 1fr 80px 1fr;
  }
  .parlay-rollup {
    margin: 6px 0 10px;
    padding: 8px 12px;
    border-radius: 6px;
    border: 1px solid var(--border);
    font-size: 12px;
    color: var(--muted);
    display: flex;
    gap: 14px;
    align-items: center;
    flex-wrap: wrap;
  }
  .parlay-rollup.win { background: rgba(74, 222, 128, 0.10); border-color: var(--green); }
  .parlay-rollup.loss { background: rgba(248, 113, 113, 0.10); border-color: var(--red); }
  .parlay-rollup.pending { background: rgba(251, 191, 36, 0.06); border-color: var(--yellow); }
  .parlay-rollup-verdict {
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .parlay-rollup.win .parlay-rollup-verdict { color: var(--green); }
  .parlay-rollup.loss .parlay-rollup-verdict { color: var(--red); }
  .parlay-rollup.pending .parlay-rollup-verdict { color: var(--yellow); }
  .parlay-rollup-counts { color: var(--text); }
  .parlay-rollup-mismatch {
    margin-left: auto;
    color: var(--red);
    font-weight: 600;
    font-size: 11px;
  }
  .live-status {
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--muted);
  }
  .live-status .live-ks { font-weight: 700; color: var(--text); margin-right: 6px; }
  .live-status.hit .live-ks { color: var(--green); }
  .live-status.miss .live-ks { color: var(--red); }
  .live-status .live-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 700;
  }
  .live-status.hit .live-badge { background: var(--green); color: #001a00; }
  .live-status.miss .live-badge { background: var(--red); color: #2a0000; }
  .live-status.live .live-badge { background: var(--yellow); color: #2a1f00; }
  .live-status.preview .live-badge {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
  }
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
  main { padding: 24px 32px; max-width: 1280px; }
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
    background: rgba(255,255,255,0.02);
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
"""


def _action_buttons_html() -> str:
    """All three buttons always present in the HTML. The two POST forms
    target Flask routes that only exist on the local server, so they're
    wrapped in `.local-only` (see CSS + head script — revealed only when
    the page is loaded from localhost). Same HTML works on Netlify and
    locally without environment-dependent generation."""
    return """<div class="actions">
    <button type="button" id="refresh-btn" class="primary">Refresh data</button>
    <span class="last-refresh" id="last-refresh"></span>
    <form class="local-only" action="/refresh" method="post" onsubmit="document.body.classList.add('loading');">
      <button type="submit">Re-run pipeline</button>
    </form>
    <form class="local-only" action="/settle" method="post" onsubmit="document.body.classList.add('loading');">
      <button type="submit">Settle yesterday</button>
    </form>
  </div>"""


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
  const RAW_BASE = "{raw_base}";
  const SHOW_HITTERS = {show_hitters_js};

  function baseUrl() {{
    const h = location.hostname;
    if (h === "localhost" || h === "127.0.0.1" || h === "") return "./";
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
    const r = await fetch(url, {{ cache: "no-cache" }});
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

  function classify(edge) {{
    if (edge === null) return "noline";
    const a = Math.abs(edge);
    if (a >= INVESTIGATE) return "investigate";
    if (a >= FOCUS_MIN && a <= FOCUS_MAX) return "focus";
    return "noise";
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

  // Parlay math — independent-leg approximation. Two pitchers in the
  // same game face different lineups (their opponent's), so independence
  // is a fine assumption for K props.
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
    const edge = f(r.edge);
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
    return {{
      pitcher: r.pitcher || "",
      pitcher_id: isNaN(pidNum) ? null : pidNum,
      line: r.line,
      dir,
      odds,
      decOdds: dec,
      hitProb,
      novigP,
      edge,
      gameTimeISO: r.game_datetime_utc || "",
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
    if (diffMin < 60) rel = `in ${{diffMin}}m`;
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
  // the public MLB Stats API. No auth, no proxy needed — works on
  // Netlify and locally. Returns Map<pitcher_id, liveData>.
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
        status: status.status,
        detailed: status.detailed || "",
        current_inning: status.current_inning,
        inning_state: status.inning_state,
      }};
      const box = boxByGpk.get(gpk);
      if (box) {{
        const key = `ID${{pid}}`;
        for (const side of ["home", "away"]) {{
          const players = (box.teams && box.teams[side] && box.teams[side].players) || {{}};
          if (players[key]) {{
            const stats = players[key].stats && players[key].stats.pitching;
            const ks = stats && stats.strikeOuts;
            if (ks !== undefined && ks !== null && ks !== "") {{
              const n = parseInt(ks, 10);
              if (!isNaN(n)) result.ks = n;
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
      // Outcome flips the card to its solid HIT/MISS fill + shows the
      // top banner. Toggle both classes off first so we never end up
      // with both.
      card.classList.remove("hit", "miss");
      if (cell.outcome) card.classList.add(cell.outcome);
      const chip = card.querySelector("[data-outcome-chip]");
      if (chip) {{
        if (cell.outcome) {{
          chip.className = `pick-card-banner ${{cell.outcome}}`;
          chip.textContent = cell.outcome.toUpperCase();
          chip.style.display = "";
        }} else {{
          chip.className = "pick-card-banner";
          chip.style.display = "none";
          chip.textContent = "";
        }}
      }}
    }});
  }}

  function sortKey(r, projField) {{
    const edge = f(r.edge);
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

  function pitcherRow(r) {{
    const edge = f(r.edge);
    const cls = classify(edge);
    const dir = edge === null || edge === 0 ? "" : (edge > 0 ? "over" : "under");
    const edgeStr = edge === null ? "—" : (edge > 0 ? "+" : "") + edge.toFixed(3);
    const proj = r.proj_ks_v2 || r.proj_ks_v1 || "";
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
      <td class="player">${{escapeHTML(r.pitcher || "")}}</td>
      <td>${{oppPrefix(r)}}${{escapeHTML(r.opp || "")}}</td>
      <td class="gametime"${{isoAttr}}${{pidAttr}}>${{cell.html}}</td>
      <td class="num">${{dash(proj)}}</td>
      <td class="num">${{dash(r.line)}}</td>
      <td class="num"${{overTitle}}>${{dash(r.over_odds)}}</td>
      <td class="num"${{underTitle}}>${{dash(r.under_odds)}}</td>
      <td class="num">${{dash(r.p_over)}}</td>
      <td class="num"${{novigTitle}}>${{dash(r.novig_over)}}</td>
      <td class="num edge ${{dir}}">${{edgeStr}}</td>
      <td class="badge"><span class="tag ${{tagCls}}">${{label(cls, dir)}}</span></td>
    </tr>`;
  }}

  // Outcome of a card's pick given current K count + line + game state.
  // Mirrors legHitState() on the bets tab. Returns "hit" | "miss" | null.
  // dir is "over" | "under" (the model's recommendation for this card).
  function pickOutcome(ks, line, dir, status) {{
    if (ks === null || ks === undefined) return null;
    const lineNum = parseFloat(line);
    if (isNaN(lineNum)) return null;
    if (ks > lineNum) return dir === "over" ? "hit" : "miss";
    if (status === "Final") return dir === "under" ? "hit" : "miss";
    return null;
  }}

  // Live cell content for a hero card. Mirrors the time-cell logic in
  // renderGameTimeCell but shorter — the matchup line above already
  // shows the scheduled clock time, so the live cell just adds the
  // status delta ("in 47m", "● B5 4K", "Final · 7K"). Returns
  // {{html, label, cls, locked, outcome}} where outcome is "hit"/"miss"/null.
  function renderHeroLive(iso, live, line, dir) {{
    const lineNum = parseFloat(line);
    const outcome = live ? pickOutcome(live.ks, line, dir, live.status) : null;
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
  function renderHeroPickCard(r) {{
    const edge = f(r.edge);
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
      ? `<div class="pick-card-banner ${{liveCell.outcome}}" data-outcome-chip>${{liveCell.outcome.toUpperCase()}}</div>`
      : `<div class="pick-card-banner" data-outcome-chip style="display:none;"></div>`;
    return `<div class="${{cardCls}}"${{isoAttr}}${{pidAttr}}${{lineAttr}}${{dirAttr}}>
      ${{banner}}
      <div class="pick-card-header">
        <span class="pick-card-badge ${{dir}}">BET ${{dir.toUpperCase()}} ${{escapeHTML(r.line || "")}}</span>
        <span class="pick-card-edge ${{dir}}">${{edgeStr}} edge</span>
      </div>
      <div class="pick-card-pitcher">${{escapeHTML(r.pitcher || "")}}</div>
      <div class="pick-card-matchup">${{oppPrefix(r)}}${{escapeHTML(r.opp || "")}} · ${{formatGameTime(r.game_datetime_utc)}}</div>
      <div class="pick-card-stats">
        <div class="pick-card-stat">
          <span class="pick-card-stat-label">Our Proj</span>
          <span class="pick-card-stat-val">${{dash(proj)}}</span>
        </div>
        <div class="pick-card-stat">
          <span class="pick-card-stat-label">Our %</span>
          <span class="pick-card-stat-val">${{dash(r.p_over)}}</span>
        </div>
        <div class="pick-card-stat">
          <span class="pick-card-stat-label card-live-label">${{escapeHTML(liveCell.label)}}</span>
          <span class="pick-card-stat-val card-live-val ${{liveCell.cls}}">${{liveCell.html}}</span>
        </div>
      </div>
    </div>`;
  }}

  function renderHeroPicks(rows) {{
    const focus = rows.filter(r => {{
      const e = f(r.edge);
      return e !== null && classify(e) === "focus";
    }}).sort((a, b) => Math.abs(f(b.edge)) - Math.abs(f(a.edge)));

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
      const time = l.gameTimeISO ? `<span class="parlay-leg-time">${{formatGameTime(l.gameTimeISO)}}</span>` : "";
      return `<div class="parlay-leg">
        <span class="parlay-leg-dir ${{dirCls}}">${{l.dir.toUpperCase()}}${{lineStr}}</span>
        <span class="parlay-leg-name">${{escapeHTML(l.pitcher)}}</span>
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
    const addBtn = `<button type="button" class="parlay-add-btn local-only" data-legs='${{escapeHTML(JSON.stringify(formLegs))}}' title="Pre-populate the Bets-tab form with these legs">+ Add to bets</button>`;
    return `<div class="parlay-card ${{evCls}}">
      <div class="parlay-legs">${{legsHTML}}</div>
      <div class="parlay-stats">
        <div class="parlay-stat"><span class="parlay-stat-label">Payout</span><span class="parlay-stat-val">${{amerStr}}</span></div>
        <div class="parlay-stat"><span class="parlay-stat-label">Hit %</span><span class="parlay-stat-val">${{(p.combinedHit * 100).toFixed(1)}}%</span></div>
        <div class="parlay-stat"><span class="parlay-stat-label">Edge</span><span class="parlay-stat-val ${{edgeCls}}">${{edgePct}}</span></div>
        <div class="parlay-stat"><span class="parlay-stat-label">EV / $1</span><span class="parlay-stat-val ${{evCls}}">${{evStr}}</span></div>
      </div>
      <div class="parlay-card-actions">${{addBtn}}</div>
    </div>`;
  }}

  // Generate all 2-leg / 3-leg combos from focus picks, rank by EV per
  // $1, and render the top few. The DFS sites the user plays require
  // ≥ 2 legs per ticket, so this turns the model's picks into something
  // that can actually be wagered.
  function renderParlaySuggestions(rows) {{
    const PARLAY_INPUT_CAP = 8;     // cap focus pool before exploding combos
    const TOP_TWO = 5;
    const TOP_THREE = 3;
    const focus = rows.filter(r => {{
      const e = f(r.edge);
      return e !== null && classify(e) === "focus";
    }});
    if (focus.length < 2) return "";

    const legs = focus
      .map(pickLegFromRow)
      .filter(l => l !== null)
      .sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge))
      .slice(0, PARLAY_INPUT_CAP);
    if (legs.length < 2) return "";

    const buildSection = (k, top, label) => {{
      if (legs.length < k) return "";
      const ranked = combos(legs, k)
        .map(evaluateParlay)
        .sort((a, b) => b.ev - a.ev)
        .slice(0, top);
      if (!ranked.length) return "";
      return `<div class="parlay-section">
        <div class="parlay-section-title">${{label}}</div>
        <div class="parlay-grid">${{ranked.map(renderParlayCard).join("")}}</div>
      </div>`;
    }};

    const twoLeg = buildSection(2, TOP_TWO, "Top 2-leg parlays");
    const threeLeg = buildSection(3, TOP_THREE, "Top 3-leg parlays");
    if (!twoLeg && !threeLeg) return "";

    return `<section class="parlay-suggester">
      <div class="parlay-suggester-header">
        <h3>Parlay Suggestions</h3>
        <span class="parlay-note">Combos of today's focus picks · ranked by EV per $1 · assumes leg independence</span>
      </div>
      ${{twoLeg}}
      ${{threeLeg}}
    </section>`;
  }}

  function hitterRow(r) {{
    const edge = f(r.edge);
    const cls = classify(edge);
    const dir = edge === null || edge === 0 ? "" : (edge > 0 ? "over" : "under");
    const edgeStr = edge === null ? "—" : (edge > 0 ? "+" : "") + edge.toFixed(3);
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
      <td class="num">${{dash(r.p_over)}}</td>
      <td class="num"${{novigTitle}}>${{dash(r.novig_over)}}</td>
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
          ? '<td class="hit">HIT</td>'
          : '<td class="miss">MISS</td>';
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
  async function fetchTodaysCSV(prefix) {{
    for (let i = 0; i <= 3; i++) {{
      const d = dateInChicago(-i);
      const text = await fetchCSV(baseUrl() + `${{prefix}}_${{d}}.csv`);
      if (text) return {{ date: d, rows: parseCSV(text) }};
    }}
    return {{ date: null, rows: [] }};
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
    const picks = [];
    for (const {{ d, text }} of results) {{
      if (!text) continue;
      const rows = parseCSV(text);
      for (const r of rows) {{
        if (f(r.actual_ks) === null) continue;
        const edge = f(slateOrLive(r, "slate_edge", "edge"));
        if (edge === null) continue;
        if (classify(edge) !== "focus") continue;
        const dir = edge > 0 ? "over" : "under";
        const overHit = f(slateOrLive(r, "slate_over_hit", "over_hit"));
        if (overHit === null) continue;
        const won = (dir === "over" && overHit >= 1) ||
                    (dir === "under" && overHit < 1);
        const pnlField = dir === "over"
          ? slateOrLive(r, "slate_over_pnl", "over_pnl")
          : slateOrLive(r, "slate_under_pnl", "under_pnl");
        const pnl = f(pnlField);
        picks.push({{
          date: d, pitcher: r.pitcher || "", dir, won,
          pnl: pnl === null ? 0 : pnl,
        }});
      }}
    }}
    return picks;
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

  function renderTrackRecord(picks, maxDays) {{
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

    return `<section class="results-section">
      <h2>Track Record — last ${{maxDays}} days</h2>
      ${{summaryHTML}}
      ${{sparkHTML}}
      ${{splitHTML}}
      <table style="margin-top: 12px;">
        <thead><tr>
          <th>Date</th>
          <th class="num">Picks</th>
          <th class="num">Hits</th>
          <th class="num">Units</th>
        </tr></thead>
        <tbody>${{dayRows}}</tbody>
      </table>
    </section>`;
  }}

  // ---------- Personal bet ledger (local-only tab) ----------

  // Cached today's slate (pitcher_id → row) — populated on tab open.
  // Drives the per-leg picker dropdown and the live-K lookup. Refresh
  // by reloading the tab.
  let slatePitchers = [];
  let slateById = new Map();
  let liveKsByPid = new Map();
  let liveLastFetchedAt = null;

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

  async function apiLiveKs(pitcherIds) {{
    if (!pitcherIds.length) return {{}};
    const url = `/api/live-ks?ids=${{pitcherIds.join(",")}}`;
    try {{
      const r = await fetch(url, {{ cache: "no-cache" }});
      if (!r.ok) return {{}};
      const d = await r.json();
      return d.results || {{}};
    }} catch (e) {{
      return {{}};
    }}
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
        <span class="muted">(excluded from staked / ROI above)</span>
      </div>`;
    }}
    return `<div class="bets-totals-card">
      <div class="report-stat"><div class="report-label">Bets</div><div class="report-val">${{t.count}}</div><div class="report-sub">${{settledLabel}}</div></div>
      <div class="report-stat"><div class="report-label">Staked</div><div class="report-val">${{fmtMoney(t.staked)}}</div>${{stakedSub}}</div>
      <div class="report-stat"><div class="report-label">Returned</div><div class="report-val">${{fmtMoney(t.returned)}}</div></div>
      <div class="report-stat"><div class="report-label">Net</div><div class="report-val ${{netCls === "flat" ? "" : netCls}}">${{fmtSignedMoney(t.net)}}</div><div class="report-sub">on settled</div></div>
      <div class="report-stat"><div class="report-label">ROI</div><div class="report-val">${{roiStr}}</div></div>
    </div>${{freeLine}}`;
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

  function renderBetRow(b, extraCls = "") {{
    const result = b.result || "";
    const resultCell = result === "W"
      ? '<td class="result W">W</td>'
      : result === "L"
        ? '<td class="result L">L</td>'
        : '<td class="result pending">—</td>';
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
      actions = `<button class="act win" data-action="win">W</button>
                 <button class="act lose" data-action="lose">L</button>
                 <button class="act" data-action="edit">Edit</button>
                 <button class="act del" data-action="delete">×</button>`;
    }} else {{
      actions = `<button class="act" data-action="edit">Edit</button>
                 <button class="act" data-action="reopen">Reopen</button>
                 <button class="act del" data-action="delete">×</button>`;
    }}
    const legCount = (b.legs || []).length;
    const legsLabel = `${{legCount}}-leg`;
    const freeBadge = b.free_entry ? '<span class="free-badge" title="Free entry — excluded from staked / ROI">FREE</span>' : "";
    const stakeDisplay = b.free_entry
      ? `<span class="muted" title="Free entry — not counted toward staked">${{fmtMoney(b.stake)}}</span>`
      : fmtMoney(b.stake);
    const rowCls = "parlay-row" + (extraCls ? " " + extraCls : "");
    const detailCls = "parlay-detail hidden" + (extraCls ? " " + extraCls : "");
    return `<tr data-id="${{escapeHTML(b.id)}}" class="${{rowCls}}">
      <td>${{escapeHTML(fmtDate(b.date))}}</td>
      <td>
        <div class="parlay-summary" data-action="toggle-legs">
          <span class="parlay-toggle">▶</span>
          <span class="muted" style="font-size: 11px;">${{legsLabel}}</span>
          <span>${{legsSummary(b.legs)}}</span>
          <span class="parlay-inline-status" data-inline-status></span>
          ${{freeBadge}}
        </div>
      </td>
      <td class="num">${{stakeDisplay}}</td>
      <td class="num">${{b.odds ? parseFloat(b.odds).toFixed(2) : "—"}}</td>
      <td>${{escapeHTML(b.boost || "")}}</td>
      ${{resultCell}}
      <td class="num payout ${{payoutCls}}">${{payoutStr}}</td>
      <td class="actions">${{actions}}</td>
    </tr>
    <tr class="${{detailCls}}" data-detail-for="${{escapeHTML(b.id)}}">
      <td colspan="8">
        <div class="parlay-rollup" id="parlay-rollup-${{escapeHTML(b.id)}}"></div>
        <ol class="parlay-leg-list">
          ${{(b.legs || []).map((l, i) => {{
            const ouCls = l.ou === "O" ? "over" : "under";
            const lineStr = l.line !== null && l.line !== undefined && l.line !== ""
              ? parseFloat(l.line).toFixed(1) : "—";
            return `<li data-pitcher-id="${{l.pitcher_id || ""}}" data-ou="${{l.ou}}" data-line="${{l.line === null || l.line === undefined ? "" : l.line}}">
              <span class="muted">Leg ${{i + 1}}</span>
              <span class="parlay-leg-name">${{escapeHTML(l.pitcher || "?")}}</span>
              <span class="parlay-leg-ou ${{ouCls}}">${{l.ou}} ${{lineStr}}</span>
              <span class="live-cell">${{l.pitcher_id ? "—" : '<span class="muted" style="font-size:11px;">(no live data)</span>'}}</span>
            </li>`;
          }}).join("")}}
        </ol>
      </td>
    </tr>`;
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
      // Decide initial picker state: if the existing leg has a known
      // pitcher_id that matches a slate row, select it; else fall back
      // to "custom" mode with the freeform name.
      const isCustom = ex.pitcher && (!ex.pitcher_id || !slateById.has(ex.pitcher_id));
      const selectValue = isCustom
        ? "custom"
        : (ex.pitcher_id ? String(ex.pitcher_id) : "");
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
            <option value="">— Select pitcher —</option>
            <option value="custom">[Type custom name]</option>
            <option disabled>──────────</option>
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
      // Pre-set the select's value if matched
      // (note: needs to happen via JS post-render — handled in renderLegInputsWire)
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
        <div class="bets-field"><label>Stake</label><input id="bf-stake" type="number" step="0.01" placeholder="10.00"></div>
        <div class="bets-field"><label>Odds</label><input id="bf-odds" type="number" step="0.01" placeholder="2.40"></div>
        <div class="bets-field"><label>Boost (free text)</label><input id="bf-boost" placeholder="Free entry / +30%"></div>
      </div>
      <div class="bets-form-actions">
        <button id="bf-save" type="button">Save bet</button>
        <button id="bf-cancel" type="button" style="background: transparent; color: var(--muted); border: 1px solid var(--border); display: none;">Cancel edit</button>
        <label title="Excludes this bet from staked / ROI calculations. Winnings still count toward returned.">
          <input type="checkbox" id="bf-free-entry">
          Free entry (don't count toward staked / ROI)
        </label>
        <span id="bf-msg" class="bets-form-msg"></span>
      </div>
    </div>`;

    // Default view: today + last 2 days + any still-pending bet (so a
    // forgotten unsettled wager never falls off the page). Older settled
    // rows are appended hidden, behind a "Show older" toggle.
    const recentDates = new Set([0, -1, -2].map(o => dateInChicago(o)));
    const isRecent = (b) =>
      b.result === null || b.result === undefined || b.result === "" ||
      recentDates.has(b.date);
    const recent = sorted.filter(isRecent);
    const older = sorted.filter(b => !isRecent(b));

    let tableBody;
    if (!sorted.length) {{
      tableBody = `<tr><td colspan="8" class="empty-msg">No bets recorded yet. Add your first one above.</td></tr>`;
    }} else {{
      const recentHTML = recent.map(b => renderBetRow(b)).join("");
      const olderHTML = older.map(b => renderBetRow(b, "bets-older-row older-hidden")).join("");
      const olderLabel = `Show ${{older.length}} older bet${{older.length === 1 ? "" : "s"}}`;
      const toggleRow = older.length
        ? `<tr class="bets-older-toggle"><td colspan="8"><button type="button" id="bets-older-btn" class="bets-older-btn" data-state="hidden">${{olderLabel}}</button></td></tr>`
        : "";
      tableBody = recentHTML + toggleRow + olderHTML;
    }}

    const toolbar = `<div class="bets-toolbar">
      <div></div>
      <div>
        <span class="live-stamp" id="live-stamp">live K not yet fetched</span>
        <button type="button" class="refresh-live" id="refresh-live">↻ Refresh live</button>
      </div>
    </div>`;

    return `${{toolbar}}
      ${{renderBetsTotals(totals)}}
      <div class="bets-table-wrap"><table class="bets-ledger">
        <thead><tr>
          <th>Date</th>
          <th>Parlay</th>
          <th class="num">Stake</th>
          <th class="num">Odds</th>
          <th>Boost</th>
          <th>W/L</th>
          <th class="num">Payout</th>
          <th></th>
        </tr></thead>
        <tbody id="bets-tbody">${{tableBody}}</tbody>
      </table></div>
      ${{formHTML}}`;
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
    try {{
      // Fetch bets + slate in parallel so the dropdown is populated
      // by the time the form renders.
      const [state, pitchers] = await Promise.all([
        apiBets("GET"),
        apiSlatePitchers(),
      ]);
      slatePitchers = pitchers;
      slateById = new Map(pitchers.map(p => [p.pitcher_id, p]));
      panel.innerHTML = renderBetsTab(state);
      wireBetsHandlers(panel);
      // Kick off live K refresh for any pending bets with a pitcher_id.
      refreshLiveKs();
    }} catch (e) {{
      panel.innerHTML = `<p class="empty-msg">Bets API unavailable. Make sure the local Flask server is running (python -m bets.server).</p>`;
    }}
  }}

  // Collect unique pitcher_ids from currently-pending bets and fetch
  // live K status. Updates per-leg rows in place — no full re-render,
  // so the user can be expanding/scrolling without disruption.
  async function refreshLiveKs(opts) {{
    opts = opts || {{}};
    const stampEl = document.getElementById("live-stamp");
    const refreshBtn = document.getElementById("refresh-live");
    if (refreshBtn) refreshBtn.disabled = true;
    if (stampEl) stampEl.textContent = "fetching…";
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
        if (stampEl) stampEl.textContent = "no bets with linked pitchers yet";
        if (refreshBtn) refreshBtn.disabled = false;
        return;
      }}
      const results = await apiLiveKs(ids);
      liveKsByPid = new Map(Object.entries(results).map(([k, v]) => [parseInt(k, 10), v]));
      // Patch each visible leg's status cell.
      paintLiveKs();
      liveLastFetchedAt = new Date();
      if (stampEl) stampEl.textContent = `updated ${{liveLastFetchedAt.toLocaleTimeString("en-US", {{ hour: "numeric", minute: "2-digit", second: "2-digit" }})}}`;
    }} catch (e) {{
      if (stampEl) stampEl.textContent = "fetch failed";
    }} finally {{
      if (refreshBtn) refreshBtn.disabled = false;
    }}
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
  function legHitState(ks, line, ou, status) {{
    if (ks === null || ks === undefined || line === null || line === undefined) {{
      return null;
    }}
    if (ks > line) {{
      // OVER won, UNDER busted — locked in regardless of status
      return ou === "O" ? "hit" : "miss";
    }}
    if (status === "Final") {{
      // Final K count ≤ line: OVER missed, UNDER held
      return ou === "U" ? "hit" : "miss";
    }}
    return null;  // pending
  }}

  // Render the live status string + class for one leg given the
  // {{ks, line, status, ...}} payload from /api/live-ks.
  function liveStatusHTML(leg, live) {{
    if (!live) {{
      return `<span class="live-status">—</span>`;
    }}
    const status = live.status;
    const ks = live.ks;
    // Prefer the bet's recorded line — that's what the user wagered
    // against on the DFS site, which can differ from the sportsbook
    // line cached in the slate.
    const line = (leg.line !== null && leg.line !== undefined)
      ? leg.line : live.line;

    // Mid-game lock-in: if the math is already settled, show the
    // verdict immediately without waiting for Final.
    const hitState = legHitState(ks, line, leg.ou, status);
    if (hitState) {{
      const cls = `live-status ${{hitState}}`;
      const verdict = hitState === "hit" ? "HIT" : "MISS";
      // Tag the badge with where in the game it locked in (helpful
      // context — "MISS in 5th" is more informative than just "MISS").
      const inningTag = status === "Live" && live.current_inning
        ? ` <span class="muted" style="font-size:10px;">(in ${{escapeHTML(live.current_inning)}})</span>`
        : "";
      return `<span class="${{cls}}"><span class="live-ks">${{ks}} K</span><span class="live-badge">${{verdict}}</span>${{inningTag}}</span>`;
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
    }} else if (status === "Live") {{
      // Game in progress, math not yet settled (ks ≤ line, OVER could
      // still hit / UNDER could still hold).
      const inning = live.current_inning ? `${{live.inning_state || ""}} ${{live.current_inning}}`.trim() : "in progress";
      cls += " live";
      badge = `<span class="live-badge">Live · ${{escapeHTML(inning)}}</span>`;
      body = ks !== null ? `<span class="live-ks">${{ks}} K</span>` : "";
    }} else if (status === "Final") {{
      // status=Final but no ks recorded — pitcher didn't pitch, scratch,
      // or stat lookup failed.
      cls += " preview";
      badge = `<span class="live-badge">Final</span>`;
      body = ks !== null ? `<span class="live-ks">${{ks}} K</span>` : "no K data";
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
    document.querySelectorAll("tr.parlay-detail").forEach(tr => {{
      // Per-leg cells
      const legStates = [];
      tr.querySelectorAll("li[data-pitcher-id]").forEach(li => {{
        const pid = parseInt(li.dataset.pitcherId, 10);
        const ou = li.dataset.ou;
        const line = li.dataset.line ? parseFloat(li.dataset.line) : null;
        const cell = li.querySelector(".live-cell");
        const live = liveKsByPid.get(pid);
        if (cell) cell.innerHTML = liveStatusHTML({{ ou, line }}, live);
        if (live) {{
          legStates.push(legHitState(live.ks, line, ou, live.status));
        }} else {{
          // No live data — could be a leg without a pitcher_id, or fetch
          // hasn't happened yet. Treat as pending.
          legStates.push(null);
        }}
      }});
      // Parlay-level rollup
      const betId = tr.dataset.detailFor;
      const rollup = document.getElementById(`parlay-rollup-${{betId}}`);
      if (rollup) {{
        rollup.innerHTML = parlayRollupHTML(legStates, betId);
        rollup.className = "parlay-rollup " + parlayRollupClass(legStates);
      }}
      // Inline per-row status badge (shows without expanding the parlay)
      const row = document.querySelector(`tr.parlay-row[data-id="${{betId}}"]`);
      if (row) {{
        const inline = row.querySelector("[data-inline-status]");
        if (inline) inline.innerHTML = inlineStatusHTML(legStates);
      }}
      // Queue auto-settle if verdict is definitive and bet result is wrong
      const verdictCls = parlayRollupClass(legStates);
      if (verdictCls === "win" || verdictCls === "loss") {{
        autoSettleQueue.push({{ betId, verdict: verdictCls }});
      }}
    }});
    // Fire auto-settles asynchronously after painting completes.
    if (autoSettleQueue.length) maybeAutoSettle(autoSettleQueue);
  }}

  function inlineStatusHTML(legStates) {{
    if (!legStates.length) return "";
    const hits = legStates.filter(s => s === "hit").length;
    const misses = legStates.filter(s => s === "miss").length;
    const pending = legStates.filter(s => s === null).length;
    // Only show if there's actual live data resolving things —
    // all-pending with no live data isn't useful.
    if (hits === 0 && misses === 0) return "";
    const parts = [];
    if (hits) parts.push(`<span class="pi-h">${{hits}}H</span>`);
    if (misses) parts.push(`<span class="pi-m">${{misses}}M</span>`);
    if (pending) parts.push(`<span class="pi-p">${{pending}}P</span>`);
    // The wrapper already exists in the row markup; we just inject the
    // inner badge content here.
    return parts.join(" · ");
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

  function parlayRollupHTML(legStates, betId) {{
    const hits = legStates.filter(s => s === "hit").length;
    const misses = legStates.filter(s => s === "miss").length;
    const pending = legStates.filter(s => s === null).length;
    let verdict;
    if (misses > 0) verdict = "Loss confirmed";
    else if (pending === 0 && hits > 0) verdict = "Win confirmed";
    else verdict = "In progress";
    const counts = `${{hits}} hit · ${{misses}} miss · ${{pending}} pending`;
    // If the user has manually marked a result that disagrees with the
    // computed verdict, surface the mismatch.
    let mismatch = "";
    const row = document.querySelector(`tr.parlay-row[data-id="${{betId}}"]`);
    const resultCell = row ? row.querySelector("td.result") : null;
    const userResult = resultCell ? resultCell.textContent.trim() : "";
    if (verdict === "Loss confirmed" && userResult === "W") {{
      mismatch = `<span class="parlay-rollup-mismatch">⚠ Marked W but legs say loss — click Reopen to revisit</span>`;
    }} else if (verdict === "Win confirmed" && userResult === "L") {{
      mismatch = `<span class="parlay-rollup-mismatch">⚠ Marked L but all legs hit — click Reopen to revisit</span>`;
    }}
    return `<span class="parlay-rollup-verdict">${{verdict}}</span><span class="parlay-rollup-counts">${{counts}}</span>${{mismatch}}`;
  }}

  // The form has two modes: "add" (default — POST on save) and "edit"
  // (loaded from a bet — PUT on save). editingBetId is the discriminant.
  let editingBetId = null;

  function readForm() {{
    const get = id => document.getElementById(id).value.trim();
    return {{
      date: get("bf-date"),
      legs: readFormLegs(),
      stake: get("bf-stake"),
      odds: get("bf-odds"),
      boost: get("bf-boost"),
      free_entry: document.getElementById("bf-free-entry").checked,
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

    // "Show older" toggle: reveal/hide settled rows older than 3 days.
    const olderBtn = document.getElementById("bets-older-btn");
    if (olderBtn) {{
      olderBtn.addEventListener("click", () => {{
        const hide = olderBtn.dataset.state !== "hidden";
        panel.querySelectorAll("tr.bets-older-row").forEach(tr => {{
          tr.classList.toggle("older-hidden", hide);
        }});
        olderBtn.dataset.state = hide ? "hidden" : "shown";
        const olderCount = panel.querySelectorAll("tr.bets-older-row.parlay-row").length;
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

    // Initial paint after first render — handles the case where the
    // user arrived via "+ Add to bets" with pre-populated legs.
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
      // Action buttons take priority — don't toggle expand on button clicks.
      const btn = e.target.closest("button.act");
      if (!btn) {{
        // Click anywhere on a parlay-row toggles its detail. (Action
        // buttons short-circuit above so they still work.)
        const row = e.target.closest("tr.parlay-row");
        if (row) {{
          const id = row.dataset.id;
          const detail = panel.querySelector(`tr.parlay-detail[data-detail-for="${{id}}"]`);
          if (detail) {{
            row.classList.toggle("expanded");
            detail.classList.toggle("hidden");
            if (!detail.classList.contains("hidden")) paintLiveKs();
          }}
        }}
        return;
      }}
      const tr = btn.closest("tr");
      const id = tr.dataset.id;
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
    for (const r of rows) c[classify(f(r.edge))]++;
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
      // for the report-card header. Mirrors the Track Record logic but
      // scoped to a single day. Slate-time fields preferred.
      const dayPicks = [];
      for (const r of settled.rows) {{
        const edge = f(slateOrLive(r, "slate_edge", "edge"));
        if (edge === null) continue;
        if (classify(edge) !== "focus") continue;
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
        <table>
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
        </table>
      </section>`;
    }} else {{
      resultsSection = `<section class="results-section"><h2>Recent Results</h2><p class="muted">No settled days yet.</p></section>`;
    }}

    const trackSection = renderTrackRecord(trackPicks, trackDays);
    return {{ html: pitcherTabHTML(heroSection, parlaySection, slateBody, resultsSection + trackSection, cnt), cnt }};
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
        <table>
          <thead><tr>
            <th>Hitter</th><th>Team</th>
            <th class="num">Proj</th><th class="num">Actual</th>
            <th class="num">Off By</th><th class="num">Line</th>
            <th>Result</th>
          </tr></thead>
          <tbody>${{sortedSettled.map(hitterResultRow).join("")}}</tbody>
        </table>
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
    return `${{heroSection}}
    ${{parlaySection}}
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
    ${{toolbar}}
    <table>
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
    </table>
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
    <table>
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
    </table>
    ${{resultsSection}}`;
  }}

  async function loadAndRender() {{
    const btn = document.getElementById("refresh-btn");
    if (btn) {{ btn.disabled = true; btn.textContent = "Refreshing…"; }}
    document.body.classList.add("loading");

    const TRACK_DAYS = 14;
    try {{
      const fetches = [
        fetchTodaysCSV("pitcher_ks"),
        fetchMostRecentSettled("pitcher_ks"),
        fetchTrackRecord(TRACK_DAYS),
      ];
      if (SHOW_HITTERS) {{
        fetches.push(
          fetchTodaysCSV("hitter_ks"),
          fetchMostRecentSettled("hitter_ks"),
        );
      }}
      const results = await Promise.all(fetches);
      const [pSlate, pSettled, pTrack, hSlate, hSettled] = results;

      const pTab = renderPitcherTab({{
        slate: pSlate, settled: pSettled,
        trackPicks: pTrack, trackDays: TRACK_DAYS,
      }});
      const pPanel = document.getElementById("pitcher-panel");
      if (pPanel) pPanel.innerHTML = pTab.html;
      // Wire noise toggle (and apply persisted preference) immediately
      // after the toolbar lands in the DOM. Must run before any await
      // so the initial paint already reflects the user's default.
      wireNoiseToggle();
      const pCounts = document.getElementById("pitcher-counts");
      if (pCounts) pCounts.textContent =
        `(${{pTab.cnt.focus}} focus / ${{pTab.cnt.investigate}} verify)`;

      // Live K + game-status overlay — fires after the table is on
      // screen so the initial paint isn't blocked on the MLB API. Once
      // the data arrives, repaintGameTimeCells() patches each row in
      // place (no full re-render).
      if (pSlate.rows.length && pSlate.date) {{
        fetchLiveKsPublic(pSlate.rows, pSlate.date)
          .then(byPid => {{ _liveByPid = byPid; repaintGameTimeCells(); }})
          .catch(() => {{}});
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
      document.body.classList.remove("loading");
      if (btn) {{ btn.disabled = false; btn.textContent = "Refresh data"; }}
    }}
  }}

  let betsLoaded = false;

  function showTab(name) {{
    document.querySelectorAll(".tab-panel").forEach(p =>
      p.classList.toggle("active", p.dataset.tab === name)
    );
    document.querySelectorAll(".tabs button").forEach(b =>
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

  document.addEventListener("DOMContentLoaded", () => {{
    updateHeaderDate();
    applyNoisePreference();

    // Single delegated handler for "+ Add to bets" buttons rendered on
    // parlay-suggester cards (those cards live in the pitcher tab and
    // get re-rendered on each refresh — document-level delegation
    // survives those replacements).
    document.addEventListener("click", (e) => {{
      const btn = e.target.closest(".parlay-add-btn");
      if (!btn) return;
      handleAddParlayToBets(btn.dataset.legs || "[]");
    }});

    document.querySelectorAll(".tabs button").forEach(b => {{
      b.addEventListener("click", () => showTab(b.dataset.tab));
    }});
    const allowed = ["pitchers"];
    if (SHOW_HITTERS) allowed.push("hitters");
    if (isLocal()) allowed.push("bets");
    const initial = (location.hash || "#pitchers").slice(1);
    showTab(allowed.includes(initial) ? initial : "pitchers");

    const btn = document.getElementById("refresh-btn");
    if (btn) btn.addEventListener("click", loadAndRender);

    loadAndRender();

    // Tick the gametime cells every minute so "in NN min" stays
    // accurate and a row flips to row-locked the moment first pitch
    // passes — no full re-render or refetch.
    setInterval(repaintGameTimeCells, 60000);
  }});
}})();
"""


def generate(target_date: date | None = None) -> Path | None:
    target_date = target_date or date.today()

    actions_block = _action_buttons_html()
    js = _render_js()

    # Tab nav: pitchers always visible. Hitters when SHOW_HITTERS.
    # Bets is always present in the HTML but tagged local-only — the
    # CSS hides it on Netlify, the JS only allows navigation to it on
    # localhost.
    pitcher_btn = '<button data-tab="pitchers" type="button">Pitcher Ks <span class="count" id="pitcher-counts"></span></button>'
    hitter_btn = '<button data-tab="hitters" type="button">Hitter Ks <span class="count" id="hitter-counts"></span></button>' if SHOW_HITTERS else ""
    bets_btn = '<button class="local-only" data-tab="bets" type="button">Bets</button>'
    tabs_nav = "    " + "\n    ".join(b for b in (pitcher_btn, hitter_btn, bets_btn) if b)

    pitcher_panel = '<div class="tab-panel active" data-tab="pitchers" id="pitcher-panel">\n    <p class="muted">Loading…</p>\n  </div>'
    hitter_panel = '<div class="tab-panel" data-tab="hitters" id="hitter-panel">\n    <p class="muted">Loading…</p>\n  </div>' if SHOW_HITTERS else ""
    bets_panel = '<div class="tab-panel local-only" data-tab="bets" id="bets-panel">\n    <p class="muted">Loading…</p>\n  </div>'
    panels = "  " + "\n  ".join(p for p in (pitcher_panel, hitter_panel, bets_panel) if p)

    # Note: NO date or timestamp in the shell — those are rendered client-
    # side by JS so the shell stays byte-identical across regens.
    # Otherwise every daily run would change index.html and trigger a
    # Netlify redeploy, defeating the credit-saving design.
    # Synchronous head script: tags <html> as local-only-buttons-eligible
    # before first paint, so .local-only buttons stay hidden on Netlify
    # and reveal cleanly on localhost (no flash). The same hostname check
    # is mirrored later in baseUrl() to pick the CSV source.
    local_check = (
        "(function(){var h=location.hostname;"
        "if(h===''||h==='localhost'||h==='127.0.0.1')"
        "document.documentElement.classList.add('is-local');})();"
    )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MLB K Props</title>
<script>{local_check}</script>
<style>{CSS}</style>
<script>{js}</script>
</head>
<body>
<header>
  <div>
    <h1>MLB K Prop Projections</h1>
    <div class="date" id="header-date"></div>
  </div>
  {actions_block}
  <nav class="tabs{' local-only' if not SHOW_HITTERS else ''}">
{tabs_nav}
  </nav>
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
