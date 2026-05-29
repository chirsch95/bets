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


def load_postlineup(target: date) -> dict:
    """{pid: {proj_ks_v2, line, novig_over, cal_edge_v2}} from the settled
    CSV's *unprefixed* (live) columns — the last pre-game pipeline run, i.e.
    the post-lineup projection if the pipeline was re-run after lineups
    posted. Empty if not settled. (The slate_* columns hold the morning
    state but omit proj_ks_v2, so the morning view must come from the frozen
    _slate.csv — that's why this is a separate source.)"""
    p = OUTPUT_DIR / f"pitcher_ks_{target.isoformat()}_settled.csv"
    if not p.exists():
        return {}
    out = {}
    with p.open() as f:
        for r in csv.DictReader(f):
            pid = str(r.get("pitcher_id", "")).strip()
            if not pid:
                continue
            out[pid] = dict(proj_ks_v2=_f(r.get("proj_ks_v2")), line=_f(r.get("line")),
                            novig_over=_f(r.get("novig_over")), cal_edge_v2=_f(r.get("cal_edge_v2")),
                            opp_k_source=r.get("opp_k_source", ""))
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
    postlineup = load_postlineup(target)
    settled = bool(actuals)
    have_post = bool(postlineup)
    bet_view = "post" if have_post else "morn"  # what Chad actually bet (post-lineup if re-run)

    print(f"=== UD-aware grading — {target.isoformat()} ===")
    print(f"slate rows: {len(slate)}   UD-priced rows: {len(ud)}   "
          f"settled: {'YES' if settled else 'NO (staged — re-run after games)'}")
    if have_post:
        lc = sum(1 for v in postlineup.values() if v.get("opp_k_source") == "lineup")
        print(f"post-lineup projection: present ({lc}/{len(postlineup)} rows lineup-confirmed)")
    else:
        print("post-lineup projection: not available "
              "(only after settle, and only if the pipeline was re-run post-lineup)")
    print()

    rows = []  # per-pitcher comparison, each with morning + post-lineup views
    for pid, e in ud.items():
        s = slate.get(pid)
        if not s:
            continue
        ud_line = e["line"] if e["line"] is not None else _f(s.get("line"))
        reliever = ud_line is not None and ud_line < MIN_LINE_FOR_FOCUS
        try:
            gpk = int(s.get("game_pk")) if s.get("game_pk") not in (None, "") else None
        except (TypeError, ValueError):
            gpk = None
        actual_ks = actuals.get(pid, {}).get("actual_ks")

        # Morning view (frozen slate: pre-lineup, team-avg opp K%).
        morn = compute_calls(_f(s.get("proj_ks_v2")), _f(s.get("line")), _f(s.get("novig_over")),
                             _f(s.get("cal_edge_v2")), ud_line, e["hi"], e["lo"], reliever, actual_ks)
        # Post-lineup view (last pre-game run), if available.
        post = None
        if have_post and pid in postlineup:
            pl = postlineup[pid]
            post = compute_calls(pl["proj_ks_v2"], pl["line"], pl["novig_over"], pl["cal_edge_v2"],
                                 ud_line, e["hi"], e["lo"], reliever, actual_ks)
            post["opp_k_source"] = pl.get("opp_k_source", "")

        rows.append(dict(pid=pid, name=s.get("pitcher", ""), ud_line=ud_line, hi=e["hi"], lo=e["lo"],
                         reliever=reliever, game_pk=gpk, actual_ks=actual_ks,
                         gs=actuals.get(pid, {}).get("gs", ""), morn=morn, post=post))

    def view(r):
        return r[bet_view] or r["morn"]

    # ---- Section A: per-pitcher table, morning vs post-lineup side by side ----
    label_morn = "MORNING (pre-lineup)"
    print(f"--- Per-pitcher calls: {label_morn}  |  POST-LINEUP (bet-time) ---")
    hdr = (f"{'pitcher':<20} {'udLn':>4} | {'m·udE':>6} {'m·SB':>9} {'m·UD':>13}"
           f" | {'p·udE':>6} {'p·SB':>9} {'p·UD':>13}")
    if settled:
        hdr += f" | {'K':>3} {'m✓':>4} {'p✓':>4}"
    print(hdr)
    for r in sorted(rows, key=lambda x: -(view(x)["ud_edge"] if view(x)["ud_edge"] is not None else -9)):
        cells = f"{r['name'][:20]:<20} {fmt(r['ud_line']):>4} |"
        cells += " " + viewcells(r["morn"])
        cells += " | " + (viewcells(r["post"]) if r["post"] else f"{'—':>6} {'—':>9} {'—':>13}")
        if settled:
            k = f"{int(r['actual_ks'])}" if r['actual_ks'] is not None else "—"
            mp = r["morn"]; pp = r["post"]
            cells += f" | {k:>3} {tick(combined_won(mp)):>4} {tick(combined_won(pp) if pp else None):>4}"
        print(cells)

    # ---- Section B: head-to-head + how the lineup moved the call (settled) ----
    if settled:
        print("\n--- Head-to-head hit rate (this slate) ---")
        for vname, vkey in (("MORNING (pre-lineup)", "morn"), ("POST-LINEUP (bet-time)", "post")):
            if vkey == "post" and not have_post:
                print(f"  {vname}: n/a (no post-lineup run captured)")
                continue
            sb = [r for r in rows if r[vkey] and r[vkey]["sb_won"] is not None]
            udd = [r for r in rows if r[vkey] and r[vkey]["ud_won"] is not None]
            print(f"  {vname}:")
            rate("    sportsbook focus picks", sb, vkey, "sb_won")
            rate("    UD-aware picks        ", udd, vkey, "ud_won")
        if have_post:
            print(f"\n  (bet-time = POST-LINEUP — what you'd actually have bet after re-running.)")

        # Where did the lineup post change the UD call?
        if have_post:
            moved = [r for r in rows if call_str(r["morn"]) != call_str(r["post"])]
            print(f"\n--- Lineup post changed the UD call ({len(moved)}) ---")
            for r in moved:
                k = f"K={int(r['actual_ks'])}" if r['actual_ks'] is not None else "K=—"
                print(f"  {r['name'][:20]:<20} morn {call_str(r['morn']):<14} → post {call_str(r['post']):<14}"
                      f"  src={r['post'].get('opp_k_source','')}  {k}")
            if not moved:
                print("  (none — lineups didn't change any UD call)")

        # SB vs UD disagreement on the bet-time view.
        bt = bet_view
        disagree = [r for r in rows if r[bt] and (r[bt]["sb_bettable"] or r[bt]["ud_bettable"]) and
                    (r[bt]["sb_bettable"] != r[bt]["ud_bettable"] or r[bt]["sb_dir"] != r[bt]["ud_dir"])]
        print(f"\n--- SB vs UD disagreements ({bet_view}) ({len(disagree)}) — who was right? ---")
        for r in disagree:
            v = r[bt]
            sb = f"SB:{('Bet '+v['sb_dir'].upper()) if v['sb_bettable'] else 'pass'}"
            udd = f"UD:{(v['ud_label']) if v['ud_bettable'] else 'pass'}"
            k = f"K={int(r['actual_ks'])}" if r['actual_ks'] is not None else "K=—"
            print(f"  {r['name'][:20]:<20} {sb:<14} {udd:<16} {k:<7} → {right_label(v)}")

    # ---- Section C: UD parlays (built from the bet-time view) ----
    print(f"\n--- UD-suggested parlays ({bet_view}-view) ---")
    legs = build_ud_legs(rows, bet_view)
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
        # bet-time → close: the value you personally captured (live entry board).
        report_clv(rows, slate, ud, ud_close, bet_view, "bet-time → close (captured)")
        # morning → close: did UD move toward the side the model flagged at open?
        # A model-detection signal independent of when you bet. Only if the
        # morning baseline was preserved AND differs from the bet-time board.
        ud_morning = load_ud(DATA_DIR / f"ud_lines_{target.isoformat()}_morning.json")
        if ud_morning and ud_morning != ud:
            print()
            report_clv(rows, slate, ud_morning, ud_close, bet_view, "morning → close (model detection)")


# ---------- parlay logic (ports of web.py) ----------------------------------
def build_ud_legs(rows, bet_view):
    legs = []
    for r in rows:
        v = r.get(bet_view) or r["morn"]
        if r["reliever"] or r["ud_line"] is None or r["ud_line"] < MIN_LINE_FOR_FOCUS:
            continue
        c = v["c"]
        if not c or c["pick_prob"] is None or not c["dir"] or c["edge"] is None or c["edge"] < 0.02:
            continue
        legs.append(dict(pitcher=r["name"], pid=r["pid"], dir=c["dir"], prob=c["pick_prob"],
                         ud_line=c["ud_line"], mult=c["side_mult"] or 1.0, edge=c["edge"],
                         priced=c["priced"], soft=v["ud_soft"], game_pk=r.get("game_pk"),
                         won=v["ud_won"]))
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


def report_clv(rows, slate, ud_entry_board, ud_close_board, bet_view, label):
    """Per-pick CLV: did the market's implied prob of the model's side rise
    from `entry` to close? Positive = the market moved toward your side after
    you got in. Picks taken from the bet-time view (post-lineup if available).
    `label` distinguishes morning→close (model detection) from bet-time→close
    (the value you personally captured)."""
    print(f"  [{label}]")
    clvs = []
    for r in rows:
        v = r.get(bet_view) or r["morn"]
        if not v["ud_bettable"] or not v["ud_dir"]:
            continue
        s = slate.get(r["pid"], {})
        proj, s_line, novig = v["proj"], v["s_line"], _f(s.get("novig_over"))
        entry = ud_entry_board.get(r["pid"])
        close = ud_close_board.get(r["pid"])
        if not close:
            continue
        p_entry, l_entry = market_pick_prob(v["ud_dir"], proj, s_line, novig, entry)
        p_close, l_close = market_pick_prob(v["ud_dir"], proj, s_line, novig, close)
        if p_entry is None or p_close is None:
            continue
        clv = p_close - p_entry
        clvs.append(clv)
        moved = "→ toward" if clv > 1e-4 else ("→ against" if clv < -1e-4 else "→ flat")
        print(f"  {r['name'][:20]:<20} {v['ud_dir'].upper():<5} "
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


def _wl(won):
    return "push" if won is None else ("✓" if won else "✗")


def viewcells(v):
    """Format one view as 'udEdge  SB-call  UD-call' (matches Section A header)."""
    ue = f"{v['ud_edge']:+.3f}" if v['ud_edge'] is not None else "—"
    sb = (f"Bet {v['sb_dir'].upper()}" if v['sb_bettable'] else "—")
    ud = (("★ " if v['ud_soft'] else "") + v['ud_label'])
    return f"{ue:>6} {sb:>9} {ud:>13}"


def combined_won(v):
    """Per-pitcher ✓/✗ for the table: the UD-aware call's result (the focus)."""
    return v["ud_won"] if v else None


def call_str(v):
    """Short UD-call string for the 'lineup changed the call' diff."""
    if not v:
        return "—"
    return (("★ " if v['ud_soft'] and v['ud_bettable'] else "") + v['ud_label'])


def rate(label, graded, vkey, key):
    n = len(graded)
    if not n:
        print(f"  {label}: no graded picks")
        return
    w = sum(1 for r in graded if r[vkey][key])
    print(f"  {label}: {w}/{n} = {w/n:.1%}")


def right_label(v):
    sb = ("SB " + _wl(v["sb_won"])) if v["sb_bettable"] else "SB passed"
    ud = ("UD " + _wl(v["ud_won"])) if v["ud_bettable"] else "UD passed"
    return "  ".join([sb, ud])


if __name__ == "__main__":
    main()
