"""Calibration analysis across all settled projection files.

Run with:
    python -m bets.analyze
"""

from __future__ import annotations

import csv
import math
from statistics import mean

from tabulate import tabulate

from .config import OUTPUT_DIR


def _f(value) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_all_settled() -> list[dict]:
    """Load every *_settled.csv into a flat list, dropping no-shows."""
    rows = []
    for path in sorted(OUTPUT_DIR.glob("pitcher_ks_*_settled.csv")):
        with path.open() as f:
            for r in csv.DictReader(f):
                if _f(r.get("actual_ks")) is None:
                    continue
                rows.append(r)
    return rows


def projection_accuracy(rows: list[dict]) -> None:
    series = {
        "v0": [x for x in (_f(r.get("error_v0")) for r in rows) if x is not None],
        "v1": [x for x in (_f(r.get("error_v1")) for r in rows) if x is not None],
        "v2": [x for x in (_f(r.get("error_v2")) for r in rows) if x is not None],
    }
    if not series["v0"]:
        print("No data for projection accuracy.")
        return

    print(f"Projection accuracy ({len(series['v0'])} pitcher-starts)")
    table = []
    for label, errs in series.items():
        if not errs:
            continue
        table.append(
            {
                "model": label,
                "MAE": mean(abs(x) for x in errs),
                "RMSE": math.sqrt(mean(x * x for x in errs)),
                "bias": mean(errs),
            }
        )
    print(tabulate(table, headers="keys", floatfmt=".3f"))


def calibration_table(rows: list[dict]) -> None:
    has_line = [
        r
        for r in rows
        if _f(r.get("p_over")) is not None and _f(r.get("over_hit")) is not None
    ]
    if not has_line:
        print("\nNo settled lines for calibration.")
        return

    buckets = [(round(i / 10, 1), round((i + 1) / 10, 1)) for i in range(10)]
    table = []
    for lo, hi in buckets:
        bucket = [
            r for r in has_line if lo <= _f(r["p_over"]) < (hi + (0.001 if hi == 1.0 else 0))
        ]
        if not bucket:
            continue
        n = len(bucket)
        avg_pred = mean(_f(r["p_over"]) for r in bucket)
        actual_rate = mean(_f(r["over_hit"]) for r in bucket)
        table.append(
            {
                "p_over_bucket": f"{lo:.1f}–{hi:.1f}",
                "n": n,
                "avg_predicted": avg_pred,
                "actual_over_rate": actual_rate,
                "diff": actual_rate - avg_pred,
            }
        )

    print(f"\nP(over) calibration ({sum(b['n'] for b in table)} lines)")
    print(tabulate(table, headers="keys", floatfmt=".3f"))


def edge_strategy(rows: list[dict]) -> None:
    has_edge = [
        r
        for r in rows
        if _f(r.get("edge")) is not None and _f(r.get("over_pnl")) is not None
    ]
    if not has_edge:
        print("\nNo settled rows with edge for strategy backtest.")
        return

    print("\nOver-bet strategy by minimum edge")
    table = []
    for thresh in (-1.0, 0.00, 0.02, 0.04, 0.06, 0.10):
        side = [r for r in has_edge if _f(r["edge"]) > thresh]
        if not side:
            continue
        pnls = [_f(r["over_pnl"]) for r in side]
        hits = [_f(r["over_hit"]) for r in side if _f(r["over_hit"]) is not None]
        table.append(
            {
                "min_edge": f"{thresh:+.2f}",
                "n": len(side),
                "hit_rate": mean(hits) if hits else 0.0,
                "roi_per_$1": mean(pnls),
            }
        )
    print(tabulate(table, headers="keys", floatfmt=".3f"))


def run() -> None:
    rows = load_all_settled()
    if not rows:
        print(
            f"No settled CSVs in {OUTPUT_DIR}. "
            f"Run `python -m bets.settle <date>` first."
        )
        return

    print(f"Loaded {len(rows)} settled pitcher-starts.\n")
    projection_accuracy(rows)
    calibration_table(rows)
    edge_strategy(rows)


if __name__ == "__main__":
    run()
