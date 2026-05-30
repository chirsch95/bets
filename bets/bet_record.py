"""Compute a user's betting record + edge-band tuning report.

Shared by the grade_my_bets.py CLI and the GET /api/bet-record endpoint that
backs the UD Lab "My record" panel. No new capture: grades every ledger leg
against actual strikeouts (settled slate CSVs) and buckets legs by the model's
edge at bet time (the slate_edge stored on each leg). The low-variance,
effort-free confidence/tuning signal that replaced closing-screenshot CLV.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

from .config import OUTPUT_DIR
from . import wagers

# Underdog per-leg breakeven (all-must-hit): 2-leg @3× = 57.7%, 3-leg @6× = 55.0%.
UD_BREAKEVEN_2, UD_BREAKEVEN_3 = 0.577, 0.550

_BANDS = [
    (-9.0, 0.065, "below bar (<0.065)"),
    (0.065, 0.10, "0.065–0.10 (low)"),
    (0.10, 0.15, "0.10–0.15 (core)"),
    (0.15, 0.20, "0.15–0.20 (high)"),
    (0.20, 9.0, "≥0.20 (investigate)"),
]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _actuals_by_date() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for p in OUTPUT_DIR.glob("pitcher_ks_*_settled.csv"):
        ds = p.stem.split("_")[2]
        m: dict[str, float] = {}
        with p.open() as f:
            for r in csv.DictReader(f):
                pid = str(r.get("pitcher_id", "")).strip()
                ks = _f(r.get("actual_ks"))
                if pid and ks is not None:
                    m[pid] = ks
        out[ds] = m
    return out


def _leg_hit(ou, line, actual):
    if actual is None or line is None:
        return None
    if abs(actual - line) < 1e-9:
        return None  # push
    over = actual > line
    return over if str(ou).upper().startswith("O") else (not over)


def _roi(rows):
    n = len(rows)
    if not n:
        return None
    staked = sum(b.get("stake", 0) for b in rows)
    returned = sum(b.get("payout", 0) for b in rows)
    wins = sum(1 for b in rows if str(b.get("result", "")).lower() == "w")
    return {
        "n": n, "wins": wins, "win_rate": wins / n,
        "staked": round(staked, 2), "returned": round(returned, 2),
        "roi": (returned - staked) / staked if staked else None,
    }


def compute(user_id: str, bootstrap: int = 10000) -> dict:
    """Return the structured betting-record report for one user."""
    bets = wagers.load_bets(user_id).get("bets", [])
    settled = [b for b in bets if str(b.get("result", "")).lower() in ("w", "l")]
    paid = [b for b in settled if not b.get("free_entry")]
    actuals = _actuals_by_date()

    # Per-leg grading + edge buckets.
    legs = []  # (supporting_edge_or_None, hit_bool)
    ungraded = 0
    for b in settled:
        amap = actuals.get(b.get("date"), {})
        for l in b.get("legs", []):
            pid = str(l.get("pitcher_id", "")).strip()
            hit = _leg_hit(l.get("ou", ""), _f(l.get("line")), amap.get(pid))
            if hit is None:
                ungraded += 1
                continue
            se = _f(l.get("slate_edge"))
            supp = None if se is None else (se if str(l.get("ou", "")).upper().startswith("O") else -se)
            legs.append((supp, hit))

    graded = len(legs)
    leg_hits = sum(1 for _, h in legs if h)
    bands = []
    for lo_b, hi_b, label in _BANDS:
        sub = [h for e, h in legs if e is not None and lo_b <= e < hi_b]
        if not sub:
            continue
        rate = sum(sub) / len(sub)
        bands.append({"label": label, "n": len(sub), "rate": rate,
                      "above_breakeven": rate >= UD_BREAKEVEN_3})

    # Bootstrap CI on paid ROI — is it distinguishable from zero?
    paid_ci = None
    if paid:
        rng = random.Random(20260530)  # fixed seed → stable CI across calls

        def agg(rs):
            s = sum(b.get("stake", 0) for b in rs)
            r = sum(b.get("payout", 0) for b in rs)
            return (r - s) / s if s else 0.0

        n = len(paid)
        boots = sorted(agg([paid[rng.randrange(n)] for _ in range(n)]) for _ in range(bootstrap))
        lo = boots[int(0.025 * bootstrap)]
        hi = boots[int(0.975 * bootstrap)]
        p_zero = sum(1 for x in boots if x <= 0) / bootstrap
        paid_ci = {"lo": lo, "hi": hi, "p_zero": p_zero,
                   "spans_zero": lo <= 0 <= hi}

    return {
        "n_settled": len(settled),
        "all": _roi(settled),
        "paid": _roi(paid),
        "paid_ci": paid_ci,
        "leg": {"graded": graded, "hits": leg_hits,
                "rate": (leg_hits / graded) if graded else None, "ungraded": ungraded},
        "breakeven": {"leg2": UD_BREAKEVEN_2, "leg3": UD_BREAKEVEN_3},
        "bands": bands,
    }
