#!/usr/bin/env python3
"""Blind-spot #1 + #4 harness: grade UD-aware picks vs sportsbook picks.

Run after a slate settles:  python3 grade_ud.py [YYYY-MM-DD]
(defaults to the most recent date that has BOTH a frozen slate and a
ud_lines_<date>.json).

What it does
------------
1. Loads the morning-frozen slate (proj_ks_v2, sportsbook line, novig, the
   production cal_edge_v2) and the hand-captured Underdog board
   (data/ud_lines_<date>.json: per-pid {line, hi, lo}).
2. Recomputes, per pitcher, BOTH calls using the *real* model functions
   (bets.model.prob_over_poisson + bets.calibration.apply, so the recompute
   reproduces production cal_edge_v2 at the sportsbook line exactly) and the
   UD-Lab JS logic ported 1:1 (de-vig multipliers → UD's own implied prob,
   else sharp-consensus at the UD line; reliever/opener gate at line<3.0):
     - SPORTSBOOK call  = isBettableFocus (cal_edge_v2 ∈ [0.065,0.15], line≥3)
     - UD-AWARE call    = udVerdict (model edge over UD's actual price)
3. If the slate is settled (actual_ks present), grades each call against the
   actual K total *at its own line* and reports the head-to-head: hit rates,
   where the two approaches disagree, and who was right on the disagreements.
4. Builds the UD-suggested parlays (same selection logic as the dashboard) and
   grades them, with realized return vs the EV the suggester claimed.
5. CLV (#4): if data/ud_lines_close_<date>.json exists (a closing-board
   snapshot — see capture_ud_close.py), reports per-pick closing-line value:
   did UD's price move toward the model's side between entry and close?

Standalone analysis tool — imports the bets package read-only, touches no
production state, writes nothing.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import calibration
from bets.config import DATA_DIR, OUTPUT_DIR
from bets.model import prob_over_poisson

# Constants mirrored from bets/live.py + web.py (kept in sync there).
FOCUS_EDGE_MIN, FOCUS_EDGE_MAX, INVESTIGATE_EDGE = 0.065, 0.15, 0.20
MIN_LINE_FOR_FOCUS = 3.0
UD_PAYOUTS = {2: 3, 3: 6, 4: 10, 5: 20}
UD_PAYOUTS_BOOSTED = {2: 3.5, 3: 6.5}
UD_BOOSTED_CONFIRMED = {2: True, 3: True}


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
    if c["edge"] >= 0.05:
        return (f"Bet {c['dir'].upper()}", "focus", soft, True)
    if c["edge"] >= 0.02:
        return (f"Lean {c['dir'].upper()}", "focus", soft, True)
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


# ---------- data loading ----------------------------------------------------
def _f(v):
    if v in ("", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_slate(target: date) -> dict:
    """{pid: row} from the frozen morning slate (preferred) else plain CSV."""
    for name in (f"pitcher_ks_{target.isoformat()}_slate.csv",
                 f"pitcher_ks_{target.isoformat()}.csv"):
        p = OUTPUT_DIR / name
        if p.exists():
            with p.open() as f:
                return {str(r["pitcher_id"]).strip(): r for r in csv.DictReader(f) if r.get("pitcher_id")}
    return {}


def load_actuals(target: date) -> dict:
    """{pid: {actual_ks, actual_bf, gs, over_hit}} from the settled CSV, or {}."""
    p = OUTPUT_DIR / f"pitcher_ks_{target.isoformat()}_settled.csv"
    if not p.exists():
        return {}
    out = {}
    with p.open() as f:
        for r in csv.DictReader(f):
            pid = str(r.get("pitcher_id", "")).strip()
            if not pid:
                continue
            out[pid] = dict(actual_ks=_f(r.get("actual_ks")), actual_bf=_f(r.get("actual_bf")),
                            gs=r.get("gs", ""), over_hit=r.get("over_hit", ""))
    return out


def load_ud(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out = {}
    for k, v in (raw or {}).items():
        if isinstance(v, dict):
            out[str(k)] = {f: _f(v.get(f)) for f in ("line", "hi", "lo")}
        else:
            out[str(k)] = {"line": _f(v), "hi": None, "lo": None}
    return out


def graded_side_won(direction, line, actual_ks):
    """True/False/None(push or no actual) — did `direction` hit at `line`?"""
    if actual_ks is None or line is None:
        return None
    if abs(actual_ks - line) < 1e-9:
        return None  # push (integer lines only)
    over = actual_ks > line
    return over if direction == "over" else (not over)


# ---------- the run ---------------------------------------------------------
def pick_date(arg: str | None) -> date | None:
    if arg:
        return date.fromisoformat(arg)
    # most recent date with both a slate and a ud_lines file
    cands = []
    for p in OUTPUT_DIR.glob("pitcher_ks_*_slate.csv"):
        ds = p.stem.split("_")[2]
        if (DATA_DIR / f"ud_lines_{ds}.json").exists():
            cands.append(ds)
    return date.fromisoformat(max(cands)) if cands else None


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    target = pick_date(arg)
    if target is None:
        print("No date with both a frozen slate and a ud_lines_<date>.json found.")
        print("Pass a date explicitly: python3 grade_ud.py 2026-05-29")
        return
    slate = load_slate(target)
    ud = load_ud(DATA_DIR / f"ud_lines_{target.isoformat()}.json")
    actuals = load_actuals(target)
    settled = bool(actuals)

    print(f"=== UD-aware grading — {target.isoformat()} ===")
    print(f"slate rows: {len(slate)}   UD-priced rows: {len(ud)}   "
          f"settled: {'YES' if settled else 'NO (staged — re-run after games)'}\n")

    rows = []  # per-pitcher comparison
    for pid, e in ud.items():
        s = slate.get(pid)
        if not s:
            continue
        proj = _f(s.get("proj_ks_v2"))
        s_line = _f(s.get("line"))
        novig = _f(s.get("novig_over"))
        cal_edge_v2 = _f(s.get("cal_edge_v2"))
        ud_line = e["line"] if e["line"] is not None else s_line
        reliever = ud_line is not None and ud_line < MIN_LINE_FOR_FOCUS

        # UD-aware call
        c = ud_compare(proj, s_line, novig, ud_line, e["hi"], e["lo"])
        if reliever:
            ud_label, ud_bettable, ud_dir, ud_soft = ("RP — skip", False, None, False)
        else:
            ud_label, _, ud_soft, ud_bettable = ud_verdict(c)
            ud_dir = c["dir"] if (c and ud_bettable) else None

        # Sportsbook call
        sb_bettable = is_bettable_focus(cal_edge_v2, s_line)
        sb_dir = ("over" if cal_edge_v2 >= 0 else "under") if (sb_bettable and cal_edge_v2 is not None) else None

        try:
            gpk = int(s.get("game_pk")) if s.get("game_pk") not in (None, "") else None
        except (TypeError, ValueError):
            gpk = None

        act = actuals.get(pid, {})
        actual_ks = act.get("actual_ks")
        ud_won = graded_side_won(ud_dir, ud_line, actual_ks) if ud_bettable else None
        sb_won = graded_side_won(sb_dir, s_line, actual_ks) if sb_bettable else None

        rows.append(dict(
            pid=pid, name=s.get("pitcher", ""), proj=proj, s_line=s_line, ud_line=ud_line,
            cal_edge_v2=cal_edge_v2, c=c, reliever=reliever, game_pk=gpk,
            ud_label=ud_label, ud_bettable=ud_bettable, ud_dir=ud_dir, ud_soft=ud_soft,
            sb_bettable=sb_bettable, sb_dir=sb_dir,
            actual_ks=actual_ks, gs=act.get("gs", ""), actual_bf=act.get("actual_bf"),
            ud_won=ud_won, sb_won=sb_won))

    # ---- Section A: per-pitcher table ----
    print("--- Per-pitcher calls (UD-priced rows) ---")
    hdr = f"{'pitcher':<20} {'sLn':>4} {'udLn':>4} {'calEdge':>7} {'udEdge':>7} {'SB':>10} {'UD':>14}"
    if settled:
        hdr += f" {'K':>3} {'SB✓':>4} {'UD✓':>4}"
    print(hdr)
    for r in sorted(rows, key=lambda x: -(x["c"]["edge"] if x["c"] and x["c"]["edge"] is not None else -9)):
        ce = f"{r['cal_edge_v2']:+.3f}" if r['cal_edge_v2'] is not None else "  —  "
        ue = f"{r['c']['edge']:+.3f}" if (r['c'] and r['c']['edge'] is not None) else "  —  "
        sb = (f"Bet {r['sb_dir'].upper()}" if r['sb_bettable'] else "—")
        line = (f"{r['name'][:20]:<20} {fmt(r['s_line']):>4} {fmt(r['ud_line']):>4} {ce:>7} {ue:>7} "
                f"{sb:>10} {(('★ ' if r['ud_soft'] else '')+r['ud_label']):>14}")
        if settled:
            k = f"{int(r['actual_ks'])}" if r['actual_ks'] is not None else "—"
            line += f" {k:>3} {tick(r['sb_won']):>4} {tick(r['ud_won']):>4}"
        print(line)

    # ---- Section B: head-to-head + disagreements (only if settled) ----
    if settled:
        graded_sb = [r for r in rows if r["sb_won"] is not None]
        graded_ud = [r for r in rows if r["ud_won"] is not None]
        print("\n--- Head-to-head hit rate (this slate) ---")
        rate("SPORTSBOOK focus picks", graded_sb, "sb_won")
        rate("UD-aware picks        ", graded_ud, "ud_won")

        disagree = [r for r in rows if (r["sb_bettable"] or r["ud_bettable"]) and
                    (r["sb_bettable"] != r["ud_bettable"] or r["sb_dir"] != r["ud_dir"])]
        print(f"\n--- Disagreements: SB vs UD ({len(disagree)}) — who was right? ---")
        for r in disagree:
            sb = f"SB:{('Bet '+r['sb_dir'].upper()) if r['sb_bettable'] else 'pass'}"
            udd = f"UD:{(r['ud_label']) if r['ud_bettable'] else 'pass'}"
            k = f"K={int(r['actual_ks'])}" if r['actual_ks'] is not None else "K=—"
            verdict = right_label(r)
            print(f"  {r['name'][:20]:<20} {sb:<14} {udd:<16} {k:<7} → {verdict}")

    # ---- Section C: UD parlays ----
    print("\n--- UD-suggested parlays ---")
    legs = build_ud_legs(rows)
    picks = ud_select_parlays(legs)
    for k, cards in (("2", picks["two"]), ("3", picks["three"])):
        for x in cards:
            grade_parlay_card(x, k, settled)
    if not picks["two"] and not picks["three"]:
        print("  (no positive-EV UD parlays from the priced board)")

    # ---- Section D: CLV (#4) ----
    print("\n--- Closing-line value (#4) ---")
    close_path = DATA_DIR / f"ud_lines_close_{target.isoformat()}.json"
    ud_close = load_ud(close_path)
    if not ud_close:
        print(f"  No closing board captured ({close_path.name} absent).")
        print("  Near first pitch, run:  python3 capture_ud_close.py <screenshot.png> ...")
    else:
        report_clv(rows, slate, ud, ud_close)


# ---------- parlay logic (ports of web.py) ----------------------------------
def build_ud_legs(rows):
    legs = []
    for r in rows:
        if r["reliever"] or r["ud_line"] is None or r["ud_line"] < MIN_LINE_FOR_FOCUS:
            continue
        c = r["c"]
        if not c or c["pick_prob"] is None or not c["dir"] or c["edge"] is None or c["edge"] < 0.02:
            continue
        legs.append(dict(pitcher=r["name"], pid=r["pid"], dir=c["dir"], prob=c["pick_prob"],
                         ud_line=c["ud_line"], mult=c["side_mult"] or 1.0, edge=c["edge"],
                         priced=c["priced"], soft=r["ud_soft"], game_pk=r.get("game_pk"),
                         won=r["ud_won"]))
    return sorted(legs, key=lambda l: -l["edge"])


def ud_entry_base(k, any_priced):
    if not any_priced:
        return UD_PAYOUTS[k], True
    if k in UD_PAYOUTS_BOOSTED:
        return UD_PAYOUTS_BOOSTED[k], UD_BOOSTED_CONFIRMED.get(k, False)
    return UD_PAYOUTS[k] + 0.5, False


def ud_ranked_combos(legs, k):
    if len(legs) < k:
        return []
    out = []
    for combo in combinations(legs, k):
        games = [l["game_pk"] for l in combo if l["game_pk"] is not None]
        if len(games) != len(set(games)):
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
    used = set()

    def pick(k, cap):
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


def grade_parlay_card(x, k, settled):
    early = x["ev"] >= 0.10 and any(l["soft"] for l in x["legs"])
    tag = "⏰" if early else "  "
    conf = "" if x["confirmed"] else "~"
    legdesc = ", ".join(f"{l['pitcher'].split()[-1]} {l['dir'].upper()} {l['ud_line']}"
                        + (f"@{l['mult']}" if l["mult"] != 1 else "") for l in x["legs"])
    head = f"  {tag} {k}-leg {conf}{x['pay_mult']:.2f}× · win {x['prob']*100:.1f}% · EV {x['ev']:+.3f}"
    if settled:
        wons = [l["won"] for l in x["legs"]]
        if any(w is None for w in wons):
            outcome = "ungraded (missing actual)"
        elif all(wons):
            outcome = f"WON → +{x['pay_mult']-1:.2f}u"
        else:
            outcome = "LOST → -1.00u"
        head += f"  ⇒ {outcome}"
    print(head)
    print(f"       {legdesc}")


# ---------- CLV (#4) --------------------------------------------------------
def market_pick_prob(direction, proj, s_line, novig, ud_entry):
    """Market's implied prob of `direction` from a UD board entry: UD's own
    de-vigged prob if priced, else sharp-consensus at that UD line."""
    ud_line = ud_entry["line"] if ud_entry and ud_entry["line"] is not None else s_line
    if ud_line is None:
        return None, None
    priced = mult_priced(ud_entry.get("hi") if ud_entry else None,
                         ud_entry.get("lo") if ud_entry else None)
    if priced:
        over = ud_implied_over(ud_entry["hi"], ud_entry["lo"])
    else:
        lam = implied_sharp_lambda(s_line, novig)
        over = prob_over_poisson(ud_line, lam) if lam is not None else None
    if over is None:
        return None, ud_line
    return (over if direction == "over" else 1 - over), ud_line


def report_clv(rows, slate, ud_entry_board, ud_close_board):
    """Per-pick CLV: did the market's implied prob of the model's side rise
    from entry to close? Positive = you bought before the market moved your way."""
    clvs = []
    for r in rows:
        if not r["ud_bettable"] or not r["ud_dir"]:
            continue
        s = slate.get(r["pid"], {})
        proj, s_line, novig = r["proj"], r["s_line"], _f(s.get("novig_over"))
        entry = ud_entry_board.get(r["pid"])
        close = ud_close_board.get(r["pid"])
        if not close:
            continue
        p_entry, l_entry = market_pick_prob(r["ud_dir"], proj, s_line, novig, entry)
        p_close, l_close = market_pick_prob(r["ud_dir"], proj, s_line, novig, close)
        if p_entry is None or p_close is None:
            continue
        clv = p_close - p_entry
        clvs.append(clv)
        moved = "→ toward" if clv > 1e-4 else ("→ against" if clv < -1e-4 else "→ flat")
        print(f"  {r['name'][:20]:<20} {r['ud_dir'].upper():<5} "
              f"entry {l_entry}/{p_entry*100:4.1f}%  close {l_close}/{p_close*100:4.1f}%  "
              f"CLV {clv*100:+5.1f}pp {moved}")
    if clvs:
        beat = sum(1 for c in clvs if c > 1e-4)
        print(f"\n  beat-the-close: {beat}/{len(clvs)} = {beat/len(clvs):.0%}   "
              f"mean CLV {sum(clvs)/len(clvs)*100:+.2f}pp")
        print("  (CLV is market-confirmed edge independent of the K outcome — "
              "the low-variance signal #3 showed the win/loss ledger can't give.)")


# ---------- formatting ------------------------------------------------------
def fmt(v):
    return "—" if v is None else (f"{v:g}")


def tick(won):
    return "—" if won is None else ("✓" if won else "✗")


def rate(label, graded, key):
    n = len(graded)
    if not n:
        print(f"  {label}: no graded picks")
        return
    w = sum(1 for r in graded if r[key])
    print(f"  {label}: {w}/{n} = {w/n:.1%}")


def right_label(r):
    if r["actual_ks"] is None:
        return "ungraded"
    parts = []
    if r["sb_bettable"]:
        parts.append("SB " + ("✓" if r["sb_won"] else "✗"))
    else:
        # would the SB-passed side have won had it bet the UD side's line?
        parts.append("SB passed")
    if r["ud_bettable"]:
        parts.append("UD " + ("✓" if r["ud_won"] else "✗"))
    else:
        parts.append("UD passed")
    return "  ".join(parts)


if __name__ == "__main__":
    main()
