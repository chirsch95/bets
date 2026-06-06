"""UD-aware suggester logic + the daily suggested-parlay snapshot.

Why this exists (2026-06-04): the UD Lab's suggested parlays are stateless
JS — recomputed from live data on every render. They churn intraday as
lineups post and multipliers move, so "what did the suggester recommend
when Chad bet?" could only be RECONSTRUCTED after the fact from the
last-saved board — contaminated by exactly that churn. This module:

  1. Holds the one Python source of truth for the UD suggester logic
     (ports of web.py's udCompare / udVerdict / udBuildLegs /
     udRankedCombos / udSelectParlays). grade_ud.py imports from here
     instead of carrying its own copy.
  2. Writes an append-only journal of the suggested cards whenever the
     pipeline refreshes or the UD board is bulk-saved, so every suggester
     state that mattered is frozen with a timestamp.

NOTE the same dual-implementation caveat as bets/parlay_suggest.py: the
JS suggester in web.py and these ports implement the same logic. Change
both when tuning either, or the snapshot will silently diverge from the
live dashboard.

Storage: data/ud_parlay_snaps_<date>.jsonl (gitignored, Air-canonical,
covered by the 3am data backup). One JSON object per line:
  {"ts": ISO-UTC, "trigger": "refresh"|"board-save", "two": [...], "three": [...]}
Each card: {prob, pay_mult, confirmed, ev, caution_unpriced, legs:[...]}.

Snapshots are NOT taken on single-cell edits (POST /api/ud-line) — only on
"Save all" and pipeline refreshes — so a board mid-edit doesn't spam the
journal with half-entered states.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from itertools import combinations

from . import calibration
from .config import DATA_DIR
from .model import prob_over_poisson

# Mirrored from web.py (and shared with grade_ud.py, which imports them).
# Floor raised 0.065 → 0.10 on 2026-06-06 — see live.py comment.
FOCUS_EDGE_MIN, FOCUS_EDGE_MAX, INVESTIGATE_EDGE = 0.10, 0.15, 0.20
MIN_LINE_FOR_FOCUS = 3.0
UD_PAYOUTS = {2: 3, 3: 6, 4: 10, 5: 20}
UD_PAYOUTS_BOOSTED = {2: 3.5, 3: 6.5}
UD_BOOSTED_CONFIRMED = {2: True, 3: True}
# Games within a 3-hr window (mirrors both suggesters): a wider gap means
# the later game's lineup won't post until after the first has started.
MAX_GAP_SECONDS = 3 * 3600
# Minimum model edge over UD's price for a leg to enter the parlay pool.
# Was 0.02; raised to the focus floor on 2026-06-06 — pool = the bettable
# band [FOCUS_EDGE_MIN, FOCUS_EDGE_MAX], nothing below the bar.
LEG_EDGE_MIN = FOCUS_EDGE_MIN


# ---------- model helpers (Python ports of the UD-Lab JS) -------------------
def cal_v2(raw_p):
    return calibration.apply(raw_p, "v2") if raw_p is not None else None


def implied_sharp_lambda(sportsbook_line, novig_over):
    """Invert the sharp no-vig P(over) at its own line → the Poisson mean it
    implies, so we can price the over at UD's line. Bisection, matches JS."""
    if sportsbook_line is None or novig_over is None:
        return None
    if not (0 < novig_over < 1):
        return None
    lo, hi = 0.01, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        p = prob_over_poisson(sportsbook_line, mid)
        if p < novig_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def ud_implied_over(hi, lo):
    if hi is None or lo is None or hi <= 0 or lo <= 0:
        return None
    ih, il = 1 / hi, 1 / lo
    return ih / (ih + il)


def mult_priced(hi, lo):
    h = 1.0 if hi is None else hi
    l = 1.0 if lo is None else lo
    return not (abs(h - 1) < 1e-9 and abs(l - 1) < 1e-9)


def ud_compare(proj, s_line, novig, ud_line, hi, lo):
    """Port of web.py udCompare. Returns dict or None."""
    if proj is None or ud_line is None:
        return None
    cal_ud_over = cal_v2(prob_over_poisson(ud_line, proj))
    lam = implied_sharp_lambda(s_line, novig)
    sharp_ud_over = prob_over_poisson(ud_line, lam) if lam is not None else None
    priced = mult_priced(hi, lo)
    ud_imp_over = ud_implied_over(hi, lo) if priced else None
    mkt_over = ud_imp_over if priced else sharp_ud_over
    edge_over = (cal_ud_over - mkt_over) if (cal_ud_over is not None and mkt_over is not None) else None
    if edge_over is not None:
        direction = "over" if edge_over >= 0 else "under"
    else:
        direction = "over" if (cal_ud_over is not None and cal_ud_over >= 0.5) else "under"
    pick_prob = None if cal_ud_over is None else (cal_ud_over if direction == "over" else 1 - cal_ud_over)
    mkt_prob = None if mkt_over is None else (mkt_over if direction == "over" else 1 - mkt_over)
    edge = (pick_prob - mkt_prob) if (pick_prob is not None and mkt_prob is not None) else None
    side_mult = (hi if direction == "over" else lo) if priced else 1.0
    line_edge = None
    if sharp_ud_over is not None and novig is not None:
        line_edge = (sharp_ud_over - novig) if direction == "over" else (novig - sharp_ud_over)
    return dict(ud_line=ud_line, s_line=s_line, proj=proj, hi=hi, lo=lo, priced=priced,
                cal_ud_over=cal_ud_over, sharp_ud_over=sharp_ud_over, ud_imp_over=ud_imp_over,
                mkt_over=mkt_over, dir=direction, pick_prob=pick_prob, mkt_prob=mkt_prob,
                edge=edge, side_mult=side_mult or 1.0, line_edge=line_edge)


def ud_verdict(c):
    """Port of web.py udVerdict → (label, cls, soft, bettable)."""
    if not c or c.get("edge") is None or not c.get("dir"):
        return ("—", "noise", False, False)
    soft = (not c["priced"]) and c["line_edge"] is not None and c["line_edge"] >= 0.02
    # Phantom-edge cap (2026-06-06): mirror the sportsbook focus band's 0.15
    # ceiling. Post-UD-switch, legs with claimed edge > 0.15 hit 15/40 (37%) —
    # when the model disagrees with the market this hard, the market usually
    # knows something the model doesn't (scratch risk, bullpen game, news).
    if c["edge"] > FOCUS_EDGE_MAX:
        return ("⚠ Investigate", "investigate", soft, False)
    # Floor raised to the focus band's 0.10 (2026-06-06): the old Bet >=0.05 /
    # Lean >=0.02 tiers sat below the bet bar — sub-0.10 legs hit 36–44%
    # all-time vs 62% for 0.10–0.15. Below the band: no bettable verdict.
    if c["edge"] >= FOCUS_EDGE_MIN:
        return (f"Bet {c['dir'].upper()}", "focus", soft, True)
    return ("—", "noise", soft, False)


def classify(edge):
    if edge is None:
        return "noline"
    a = abs(edge)
    if a >= INVESTIGATE_EDGE:
        return "investigate"
    if FOCUS_EDGE_MIN <= a <= FOCUS_EDGE_MAX:
        return "focus"
    return "noise"


def is_bettable_focus(cal_edge_v2, s_line):
    """Port of web.py isBettableFocus (uses cal_edge_v2 as pickEdge)."""
    if cal_edge_v2 is None or classify(cal_edge_v2) != "focus":
        return False
    return s_line is not None and s_line >= MIN_LINE_FOR_FOCUS


def graded_side_won(direction, line, actual_ks):
    """True/False/None(push or no actual) — did `direction` hit at `line`?"""
    if actual_ks is None or line is None:
        return None
    if abs(actual_ks - line) < 1e-9:
        return None  # push (integer lines only)
    over = actual_ks > line
    return over if direction == "over" else (not over)


def compute_calls(proj, s_line, novig, cal_edge_v2, ud_line, hi, lo, reliever, actual_ks):
    """One projection-state → both calls (UD-aware + sportsbook), graded.

    Used twice per pitcher: once on the frozen morning slate (pre-lineup,
    team-avg opp K%) and once on the post-lineup pre-game projection. Grades
    each call at its own line so the morning-vs-post-lineup comparison shows
    whether the lineup post changed the call and whether it was right."""
    c = ud_compare(proj, s_line, novig, ud_line, hi, lo)
    if reliever:
        ud_label, ud_bettable, ud_dir, ud_soft = ("RP — skip", False, None, False)
    else:
        ud_label, _, ud_soft, ud_bettable = ud_verdict(c)
        ud_dir = c["dir"] if (c and ud_bettable) else None
    sb_bettable = is_bettable_focus(cal_edge_v2, s_line)
    sb_dir = ("over" if cal_edge_v2 >= 0 else "under") if (sb_bettable and cal_edge_v2 is not None) else None
    return dict(
        proj=proj, s_line=s_line, novig=novig, cal_edge_v2=cal_edge_v2, c=c,
        ud_label=ud_label, ud_bettable=ud_bettable, ud_dir=ud_dir, ud_soft=ud_soft,
        sb_bettable=sb_bettable, sb_dir=sb_dir,
        ud_won=(graded_side_won(ud_dir, ud_line, actual_ks) if ud_bettable else None),
        sb_won=(graded_side_won(sb_dir, s_line, actual_ks) if sb_bettable else None),
        ud_edge=(c["edge"] if c else None))


# ---------- parlay construction (ports of web.py) ----------------------------
def ud_entry_base(k, any_priced):
    if not any_priced:
        return UD_PAYOUTS[k], True
    if k in UD_PAYOUTS_BOOSTED:
        return UD_PAYOUTS_BOOSTED[k], UD_BOOSTED_CONFIRMED.get(k, False)
    return UD_PAYOUTS[k] + 0.5, False


def ud_ranked_combos(legs, k):
    """All positive-EV k-leg combos (one leg per game, games within 3 hrs),
    ranked by EV. Legs may carry `game_time` (epoch seconds, optional) —
    combos missing times skip the window check, matching the JS."""
    if len(legs) < k:
        return []
    out = []
    for combo in combinations(legs, k):
        games = [l["game_pk"] for l in combo if l["game_pk"] is not None]
        if len(games) != len(set(games)):
            continue
        times = [l.get("game_time") for l in combo if l.get("game_time") is not None]
        if times and (max(times) - min(times) > MAX_GAP_SECONDS):
            continue
        any_priced = any(l["priced"] for l in combo)
        base, confirmed = ud_entry_base(k, any_priced)
        win_p = 1.0
        pay = base
        for l in combo:
            win_p *= l["prob"]
            pay *= (l["mult"] or 1.0)
        ev = win_p * pay - 1
        if ev <= 0:
            continue
        out.append(dict(legs=combo, prob=win_p, pay_mult=pay, confirmed=confirmed, ev=ev))
    return sorted(out, key=lambda x: -x["ev"])


def ud_select_parlays(legs):
    """Per-section pitcher cap (no pitcher twice within the 2-leg or 3-leg
    column), mirroring web.py udSelectParlays."""
    def pick(k, cap):
        used = set()
        out = []
        for x in ud_ranked_combos(legs, k):
            if len(out) >= cap:
                break
            if any(l["pid"] in used for l in x["legs"]):
                continue
            for l in x["legs"]:
                used.add(l["pid"])
            out.append(x)
        return out

    return dict(two=pick(2, 3), three=pick(3, 3))


# ---------- the snapshot ------------------------------------------------------
def _f(v):
    if v in ("", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_iso_epoch(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def snaps_path(target: date):
    return DATA_DIR / f"ud_parlay_snaps_{target.isoformat()}.jsonl"


def build_legs_from_csv_rows(rows, board):
    """Build the suggester's leg pool exactly like web.py udBuildLegs:
    EVERY slate row participates, with the saved board entry when present
    and the sportsbook-line / symmetric-multiplier prefill otherwise."""
    legs = []
    for r in rows:
        pid = str(r.get("pitcher_id") or "").strip()
        if not pid:
            continue
        e = board.get(pid)
        s_line = _f(r.get("line"))
        ud_line = e["line"] if (e and e.get("line") is not None) else s_line
        if ud_line is None or ud_line < MIN_LINE_FOR_FOCUS:
            continue  # no line, or reliever/opener gate
        hi = e.get("hi") if e else None
        lo = e.get("lo") if e else None
        c = ud_compare(_f(r.get("proj_ks_v2")), s_line, _f(r.get("novig_over")), ud_line, hi, lo)
        # Edge floor AND the focus band's 0.15 ceiling (phantom-edge cap, see
        # ud_verdict) — mirrors web.py udBuildLegs.
        if not c or c["pick_prob"] is None or not c["dir"] or c["edge"] is None or c["edge"] < LEG_EDGE_MIN or c["edge"] > FOCUS_EDGE_MAX:
            continue
        lj = (r.get("opp_lineup_json") or "").strip()
        try:
            gpk = int(r.get("game_pk")) if r.get("game_pk") not in (None, "") else None
        except (TypeError, ValueError):
            gpk = None
        legs.append(dict(
            pitcher=(r.get("pitcher") or "").strip(), pid=pid, dir=c["dir"],
            prob=c["pick_prob"], ud_line=c["ud_line"], mult=c["side_mult"] or 1.0,
            edge=c["edge"], priced=c["priced"], game_pk=gpk,
            game_time=_parse_iso_epoch(r.get("game_datetime_utc")),
            lineup_pending=not (lj and lj != "[]"),
        ))
    return sorted(legs, key=lambda l: -l["edge"])


def _serialize_card(x):
    return dict(
        prob=round(x["prob"], 4),
        pay_mult=round(x["pay_mult"], 4),
        confirmed=x["confirmed"],
        ev=round(x["ev"], 4),
        # Caution flag (replaced ⏰ place-early 2026-06-04): unpriced legs'
        # edge rests only on model-vs-sportsbook disagreement; early
        # tracking shows they underperform priced legs.
        caution_unpriced=sum(1 for l in x["legs"] if not l["priced"]),
        legs=[dict(pid=l["pid"], pitcher=l["pitcher"], dir=l["dir"],
                   line=l["ud_line"], mult=l["mult"], priced=l["priced"],
                   edge=round(l["edge"], 4), model_p=round(l["prob"], 4),
                   lineup_pending=l["lineup_pending"])
              for l in x["legs"]],
    )


def snapshot(target: date, trigger: str) -> dict | None:
    """Compute the UD Lab's current suggested cards (same inputs the
    dashboard uses: pin-overlaid live CSV + saved board) and append them
    to the day's journal. Returns the record, or None if there's nothing
    to snapshot (no slate, or no board saved yet — pre-board suggestions
    are pure sportsbook-prefill and not what Chad acts on)."""
    from . import live, ud_lines  # late import — avoid cycles at module load

    text = live.pinned_csv_text(target)
    if not text:
        return None
    board = ud_lines.load(target)
    if not board:
        return None
    rows = list(csv.DictReader(io.StringIO(text)))
    picks = ud_select_parlays(build_legs_from_csv_rows(rows, board))
    rec = dict(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        trigger=trigger,
        two=[_serialize_card(x) for x in picks["two"]],
        three=[_serialize_card(x) for x in picks["three"]],
    )
    path = snaps_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec
