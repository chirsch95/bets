#!/usr/bin/env python3
"""Your actual betting record — results, ROI confidence, and where the bet bar
should be. Thin CLI over bets.bet_record (the same logic the UD Lab "My record"
button calls), so the terminal and the dashboard always agree.

    .venv/bin/python grade_my_bets.py [user]      # default: chad

No new capture — grades every ledger leg against actual strikeouts and buckets
legs by the model's edge at bet time. Run it whenever; it sharpens as bets
accumulate. The low-variance, effort-free alternative to closing screenshots.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import bet_record


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else "chad"
    r = bet_record.compute(user)
    print(f"=== {user}'s betting record — {r['n_settled']} settled bets ===\n")

    print("PROFITABILITY")
    for key, label in (("all", "all (incl promos)"), ("paid", "paid only       ")):
        d = r[key]
        if not d:
            print(f"  {label}: none"); continue
        roi = f"{d['roi']:+.1%}" if d["roi"] is not None else "—"
        print(f"  {label}: {d['n']} bets, {d['wins']}/{d['n']}={d['win_rate']:.0%} won, "
              f"staked ${d['staked']:.0f} → ${d['returned']:.0f}, ROI {roi}")
    ci = r["paid_ci"]
    if ci:
        print(f"    paid ROI 95% CI: [{ci['lo']:+.0%}, {ci['hi']:+.0%}]   "
              f"P(true ROI ≤ 0) = {ci['p_zero']:.0%}")
        if ci["spans_zero"]:
            print("    → not yet distinguishable from zero (need more bets to confirm an edge).")

    print("\nBY BUCKET (three-bucket policy)")
    labels = {"focus": "EDGE (focus)", "boost": "BOOST", "default": "legacy/untagged",
              "free": "FREE credits", "fun": "FUN budget"}
    for bk in ("focus", "boost", "default", "free", "fun"):
        d = r["buckets"].get(bk)
        if not d:
            continue
        roi = f"{d['roi']:+.0%}" if d["roi"] is not None else "—"
        cost = "$0 (house)" if bk == "free" else f"${d['staked']:.0f}"
        tail = "  [walled off from edge ROI]" if bk == "fun" else ""
        print(f"  {labels[bk]:16}: {d['n']:3} bets, {d['wins']}/{d['n']}={d['win_rate']:.0%} won, "
              f"{cost} → ${d['returned']:.0f}, ROI {roi}{tail}")
    fun = r["fun"]
    if fun["n"]:
        wk = sorted(fun["by_week"])
        latest = f"  (latest week {wk[-1]}: ${fun['by_week'][wk[-1]]:.0f}/$15)" if wk else ""
        print(f"  fun-budget spend: ${fun['spend']:.0f} → ${fun['returned']:.0f} "
              f"(net ${fun['net']:+.0f}, entertainment — not an edge claim){latest}")

    leg = r["leg"]
    be = r["breakeven"]
    print("\nPER-LEG HIT RATE (graded vs actual strikeouts)")
    if leg["rate"] is not None:
        print(f"  all legs: {leg['hits']}/{leg['graded']} = {leg['rate']:.1%}   "
              f"(UD needs ~{be['leg3']:.0%}–{be['leg2']:.0%} per leg to profit)")
    if leg["ungraded"]:
        print(f"  ({leg['ungraded']} legs couldn't be graded — no settled actual on file)")

    print(f"\nBY MODEL EDGE AT BET TIME")
    if r["bands"]:
        print(f"  {'edge band':<24}{'legs':>5}{'hit':>8}   vs UD breakeven")
        for b in r["bands"]:
            flag = "✓ above" if b["above_breakeven"] else "✗ below"
            print(f"  {b['label']:<24}{b['n']:>5}{b['rate']:>7.0%}   {flag}")
        print("  (Tune the bet bar toward the bands that clear breakeven — once each has enough legs.)")
    else:
        print("  (no legs with a captured edge yet)")


if __name__ == "__main__":
    main()
