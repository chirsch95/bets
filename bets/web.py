"""Static HTML dashboard generator.

Renders a tabbed dashboard with two views:
    - Pitcher Ks (today's slate + Recent Results)
    - Hitter Ks  (today's slate + Recent Results)

Both tabs are server-rendered into the same index.html; a tiny JS toggle
switches which section is visible. Open in a browser:

    python -m bets.web              # today
    python -m bets.web 2026-04-30   # specific date
"""

from __future__ import annotations

import csv
import html
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

try:
    from zoneinfo import ZoneInfo  # py3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from .config import OUTPUT_DIR


def _is_static_mode() -> bool:
    """When STATIC_MODE=1, render a read-only dashboard (no buttons).

    Set this in the GitHub Actions workflow that publishes to Netlify, so
    the deployed page doesn't show buttons that POST to a Flask server
    that isn't there.
    """
    return os.environ.get("STATIC_MODE", "").strip() not in ("", "0", "false", "False")


def _generated_at_label() -> str:
    """Return a human-friendly 'last refreshed' string in US/Eastern."""
    now_utc = datetime.now(timezone.utc)
    if ZoneInfo is not None:
        try:
            return now_utc.astimezone(ZoneInfo("America/New_York")).strftime(
                "%b %d, %Y %-I:%M %p ET"
            )
        except Exception:  # noqa: BLE001
            pass
    return now_utc.strftime("%b %d, %Y %H:%M UTC")

# Edge bands (shared between pitcher and hitter views).
FOCUS_EDGE_MIN = 0.05
FOCUS_EDGE_MAX = 0.15
INVESTIGATE_EDGE = 0.20


def _f(v):
    if v in ("", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify(edge: float | None) -> str:
    if edge is None:
        return "noline"
    abs_edge = abs(edge)
    if abs_edge >= INVESTIGATE_EDGE:
        return "investigate"
    if FOCUS_EDGE_MIN <= abs_edge <= FOCUS_EDGE_MAX:
        return "focus"
    return "noise"


def _label(cls: str, direction: str) -> str:
    if cls == "focus" and direction:
        return f"Bet <strong>{direction.upper()}</strong>"
    if cls == "investigate" and direction:
        return f"Verify <strong>{direction.upper()}</strong>?"
    if cls == "noline":
        return "No line"
    return "—"


def _dash(value) -> str:
    if value in ("", None):
        return "—"
    return html.escape(str(value))


def _sort_key(r: dict, proj_field: str):
    edge = _f(r.get("edge"))
    cls = _classify(edge)
    cls_rank = {"focus": 0, "investigate": 1, "noise": 2, "noline": 3}[cls]
    edge_rank = -abs(edge) if edge is not None else 0
    proj = -(_f(r.get(proj_field)) or 0)
    return (cls_rank, edge_rank, proj)


# ---------- Pitcher view ----------


def _pitcher_row_html(r: dict) -> str:
    edge = _f(r.get("edge"))
    cls = _classify(edge)
    direction = ""
    if edge is not None and edge != 0:
        direction = "over" if edge > 0 else "under"

    edge_str = f"{edge:+.3f}" if edge is not None else "—"
    row_classes = f"row-{cls}" + (f" dir-{direction}" if direction else "")
    tag_classes = f"tag-{cls}" + (f" tag-dir-{direction}" if direction else "")

    over_book = r.get("over_book") or ""
    under_book = r.get("under_book") or ""
    n_books = r.get("n_books") or ""
    over_title = f' title="Best price at {over_book}"' if over_book else ""
    under_title = f' title="Best price at {under_book}"' if under_book else ""
    novig_title = f' title="Median across {n_books} books"' if n_books else ""

    proj = r.get("proj_ks_v2") or r.get("proj_ks_v1")
    return f"""    <tr class="{row_classes}">
      <td class="player">{html.escape(r.get('pitcher', ''))}</td>
      <td>{html.escape(r.get('opp', ''))}</td>
      <td class="num">{_dash(proj)}</td>
      <td class="num">{_dash(r.get('line'))}</td>
      <td class="num"{over_title}>{_dash(r.get('over_odds'))}</td>
      <td class="num"{under_title}>{_dash(r.get('under_odds'))}</td>
      <td class="num">{_dash(r.get('p_over'))}</td>
      <td class="num"{novig_title}>{_dash(r.get('novig_over'))}</td>
      <td class="num edge {direction}">{edge_str}</td>
      <td class="badge"><span class="tag {tag_classes}">{_label(cls, direction)}</span></td>
    </tr>"""


def _pitcher_result_row_html(r: dict) -> str:
    actual = _f(r.get("actual_ks"))
    proj = _f(r.get("proj_ks_v2")) or _f(r.get("proj_ks_v1"))
    line = _f(r.get("line"))
    over_hit = _f(r.get("over_hit"))

    err = (actual - proj) if (actual is not None and proj is not None) else None
    if err is None:
        err_cls = "zero"
        err_str = "—"
    elif err > 0.5:
        err_cls = "over"
        err_str = f"+{err:.1f}"
    elif err < -0.5:
        err_cls = "under"
        err_str = f"{err:.1f}"
    else:
        err_cls = "zero"
        err_str = f"{err:+.1f}"

    if line is None:
        result_cell = '<td class="muted">no line</td>'
    elif over_hit is None:
        result_cell = '<td class="muted">—</td>'
    elif over_hit >= 1:
        result_cell = '<td class="hit">OVER hit</td>'
    else:
        result_cell = '<td class="miss">UNDER hit</td>'

    proj_cell = r.get("proj_ks_v2") or r.get("proj_ks_v1")
    return f"""    <tr>
      <td class="player">{html.escape(r.get('pitcher', ''))}</td>
      <td>{html.escape(r.get('opp', ''))}</td>
      <td class="num">{_dash(proj_cell)}</td>
      <td class="num">{int(actual) if actual is not None else '—'}</td>
      <td class="num error {err_cls}">{err_str}</td>
      <td class="num">{_dash(r.get('line'))}</td>
      {result_cell}
    </tr>"""


# ---------- Hitter view ----------


def _hitter_row_html(r: dict) -> str:
    edge = _f(r.get("edge"))
    cls = _classify(edge)
    direction = ""
    if edge is not None and edge != 0:
        direction = "over" if edge > 0 else "under"

    edge_str = f"{edge:+.3f}" if edge is not None else "—"
    row_classes = f"row-{cls}" + (f" dir-{direction}" if direction else "")
    tag_classes = f"tag-{cls}" + (f" tag-dir-{direction}" if direction else "")

    over_book = r.get("over_book") or ""
    under_book = r.get("under_book") or ""
    n_books = r.get("n_books") or ""
    over_title = f' title="Best price at {over_book}"' if over_book else ""
    under_title = f' title="Best price at {under_book}"' if under_book else ""
    novig_title = f' title="Median across {n_books} books"' if n_books else ""

    return f"""    <tr class="{row_classes}">
      <td class="player">{html.escape(r.get('hitter', ''))}</td>
      <td class="num slot">{_dash(r.get('slot'))}</td>
      <td>{html.escape(r.get('team', ''))}</td>
      <td>vs {html.escape(r.get('opp_pitcher', ''))}</td>
      <td class="num">{_dash(r.get('proj_ks'))}</td>
      <td class="num">{_dash(r.get('line'))}</td>
      <td class="num"{over_title}>{_dash(r.get('over_odds'))}</td>
      <td class="num"{under_title}>{_dash(r.get('under_odds'))}</td>
      <td class="num">{_dash(r.get('p_over'))}</td>
      <td class="num"{novig_title}>{_dash(r.get('novig_over'))}</td>
      <td class="num edge {direction}">{edge_str}</td>
      <td class="badge"><span class="tag {tag_classes}">{_label(cls, direction)}</span></td>
    </tr>"""


def _hitter_result_row_html(r: dict) -> str:
    actual = _f(r.get("actual_ks"))
    proj = _f(r.get("proj_ks"))
    line = _f(r.get("line"))
    over_hit = _f(r.get("over_hit"))

    err = (actual - proj) if (actual is not None and proj is not None) else None
    if err is None:
        err_cls = "zero"
        err_str = "—"
    elif err > 0.3:
        err_cls = "over"
        err_str = f"+{err:.1f}"
    elif err < -0.3:
        err_cls = "under"
        err_str = f"{err:.1f}"
    else:
        err_cls = "zero"
        err_str = f"{err:+.1f}"

    if line is None:
        result_cell = '<td class="muted">no line</td>'
    elif over_hit is None:
        result_cell = '<td class="muted">—</td>'
    elif over_hit >= 1:
        result_cell = '<td class="hit">OVER hit</td>'
    else:
        result_cell = '<td class="miss">UNDER hit</td>'

    return f"""    <tr>
      <td class="player">{html.escape(r.get('hitter', ''))}</td>
      <td>{html.escape(r.get('team', ''))}</td>
      <td class="num">{_dash(r.get('proj_ks'))}</td>
      <td class="num">{int(actual) if actual is not None else '—'}</td>
      <td class="num error {err_cls}">{err_str}</td>
      <td class="num">{_dash(r.get('line'))}</td>
      {result_cell}
    </tr>"""


# ---------- Settled-history loaders ----------


def _load_recent_settled(prefix: str, days: int = 14) -> list[dict]:
    cutoff = date.today() - timedelta(days=days)
    rows = []
    for path in sorted(OUTPUT_DIR.glob(f"{prefix}_*_settled.csv")):
        try:
            d = datetime.strptime(path.stem.split("_")[2], "%Y-%m-%d").date()
        except (IndexError, ValueError):
            continue
        if d < cutoff:
            continue
        with path.open() as f:
            for r in csv.DictReader(f):
                if _f(r.get("actual_ks")) is None:
                    continue
                rows.append(r)
    return rows


def _load_most_recent_settled(prefix: str) -> tuple[date | None, list[dict]]:
    candidates = []
    for path in OUTPUT_DIR.glob(f"{prefix}_*_settled.csv"):
        try:
            d = datetime.strptime(path.stem.split("_")[2], "%Y-%m-%d").date()
        except (IndexError, ValueError):
            continue
        candidates.append((d, path))
    if not candidates:
        return None, []
    candidates.sort(key=lambda x: x[0], reverse=True)
    d, path = candidates[0]
    with path.open() as f:
        rows = [r for r in csv.DictReader(f) if _f(r.get("actual_ks")) is not None]
    return d, rows


# ---------- Summary boxes ----------


def _pitcher_summary_html(rows: list[dict]) -> str:
    if not rows:
        return (
            "<p class='muted'>No settled history yet — run "
            "<code>python -m bets.settle &lt;date&gt;</code> on past dates "
            "once you have a few days of projections.</p>"
        )

    e2 = [x for x in (_f(r.get("error_v2")) for r in rows) if x is not None]
    e1 = [x for x in (_f(r.get("error_v1")) for r in rows) if x is not None]
    pnls = [x for x in (_f(r.get("over_pnl")) for r in rows) if x is not None]

    parts = [f"<strong>{len(rows)}</strong> settled starts in last 14 days"]
    if e2:
        parts.append(f"v2 MAE <strong>{mean(abs(x) for x in e2):.2f}</strong>")
        parts.append(f"bias <strong>{mean(e2):+.2f}</strong>")
    elif e1:
        parts.append(f"v1 MAE <strong>{mean(abs(x) for x in e1):.2f}</strong>")
        parts.append(f"bias <strong>{mean(e1):+.2f}</strong>")
    if pnls:
        parts.append(
            f"flat-bet over ROI <strong>{mean(pnls):+.3f}</strong>/$1 "
            f"(n={len(pnls)})"
        )
    return "<p>" + " &middot; ".join(parts) + "</p>"


def _hitter_summary_html(rows: list[dict]) -> str:
    if not rows:
        return (
            "<p class='muted'>No settled hitter history yet — settle past dates "
            "with <code>python -m bets.settle &lt;date&gt;</code> once you have "
            "hitter projections.</p>"
        )

    errs = [x for x in (_f(r.get("error")) for r in rows) if x is not None]
    pnls = [x for x in (_f(r.get("over_pnl")) for r in rows) if x is not None]

    parts = [f"<strong>{len(rows)}</strong> settled batters in last 14 days"]
    if errs:
        parts.append(f"MAE <strong>{mean(abs(x) for x in errs):.2f}</strong>")
        parts.append(f"bias <strong>{mean(errs):+.2f}</strong>")
    if pnls:
        parts.append(
            f"flat-bet over ROI <strong>{mean(pnls):+.3f}</strong>/$1 "
            f"(n={len(pnls)})"
        )
    return "<p>" + " &middot; ".join(parts) + "</p>"


# ---------- Recent Results sections ----------


def _pitcher_results_section(target_date: date) -> str:
    settled_date, rows = _load_most_recent_settled("pitcher_ks")
    if not rows:
        return (
            "<section class='results-section'>"
            "<h2>Recent Results</h2>"
            "<p class='muted'>No settled days yet. Click "
            "<strong>Settle Yesterday</strong> after games finish to populate.</p>"
            "</section>"
        )

    rows.sort(
        key=lambda r: (_f(r.get("error_v2")) or _f(r.get("error_v1")) or 0),
        reverse=True,
    )

    n = len(rows)
    errs = [
        x
        for x in (
            _f(r.get("error_v2")) or _f(r.get("error_v1")) for r in rows
        )
        if x is not None
    ]
    mae = (sum(abs(x) for x in errs) / len(errs)) if errs else 0
    bias = (sum(errs) / len(errs)) if errs else 0
    hits = [_f(r.get("over_hit")) for r in rows]
    hits = [int(h) for h in hits if h is not None]
    over_rate = (sum(hits) / len(hits)) if hits else None

    summary_parts = [
        f"<strong>{n}</strong> pitchers",
        f"MAE <strong>{mae:.2f}</strong>",
        f"bias <strong>{bias:+.2f}</strong>",
    ]
    if over_rate is not None:
        summary_parts.append(
            f"OVER hit <strong>{over_rate:.0%}</strong> "
            f"({sum(hits)}/{len(hits)} lines)"
        )

    table_body = "\n".join(_pitcher_result_row_html(r) for r in rows)
    title = _results_title("Results", target_date, settled_date)

    return f"""<section class="results-section">
  <h2>{title}</h2>
  <p class="muted">{' &middot; '.join(summary_parts)}</p>
  <table>
    <thead>
      <tr>
        <th>Pitcher</th>
        <th>Opponent</th>
        <th class="num" title="Model projection">Proj</th>
        <th class="num" title="Actual strikeouts">Actual</th>
        <th class="num" title="Actual minus projected">Off By</th>
        <th class="num" title="Sportsbook line that day">Line</th>
        <th title="Whether OVER or UNDER hit">Result</th>
      </tr>
    </thead>
    <tbody>
{table_body}
    </tbody>
  </table>
</section>"""


def _hitter_results_section(target_date: date) -> str:
    settled_date, rows = _load_most_recent_settled("hitter_ks")
    if not rows:
        return (
            "<section class='results-section'>"
            "<h2>Recent Results</h2>"
            "<p class='muted'>No settled hitter days yet. Click "
            "<strong>Settle Yesterday</strong> after games finish to populate.</p>"
            "</section>"
        )

    # Sort by absolute error (largest misses first), keep top 30 to keep the
    # section scannable — slate is much bigger than pitchers.
    def _abs_err(r):
        e = _f(r.get("error"))
        return abs(e) if e is not None else -1
    rows.sort(key=_abs_err, reverse=True)
    rows = rows[:40]

    errs = [x for x in (_f(r.get("error")) for r in rows) if x is not None]
    mae = (sum(abs(x) for x in errs) / len(errs)) if errs else 0
    bias = (sum(errs) / len(errs)) if errs else 0
    hits = [_f(r.get("over_hit")) for r in rows]
    hits = [int(h) for h in hits if h is not None]
    over_rate = (sum(hits) / len(hits)) if hits else None

    summary_parts = [
        f"<strong>{len(rows)}</strong> top-error hitters",
        f"MAE <strong>{mae:.2f}</strong>",
        f"bias <strong>{bias:+.2f}</strong>",
    ]
    if over_rate is not None:
        summary_parts.append(
            f"OVER hit <strong>{over_rate:.0%}</strong> "
            f"({sum(hits)}/{len(hits)} lines)"
        )

    table_body = "\n".join(_hitter_result_row_html(r) for r in rows)
    title = _results_title("Results", target_date, settled_date)

    return f"""<section class="results-section">
  <h2>{title}</h2>
  <p class="muted">{' &middot; '.join(summary_parts)}</p>
  <table>
    <thead>
      <tr>
        <th>Hitter</th>
        <th>Team</th>
        <th class="num" title="Model projection">Proj</th>
        <th class="num" title="Actual strikeouts">Actual</th>
        <th class="num" title="Actual minus projected">Off By</th>
        <th class="num" title="Sportsbook line that day">Line</th>
        <th title="Whether OVER or UNDER hit">Result</th>
      </tr>
    </thead>
    <tbody>
{table_body}
    </tbody>
  </table>
</section>"""


def _results_title(prefix: str, target_date: date, settled_date: date) -> str:
    header_label = settled_date.strftime("%A, %B %d, %Y")
    days_ago = (target_date - settled_date).days
    if days_ago == 0:
        return f"Today's {prefix} — {header_label}"
    if days_ago == 1:
        return f"Yesterday's {prefix} — {header_label}"
    return f"Most Recent {prefix} — {header_label}"


# ---------- Tab assembly ----------


def _pitcher_tab(target_date: date) -> tuple[str, dict]:
    proj_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    counts = {"focus": 0, "investigate": 0, "noise": 0, "noline": 0}
    if not proj_path.exists():
        body = (
            "<p class='muted'>No pitcher projections yet — run "
            "<strong>Refresh Lines</strong> or "
            "<code>python -m bets.main</code>.</p>"
        )
        return body, counts

    with proj_path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "<p class='muted'>Projection file is empty.</p>", counts

    rows.sort(key=lambda r: _sort_key(r, "proj_ks_v2"))
    for r in rows:
        counts[_classify(_f(r.get("edge")))] += 1

    settled = _load_recent_settled("pitcher_ks")
    summary = _pitcher_summary_html(settled)
    table_rows = "\n".join(_pitcher_row_html(r) for r in rows)
    results_section = _pitcher_results_section(target_date)

    body = f"""
  <div class="summary">{summary}</div>

  <div class="legend">
    <div class="legend-row">
      <span class="tag tag-focus tag-dir-over">Bet <strong>OVER</strong></span>
      <span class="tag tag-focus tag-dir-under">Bet <strong>UNDER</strong></span>
      <span>moderate edge ({FOCUS_EDGE_MIN:.0%}–{FOCUS_EDGE_MAX:.0%}) — actionable pick</span>
    </div>
    <div class="legend-row">
      <span class="tag tag-investigate">Verify <strong>OVER</strong>?</span>
      <span class="tag tag-investigate">Verify <strong>UNDER</strong>?</span>
      <span>extreme edge (&ge; {INVESTIGATE_EDGE:.0%}) — model probably wrong, do not bet</span>
    </div>
    <div class="legend-row">
      <span class="tag tag-noline">No line</span>
      <span>book hasn't posted, or game already started</span>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Pitcher</th>
        <th>Opponent</th>
        <th class="num" title="Model projection (v2)">Our Proj</th>
        <th class="num" title="Sportsbook over/under line">Book Line</th>
        <th class="num" title="Best OVER price across all US books">Over Odds</th>
        <th class="num" title="Best UNDER price across all US books">Under Odds</th>
        <th class="num" title="Our Poisson P(over)">Our Over %</th>
        <th class="num" title="Median no-vig P(over) across books">Book Over %</th>
        <th class="num" title="Our Over % minus Book Over %">Edge</th>
        <th title="Pick recommendation">Pick</th>
      </tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

  {results_section}
"""
    return body, counts


def _hitter_tab(target_date: date) -> tuple[str, dict]:
    proj_path = OUTPUT_DIR / f"hitter_ks_{target_date.isoformat()}.csv"
    counts = {"focus": 0, "investigate": 0, "noise": 0, "noline": 0}
    if not proj_path.exists():
        body = (
            "<p class='muted'>No hitter projections yet — run "
            "<strong>Refresh Lines</strong> or "
            "<code>python -m bets.hitters</code>. "
            "Hitter projections require confirmed lineups, which usually "
            "post 2–3 hours before first pitch.</p>"
        )
        return body, counts

    with proj_path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "<p class='muted'>Hitter projection file is empty.</p>", counts

    rows.sort(key=lambda r: _sort_key(r, "proj_ks"))
    for r in rows:
        counts[_classify(_f(r.get("edge")))] += 1

    settled = _load_recent_settled("hitter_ks")
    summary = _hitter_summary_html(settled)
    table_rows = "\n".join(_hitter_row_html(r) for r in rows)
    results_section = _hitter_results_section(target_date)

    body = f"""
  <div class="summary">{summary}</div>

  <div class="legend">
    <div class="legend-row">
      <span class="tag tag-focus tag-dir-over">Bet <strong>OVER</strong></span>
      <span class="tag tag-focus tag-dir-under">Bet <strong>UNDER</strong></span>
      <span>moderate edge ({FOCUS_EDGE_MIN:.0%}–{FOCUS_EDGE_MAX:.0%}) — actionable pick</span>
    </div>
    <div class="legend-row">
      <span class="tag tag-investigate">Verify <strong>OVER</strong>?</span>
      <span class="tag tag-investigate">Verify <strong>UNDER</strong>?</span>
      <span>extreme edge (&ge; {INVESTIGATE_EDGE:.0%}) — model probably wrong, do not bet</span>
    </div>
    <div class="legend-row">
      <span class="tag tag-noline">No line</span>
      <span>book hasn't posted batter K market for this player</span>
    </div>
  </div>

  <details class="howto">
    <summary>How the hitter K projection works</summary>
    <dl>
      <dt>Projection</dt>
      <dd>(Hitter K% × opposing starter K% / league K%) × park × expected PA. Expected PA comes from a static lineup-slot table (leadoff ≈ 4.65, #9 ≈ 3.85). v0 treats the whole game as if every PA were vs the starter — bullpen K% blending is a future improvement.</dd>
      <dt>Lines</dt>
      <dd>Hitter K lines are usually 0.5 or 1.5. Distribution is Poisson-like with low mean, so the same P(over) math from the pitcher model carries over.</dd>
      <dt>Heads up</dt>
      <dd>Hitter projections only render for batters whose lineup card is confirmed in the MLB API — typically 2–3 hours before first pitch.</dd>
    </dl>
  </details>

  <table>
    <thead>
      <tr>
        <th>Hitter</th>
        <th class="num" title="Batting-order slot">Slot</th>
        <th>Team</th>
        <th>Matchup</th>
        <th class="num" title="Model projection">Our Proj</th>
        <th class="num" title="Sportsbook line">Book Line</th>
        <th class="num">Over Odds</th>
        <th class="num">Under Odds</th>
        <th class="num">Our Over %</th>
        <th class="num">Book Over %</th>
        <th class="num">Edge</th>
        <th>Pick</th>
      </tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

  {results_section}
"""
    return body, counts


# ---------- CSS / shell ----------


CSS = """
  :root {
    --bg: #0e1015;
    --panel: #161922;
    --text: #e6e8eb;
    --muted: #8a93a3;
    --border: #232734;
    --green: #4ade80;
    --green-bg: rgba(74, 222, 128, 0.1);
    --red: #f87171;
    --red-bg: rgba(248, 113, 113, 0.1);
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
  .actions { display: flex; gap: 8px; padding-top: 4px; }
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
  .static-meta {
    align-self: center;
    color: var(--muted);
    font-size: 12px;
  }
  .static-meta .last-refresh strong { color: var(--text); font-weight: 500; }
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

# Tab toggle keeps state in URL hash so a refresh / settle round-trip stays
# on the same tab.
TAB_JS = """
  function showTab(name) {
    document.querySelectorAll('.tab-panel').forEach(p =>
      p.classList.toggle('active', p.dataset.tab === name)
    );
    document.querySelectorAll('.tabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === name)
    );
    if (location.hash !== '#' + name) {
      history.replaceState(null, '', '#' + name);
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tabs button').forEach(b => {
      b.addEventListener('click', () => showTab(b.dataset.tab));
    });
    const initial = (location.hash || '#pitchers').slice(1);
    showTab(['pitchers','hitters'].includes(initial) ? initial : 'pitchers');
  });
"""


def generate(target_date: date | None = None) -> Path | None:
    target_date = target_date or date.today()

    pitcher_body, p_counts = _pitcher_tab(target_date)
    hitter_body, h_counts = _hitter_tab(target_date)

    pitcher_label = (
        f"Pitcher Ks <span class='count'>"
        f"({p_counts['focus']} focus / {p_counts['investigate']} verify)</span>"
    )
    hitter_label = (
        f"Hitter Ks <span class='count'>"
        f"({h_counts['focus']} focus / {h_counts['investigate']} verify)</span>"
    )

    static_mode = _is_static_mode()
    refreshed_at = _generated_at_label()
    if static_mode:
        actions_block = (
            f"<div class='actions static-meta'>"
            f"<span class='last-refresh'>Auto-refreshed daily &middot; "
            f"<strong>{refreshed_at}</strong></span>"
            f"</div>"
        )
    else:
        actions_block = """<div class="actions">
    <form action="/refresh" method="post" onsubmit="document.body.classList.add('loading');">
      <button type="submit" class="primary">Refresh Lines</button>
    </form>
    <form action="/settle" method="post" onsubmit="document.body.classList.add('loading');">
      <button type="submit">Settle Yesterday</button>
    </form>
  </div>"""

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MLB K Props — {target_date.isoformat()}</title>
<style>{CSS}</style>
<script>{TAB_JS}</script>
</head>
<body>
<header>
  <div>
    <h1>MLB K Prop Projections</h1>
    <div class="date">{target_date.strftime('%A, %B %d, %Y')}</div>
  </div>
  {actions_block}
  <nav class="tabs">
    <button data-tab="pitchers" type="button">{pitcher_label}</button>
    <button data-tab="hitters" type="button">{hitter_label}</button>
  </nav>
</header>
<main>
  <div class="tab-panel" data-tab="pitchers">
{pitcher_body}
  </div>
  <div class="tab-panel" data-tab="hitters">
{hitter_body}
  </div>
</main>
<footer>
  Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} &middot;
  Pitchers: {p_counts['focus']} focus / {p_counts['investigate']} verify /
  {p_counts['noise']} noise / {p_counts['noline']} no-line &middot;
  Hitters: {h_counts['focus']} focus / {h_counts['investigate']} verify /
  {h_counts['noise']} noise / {h_counts['noline']} no-line
</footer>
</body>
</html>
"""

    out_path = OUTPUT_DIR / "index.html"
    out_path.write_text(doc)
    print(f"Wrote dashboard → {out_path}")
    return out_path


def main() -> None:
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        target = date.today()
    generate(target)


if __name__ == "__main__":
    main()
