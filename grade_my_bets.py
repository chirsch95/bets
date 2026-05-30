#!/usr/bin/env python3
"""Your actual betting record — results, ROI confidence, and where the bet bar
should be. Reads the ledger (data/users/<user>/bets.json), grades every leg
against actual strikeouts, and buckets legs by the model's edge at bet time so
you can see which edge ranges actually clear Underdog's breakeven.

    .venv/bin/python grade_my_bets.py [user]      # default: chad

No new capture — uses the bet-time snapshot already stored on each leg plus the
settled slate CSVs. Run it whenever; it sharpens as bets accumulate. This is
the low-variance, effort-free alternative to closing screenshots: instead of
asking "did the line move my way," it asks "do my picks actually hit, and where."
"""
from __future__ import annotations

import csv
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets.config import DATA_DIR, OUTPUT_DIR

random.seed(20260530)

# Underdog per-leg breakeven (all-must-hit): 2-leg @3× = 57.7%, 3-leg @6× = 55.0%.
UD_BREAKEVEN_2, UD_BREAKEVEN_3 = 0.577, 0.550


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_actuals_by_date():
    """{date: {pid: actual_ks}} from every settled slate CSV."""
    out = {}
    for p in OUTPUT_DIR.glob("pitcher_ks_*_settled.csv"):
        ds = p.stem.split("_")[2]
        m = {}
        with p.open() as f:
            for r in csv.DictReader(f):
                pid = str(r.get("pitcher_id", "")).strip()
                ks = _f(r.get("actual_ks"))
                if pid and ks is not None:
                    m[pid] = ks
        out[ds] = m
    return out


def leg_hit(ou, line, actual):
    if actual is None or line is None:
        return None
    if abs(actual - line) < 1e-9:
        return None  # push
    over = actual > line
    return over if ou.upper().startswith("O") else (not over)


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else "chad"
    ledger = DATA_DIR / "users" / user / "bets.json"
    if not ledger.exists():
        print(f"No ledger for user '{user}' at {ledger}")
        return
    bets = json.loads(ledger.read_text()).get("bets", [])
    settled = [b for b in bets if b.get("result") in ("W", "L", "w", "l")]
    actuals = load_actuals_by_date()

    print(f"=== {user}'s betting record — {len(settled)} settled bets ===\n")

    # ---- 1. ROI + confidence (the #3 analysis, auto-updating) ----
    def roi_block(rows, label):
        n = len(rows)
        if not n:
            print(f"  {label}: none")
            return
        staked = sum(b["stake"] for b in rows)
        returned = sum(b.get("payout", 0) for b in rows)
        wins = sum(1 for b in rows if str(b["result"]).lower() == "w")
        print(f"  {label}: {n} bets, {wins}/{n}={wins/n:.0%} won, "
              f"staked ${staked:.0f} → ${returned:.0f}, ROI {(returned-staked)/staked:+.1%}")
        return rows

    paid = [b for b in settled if not b.get("free_entry")]
    print("PROFITABILITY")
    roi_block(settled, "all (incl promos)")
    roi_block(paid, "paid only       ")
    # bootstrap 95% CI on paid ROI — is it distinguishable from zero?
    if paid:
        def agg(rs):
            s = sum(b["stake"] for b in rs); r = sum(b.get("payout", 0) for b in rs)
            return (r - s) / s if s else 0.0
        B, n = 20000, len(paid)
        boots = sorted(agg([paid[random.randrange(n)] for _ in range(n)]) for _ in range(B))
        lo, hi = boots[int(0.025 * B)], boots[int(0.975 * B)]
        p0 = sum(1 for x in boots if x <= 0) / B
        print(f"    paid ROI 95% CI: [{lo:+.0%}, {hi:+.0%}]   P(true ROI ≤ 0) = {p0:.0%}")
        if lo <= 0 <= hi:
            print("    → not yet distinguishable from zero (need more bets to confirm an edge).")

    # ---- 2. Per-leg hit rate, graded vs actuals ----
    legs = []  # (supporting_edge_or_None, hit_bool)
    graded = ungraded = 0
    for b in settled:
        ds = b.get("date")
        amap = actuals.get(ds, {})
        for l in b.get("legs", []):
            pid = str(l.get("pitcher_id", "")).strip()
            hit = leg_hit(l.get("ou", ""), _f(l.get("line")), amap.get(pid))
            if hit is None:
                ungraded += 1
                continue
            graded += 1
            se = _f(l.get("slate_edge"))
            # edge supporting the side actually bet (over edge is +, under is −)
            supp = None if se is None else (se if str(l.get("ou", "")).upper().startswith("O") else -se)
            legs.append((supp, hit))

    print("\nPER-LEG HIT RATE (graded vs actual strikeouts)")
    hits = sum(1 for _, h in legs if h)
    if legs:
        print(f"  all legs: {hits}/{len(legs)} = {hits/len(legs):.1%}   "
              f"(UD needs ~{UD_BREAKEVEN_3:.0%}–{UD_BREAKEVEN_2:.0%} per leg to profit)")
    if ungraded:
        print(f"  ({ungraded} legs couldn't be graded — no settled actual on file)")

    # ---- 3. Bucket by model edge at bet time → where's the bar? ----
    withedge = [(e, h) for e, h in legs if e is not None]
    print(f"\nBY MODEL EDGE AT BET TIME ({len(withedge)} legs have a captured edge)")
    if withedge:
        bands = [(-9, 0.065, "below bar  (<0.065)"),
                 (0.065, 0.10, "0.065–0.10 (low bar)"),
                 (0.10, 0.15, "0.10–0.15  (bar core)"),
                 (0.15, 0.20, "0.15–0.20  (high)"),
                 (0.20, 9, "≥0.20      (investigate)")]
        print(f"  {'edge band':<24}{'legs':>5}{'hit':>8}   vs UD breakeven")
        for lo_b, hi_b, name in bands:
            sub = [h for e, h in withedge if lo_b <= e < hi_b]
            if not sub:
                continue
            hr = sum(sub) / len(sub)
            flag = "✓ above" if hr >= UD_BREAKEVEN_3 else "✗ below"
            print(f"  {name:<24}{len(sub):>5}{hr:>7.0%}   {flag}")
        print("  (Tune the bet bar toward the bands that clear breakeven — once each has enough legs.)")


if __name__ == "__main__":
    main()
