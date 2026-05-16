"""Residuals report for v2 — bucket settled outings by candidate variables
and surface where the model is systematically biased.

This is the gatekeeper for adding new factors. Per the 2026-05-16 grill
decision, a new factor (framing, weather, splits, etc.) only earns a
ship-the-code conversation if this report shows v2 is systematically wrong
in a direction the factor would correct.

The promotion bar for a candidate variable:
    * the most-biased bucket has |mean signed error| >= 0.2 K
    * every bucket has n >= 30 outings
    * bias is monotonic across buckets (consistent direction)
    * permutation test p-value < 0.05 (pattern survives a noise check)

`error_v2 = actual_ks - proj_v2`, so:
    positive bias → model under-predicts (actual K came in higher)
    negative bias → model over-predicts (actual K came in lower)

Run with:
    python -m bets.residuals
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from statistics import mean
from typing import Callable

from tabulate import tabulate

from .config import OUTPUT_DIR

PERMUTATION_SEED = 42
PERMUTATION_N = 1000
BAR_MIN_BIAS_K = 0.20
BAR_MIN_BUCKET_N = 30
BAR_MAX_P_VALUE = 0.05


def _f(value) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_settled() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(OUTPUT_DIR.glob("pitcher_ks_*_settled.csv")):
        with path.open() as f:
            for r in csv.DictReader(f):
                if _f(r.get("actual_ks")) is None:
                    continue
                if _f(r.get("error_v2")) is None:
                    continue
                rows.append(r)
    return rows


def _form_divergence(r: dict) -> float | None:
    recent = _f(r.get("recent_k_pct"))
    season = _f(r.get("season_k_pct"))
    if recent is None or season is None:
        return None
    return recent - season


@dataclass
class BucketStat:
    label: str
    n: int
    mean_signed_err: float
    var_lo: float | None = None
    var_hi: float | None = None


def _tercile_buckets(pairs: list[tuple[float, float]]) -> list[BucketStat]:
    pairs.sort(key=lambda p: p[0])
    n = len(pairs)
    b1 = n // 3
    b2 = 2 * n // 3
    slices = [
        ("bottom", pairs[:b1]),
        ("middle", pairs[b1:b2]),
        ("top", pairs[b2:]),
    ]
    out = []
    for label, sl in slices:
        if not sl:
            continue
        errs = [e for _, e in sl]
        out.append(BucketStat(
            label=label,
            n=len(errs),
            mean_signed_err=mean(errs),
            var_lo=sl[0][0],
            var_hi=sl[-1][0],
        ))
    return out


def _categorical_buckets(pairs: list[tuple[str, float]]) -> list[BucketStat]:
    groups: dict[str, list[float]] = {}
    for cat, e in pairs:
        groups.setdefault(cat, []).append(e)
    return [
        BucketStat(label=str(k), n=len(v), mean_signed_err=mean(v))
        for k, v in sorted(groups.items())
    ]


def _permutation_p(errs: list[float], bucket_sizes: list[int],
                   observed_spread: float) -> float:
    """How often does shuffling errors across same-sized buckets produce
    an equal-or-larger spread? Two-tailed."""
    rng = random.Random(PERMUTATION_SEED)
    larger_or_equal = 0
    n_total = sum(bucket_sizes)
    pool = errs[:n_total]
    for _ in range(PERMUTATION_N):
        rng.shuffle(pool)
        idx = 0
        bucket_means = []
        for size in bucket_sizes:
            bucket_means.append(mean(pool[idx:idx + size]))
            idx += size
        spread = max(bucket_means) - min(bucket_means)
        if spread >= observed_spread:
            larger_or_equal += 1
    return larger_or_equal / PERMUTATION_N


def _is_monotonic(buckets: list[BucketStat]) -> bool:
    if len(buckets) < 2:
        return True
    diffs = [
        buckets[i + 1].mean_signed_err - buckets[i].mean_signed_err
        for i in range(len(buckets) - 1)
    ]
    return all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)


def _verdict(buckets: list[BucketStat], p_value: float,
             monotonic_required: bool) -> str:
    max_bias = max(abs(b.mean_signed_err) for b in buckets)
    min_n = min(b.n for b in buckets)
    monotonic_ok = (not monotonic_required) or _is_monotonic(buckets)
    if (max_bias >= BAR_MIN_BIAS_K
            and min_n >= BAR_MIN_BUCKET_N
            and p_value < BAR_MAX_P_VALUE
            and monotonic_ok):
        return "FACTOR WORTH EXPLORING"
    reasons = []
    if max_bias < BAR_MIN_BIAS_K:
        reasons.append(f"bias too small ({max_bias:.2f} < {BAR_MIN_BIAS_K})")
    if min_n < BAR_MIN_BUCKET_N:
        reasons.append(f"bucket too thin (n={min_n} < {BAR_MIN_BUCKET_N})")
    if p_value >= BAR_MAX_P_VALUE:
        reasons.append(f"p={p_value:.3f} (≥ {BAR_MAX_P_VALUE})")
    if monotonic_required and not _is_monotonic(buckets):
        reasons.append("non-monotonic")
    return "no signal — " + ", ".join(reasons)


def _report(name: str, buckets: list[BucketStat], p_value: float,
            monotonic_required: bool) -> dict:
    verdict = _verdict(buckets, p_value, monotonic_required)
    rows = []
    for b in buckets:
        range_str = (
            f"[{b.var_lo:.3f}, {b.var_hi:.3f}]"
            if b.var_lo is not None else b.label
        )
        rows.append([b.label, b.n, range_str, f"{b.mean_signed_err:+.3f}"])
    print(f"\n=== {name} ===")
    print(tabulate(rows, headers=["bucket", "n", "range/value", "mean signed err (K)"]))
    print(f"permutation p-value: {p_value:.3f}  →  {verdict}")
    return {
        "variable": name,
        "p_value": p_value,
        "verdict": verdict,
        "buckets": [
            {"label": b.label, "n": b.n,
             "var_lo": b.var_lo, "var_hi": b.var_hi,
             "mean_signed_err": b.mean_signed_err}
            for b in buckets
        ],
    }


def analyze_continuous(rows: list[dict], name: str,
                       getter: Callable[[dict], float | None]) -> dict | None:
    pairs = [
        (getter(r), _f(r["error_v2"]))
        for r in rows
        if getter(r) is not None
    ]
    pairs = [p for p in pairs if p[1] is not None]
    if len(pairs) < 30:
        print(f"\n=== {name} ===\n(skip — only {len(pairs)} outings)")
        return None
    buckets = _tercile_buckets(pairs)
    errs = [e for _, e in pairs]
    spread = max(b.mean_signed_err for b in buckets) - min(b.mean_signed_err for b in buckets)
    p = _permutation_p(errs, [b.n for b in buckets], spread)
    return _report(name, buckets, p, monotonic_required=True)


def analyze_categorical(rows: list[dict], name: str,
                        getter: Callable[[dict], str | None]) -> dict | None:
    pairs = [
        (getter(r), _f(r["error_v2"]))
        for r in rows
        if getter(r) is not None
    ]
    pairs = [p for p in pairs if p[1] is not None]
    if len(pairs) < 30:
        print(f"\n=== {name} ===\n(skip — only {len(pairs)} outings)")
        return None
    buckets = _categorical_buckets(pairs)
    errs = [e for _, e in pairs]
    spread = max(b.mean_signed_err for b in buckets) - min(b.mean_signed_err for b in buckets)
    p = _permutation_p(errs, [b.n for b in buckets], spread)
    return _report(name, buckets, p, monotonic_required=False)


def main() -> None:
    rows = _load_settled()
    print(f"Loaded {len(rows)} settled outings with non-null error_v2.")
    print(f"\nPromotion bar: |bias| ≥ {BAR_MIN_BIAS_K} K AND every bucket n ≥ {BAR_MIN_BUCKET_N} "
          f"AND p < {BAR_MAX_P_VALUE} AND monotonic.")

    overall = mean(_f(r["error_v2"]) for r in rows)
    print(f"\nOverall mean signed error_v2: {overall:+.3f} K "
          f"(positive = model under-predicts on average)")

    results = []
    results.append(analyze_continuous(rows, "swstr_pct",
                                      lambda r: _f(r.get("swstr_pct"))))
    results.append(analyze_continuous(rows, "season_k_pct",
                                      lambda r: _f(r.get("season_k_pct"))))
    results.append(analyze_continuous(rows, "recent_k_pct",
                                      lambda r: _f(r.get("recent_k_pct"))))
    results.append(analyze_continuous(rows, "form_divergence (recent - season)",
                                      _form_divergence))
    results.append(analyze_continuous(rows, "opp_k_pct",
                                      lambda r: _f(r.get("opp_k_pct"))))
    results.append(analyze_continuous(rows, "park_factor",
                                      lambda r: _f(r.get("park_factor"))))
    results.append(analyze_continuous(rows, "matchup_k_pct",
                                      lambda r: _f(r.get("matchup_k_pct"))))
    results.append(analyze_continuous(rows, "exp_bf",
                                      lambda r: _f(r.get("exp_bf"))))
    results.append(analyze_categorical(rows, "is_home",
                                       lambda r: (r.get("is_home") or "").strip() or None))
    results.append(analyze_categorical(rows, "opp_k_source",
                                       lambda r: (r.get("opp_k_source") or "").strip() or None))

    out_path = OUTPUT_DIR / "residuals_report.json"
    with out_path.open("w") as f:
        json.dump({
            "n_outings": len(rows),
            "overall_mean_signed_err": overall,
            "bar": {
                "min_bias_k": BAR_MIN_BIAS_K,
                "min_bucket_n": BAR_MIN_BUCKET_N,
                "max_p_value": BAR_MAX_P_VALUE,
            },
            "variables": [r for r in results if r is not None],
        }, f, indent=2)
    print(f"\nWrote machine-readable report → {out_path}")

    worth = [r for r in results if r and r["verdict"].startswith("FACTOR")]
    if worth:
        print(f"\n>>> {len(worth)} variable(s) cleared the bar:")
        for r in worth:
            print(f"    • {r['variable']}")
    else:
        print("\n>>> No variables cleared the bar. v2 is not systematically biased "
              "on any tested variable at the configured threshold.")


if __name__ == "__main__":
    main()
