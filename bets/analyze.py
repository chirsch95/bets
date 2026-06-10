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
from .model import prob_over_poisson


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
        # bias-corrected v2 shadow (2026-06-09+; empty for earlier rows)
        "v2bc": [x for x in (_f(r.get("error_v2bc")) for r in rows) if x is not None],
        "ml": [x for x in (_f(r.get("error_ml")) for r in rows) if x is not None],
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
                "n": len(errs),
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


def slice_table(rows: list[dict]) -> None:
    paired = [
        r
        for r in rows
        if _f(r.get("error_v2")) is not None and _f(r.get("error_ml")) is not None
    ]
    if not paired:
        print("\nNo paired v2/ml rows for slicing.")
        return

    def home_away(r):
        v = r.get("is_home", "")
        if v in ("True", "true", "1", 1, True):
            return "home"
        if v in ("False", "false", "0", 0, False):
            return "away"
        return None

    def opp_k_bucket(r):
        v = _f(r.get("opp_k_pct"))
        if v is None:
            return None
        if v < 0.21:
            return "low (<.21)"
        if v < 0.23:
            return "mid (.21-.23)"
        return "high (≥.23)"

    def park_bucket(r):
        v = _f(r.get("park_factor"))
        if v is None:
            return None
        if v < 1.00:
            return "suppressor (<1.00)"
        if v == 1.00:
            return "neutral (=1.00)"
        return "booster (>1.00)"

    def line_bucket(r):
        v = _f(r.get("line"))
        if v is None:
            return None
        if v <= 4.5:
            return "≤4.5"
        if v <= 5.5:
            return "5.0-5.5"
        if v <= 6.5:
            return "6.0-6.5"
        return "≥7.0"

    def lineup_avail(r):
        s = r.get("opp_k_source", "")
        if s == "lineup":
            return "lineup known"
        if s == "team":
            return "team fallback"
        return None

    def form_divergence(r):
        season = _f(r.get("season_k_pct"))
        recent = _f(r.get("recent_k_pct"))
        if season is None or recent is None:
            return None
        diff = recent - season
        if diff < -0.03:
            return "cooling (recent <season -3pp)"
        if diff > 0.03:
            return "heating (recent >season +3pp)"
        return "stable (±3pp)"

    slices = [
        ("home/away", home_away, ["away", "home"]),
        (
            "opp K% bucket",
            opp_k_bucket,
            ["low (<.21)", "mid (.21-.23)", "high (≥.23)"],
        ),
        (
            "park factor",
            park_bucket,
            ["suppressor (<1.00)", "neutral (=1.00)", "booster (>1.00)"],
        ),
        ("line bucket", line_bucket, ["≤4.5", "5.0-5.5", "6.0-6.5", "≥7.0"]),
        ("lineup availability", lineup_avail, ["lineup known", "team fallback"]),
        (
            "recent vs season form",
            form_divergence,
            [
                "cooling (recent <season -3pp)",
                "stable (±3pp)",
                "heating (recent >season +3pp)",
            ],
        ),
    ]

    print(f"\nv2 vs ML — paired comparison ({len(paired)} starts)")
    print("(Δ_RMSE = ml_RMSE − v2_RMSE; negative means ML wins that slice)")
    for name, fn, order in slices:
        bucketed: dict[str, list] = {}
        for r in paired:
            key = fn(r)
            if key is None:
                continue
            bucketed.setdefault(key, []).append(r)
        if not bucketed:
            continue
        rows_out = []
        for key in order:
            bucket = bucketed.get(key)
            if not bucket:
                continue
            v2_errs = [_f(r["error_v2"]) for r in bucket]
            ml_errs = [_f(r["error_ml"]) for r in bucket]
            v2_rmse = math.sqrt(mean(x * x for x in v2_errs))
            ml_rmse = math.sqrt(mean(x * x for x in ml_errs))
            rows_out.append(
                {
                    "bucket": key,
                    "n": len(bucket),
                    "v2_MAE": mean(abs(x) for x in v2_errs),
                    "ml_MAE": mean(abs(x) for x in ml_errs),
                    "v2_RMSE": v2_rmse,
                    "ml_RMSE": ml_rmse,
                    "Δ_RMSE": ml_rmse - v2_rmse,
                }
            )
        if rows_out:
            print(f"\n  by {name}")
            print(tabulate(rows_out, headers="keys", floatfmt=".3f"))


def promotion_verdict(rows: list[dict]) -> None:
    """Apply the v2-vs-ML promotion bar locked in by the 2026-05-16 grill
    (see project_ml_phase1 memory). Two checks must BOTH pass for promotion:

    1. Diebold-Mariano paired t-test on per-outing squared errors:
       t >= 2.0 (≈ p < 0.05). v2 baseline is fixed; squared error of v2
       minus squared error of ML, averaged, divided by its SE.

    2. Operational hit-rate sanity: at each edge threshold the user actually
       acts on (≥5%, ≥10%), ML's hit rate must be within ±2pp of v2's.
       Catches the failure mode where ML wins on average squared error but
       diverges on the marginal calls that flip an over/under decision.

    OLD bar (≥0.1 K RMSE) is deprecated — see project_ml_phase1.
    """
    paired = [
        r
        for r in rows
        if _f(r.get("error_v2")) is not None
        and _f(r.get("error_ml")) is not None
    ]
    if len(paired) < 30:
        print(f"\n=== Promotion verdict ===")
        print(f"Only {len(paired)} paired v2/ml outings — need ≥30 for any verdict.")
        return

    # (1) Diebold-Mariano paired t-test on squared errors
    diffs = [
        _f(r["error_v2"]) ** 2 - _f(r["error_ml"]) ** 2 for r in paired
    ]
    n = len(diffs)
    mean_d = sum(diffs) / n
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1) if n > 1 else 0
    se_d = math.sqrt(var_d / n) if n > 1 else 0
    t_stat = mean_d / se_d if se_d > 0 else 0
    stat_pass = t_stat >= 2.0

    # (2) Hit-rate comparison at each operational edge threshold
    def _hit_for(direction: str, over_hit: int | None) -> int | None:
        if over_hit is None:
            return None
        return over_hit if direction == "over" else (1 - over_hit)

    sanity_rows = []
    sanity_pass = True
    for thresh in (0.05, 0.10):
        v2_subset, ml_subset = [], []
        for r in paired:
            ce_v2 = _f(r.get("cal_edge_v2")) or _f(r.get("edge"))
            ce_ml = _f(r.get("cal_edge_ml"))
            oh = _f(r.get("over_hit"))
            if oh is None:
                continue
            if ce_v2 is not None and abs(ce_v2) >= thresh:
                v2_subset.append(_hit_for("over" if ce_v2 > 0 else "under", int(oh)))
            if ce_ml is not None and abs(ce_ml) >= thresh:
                ml_subset.append(_hit_for("over" if ce_ml > 0 else "under", int(oh)))
        if len(v2_subset) >= 10 and len(ml_subset) >= 10:
            v2_rate = mean(v2_subset)
            ml_rate = mean(ml_subset)
            gap_pp = (ml_rate - v2_rate) * 100
            ok = abs(gap_pp) <= 2.0
            if not ok:
                sanity_pass = False
            sanity_rows.append([
                f"|edge|≥{thresh:.2f}",
                f"{len(v2_subset)}",
                f"{v2_rate*100:.1f}%",
                f"{len(ml_subset)}",
                f"{ml_rate*100:.1f}%",
                f"{gap_pp:+.1f}pp",
                "ok" if ok else "GAP > ±2pp",
            ])
        else:
            sanity_rows.append([
                f"|edge|≥{thresh:.2f}",
                f"{len(v2_subset)}",
                "—",
                f"{len(ml_subset)}",
                "—",
                "—",
                "need more data",
            ])

    print(f"\n=== Promotion verdict ({n} paired outings) ===")
    print(f"\n(1) Diebold-Mariano paired t-test on squared errors:")
    print(f"    mean(v2² − ml²) = {mean_d:+.4f}  SE = {se_d:.4f}  t = {t_stat:+.2f}")
    print(f"    bar: t ≥ 2.0  →  {'PASS' if stat_pass else 'FAIL'}")
    print(f"\n(2) Hit-rate sanity (ML within ±2pp of v2):")
    print(tabulate(
        sanity_rows,
        headers=["threshold", "v2_n", "v2_hit%", "ml_n", "ml_hit%", "gap", "verdict"],
    ))

    if stat_pass and sanity_pass:
        print("\n>>> PROMOTE ML to primary. Both bars cleared.")
    elif stat_pass and not sanity_pass:
        print("\n>>> HOLD — statistical bar cleared but hit-rate sanity failed.")
        print("    ML likely wins on easy projections, loses on marginal calls.")
        print("    Consider the form-divergence hybrid (project_ml_form_divergence).")
    elif not stat_pass and sanity_pass:
        print("\n>>> HOLD — sanity check passed but statistical bar not cleared.")
        print("    Likely a noise-level difference; needs more data.")
    else:
        print("\n>>> HOLD — neither bar cleared. Keep ML as shadow.")


def _v0_edge(r: dict) -> float | None:
    """v0's edge against the same no-vig line v2/production was scored on.

    The CSV stores production (v2) ``p_over`` and ``edge``; their difference
    recovers the no-vig over prob. Recompute v0's over prob from ``proj_ks_v0``
    via the production Poisson formula so the comparison is apples-to-apples.
    """
    line = _f(r.get("line"))
    pov = _f(r.get("p_over"))
    edge = _f(r.get("edge"))
    v0 = _f(r.get("proj_ks_v0"))
    if None in (line, pov, edge, v0):
        return None
    return prob_over_poisson(line, v0) - (pov - edge)  # v0_p_over − novig_over


def _edge_roi(rows: list[dict], edge_fn, thresh: float) -> dict | None:
    """Over-bet every row whose model edge exceeds ``thresh``; realized pnl
    comes from the same ``over_pnl`` column regardless of which model selected
    the bet, so this isolates *selection* quality."""
    side = []
    for r in rows:
        e = edge_fn(r)
        pnl = _f(r.get("over_pnl"))
        if e is None or pnl is None or e <= thresh:
            continue
        side.append((pnl, _f(r.get("over_hit"))))
    if not side:
        return None
    hits = [h for _, h in side if h is not None]
    return {
        "n": len(side),
        "hit": mean(hits) if hits else 0.0,
        "roi": mean(p for p, _ in side),
    }


def v0_shadow_report(rows: list[dict]) -> None:
    """Standing v0-vs-v2 comparison + the pre-registered production-flip rule.

    Context (2026-05-24 analysis): over the first 24 days / 641 starts the
    simplest model (v0) beat production v2 on BOTH point accuracy (RMSE) and
    high-edge betting ROI. The flip is deliberately gated to guard against
    fitting to a single early-season regime — v2's SwStr-blend thesis is
    weakest in April (thin Savant samples) and should strengthen as the
    season matures. Flip v2→v0 only when ALL of:
      (1) ≥40 distinct slate days logged,
      (2) v0 RMSE < v2 RMSE (pooled),
      (3) v0 ROI > v2 ROI at edge≥0.06 (pooled),
      (4) v0 ROI ≥ v2 ROI in every month with ≥20 qualifying bets (regime
          stability — the overfitting guard).
    """
    v2_fn = lambda r: _f(r.get("edge"))
    usable = [
        r
        for r in rows
        if _v0_edge(r) is not None and _f(r.get("over_pnl")) is not None
    ]
    if not usable:
        print("\n=== v0 shadow ===\nNo rows with v0 edge + pnl yet.")
        return

    print(f"\n=== v0 shadow — bet-selection ROI head-to-head ({len(usable)} lines) ===")
    table = []
    for t in (0.00, 0.02, 0.04, 0.06, 0.10):
        v2 = _edge_roi(usable, v2_fn, t)
        v0 = _edge_roi(usable, _v0_edge, t)
        table.append(
            {
                "min_edge": f"{t:+.2f}",
                "v2_n": v2["n"] if v2 else 0,
                "v2_roi": v2["roi"] if v2 else float("nan"),
                "v0_n": v0["n"] if v0 else 0,
                "v0_roi": v0["roi"] if v0 else float("nan"),
            }
        )
    print(tabulate(table, headers="keys", floatfmt=".3f"))

    # (4) regime split — ROI at edge≥0.06 per calendar month
    months = sorted({r["date"][:7] for r in usable if r.get("date")})
    msplit = []
    for m in months:
        mr = [r for r in usable if r.get("date", "").startswith(m)]
        v2 = _edge_roi(mr, v2_fn, 0.06)
        v0 = _edge_roi(mr, _v0_edge, 0.06)
        msplit.append(
            {
                "month": m,
                "v2_n": v2["n"] if v2 else 0,
                "v2_roi": v2["roi"] if v2 else float("nan"),
                "v0_n": v0["n"] if v0 else 0,
                "v0_roi": v0["roi"] if v0 else float("nan"),
                "v0_leads": "yes"
                if (v0 and v2 and v0["roi"] > v2["roi"])
                else "no",
            }
        )
    print("\n  regime split — ROI at edge≥0.06 by month (overfitting guard)")
    print(tabulate(msplit, headers="keys", floatfmt=".3f"))

    # accuracy + Diebold-Mariano on squared errors (positive t → v0 better)
    paired = [
        r
        for r in rows
        if _f(r.get("error_v2")) is not None and _f(r.get("error_v0")) is not None
    ]
    v2_rmse = math.sqrt(mean(_f(r["error_v2"]) ** 2 for r in paired)) if paired else float("nan")
    v0_rmse = math.sqrt(mean(_f(r["error_v0"]) ** 2 for r in paired)) if paired else float("nan")
    diffs = [_f(r["error_v2"]) ** 2 - _f(r["error_v0"]) ** 2 for r in paired]
    n = len(diffs)
    mean_d = sum(diffs) / n if n else 0.0
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
    se_d = math.sqrt(var_d / n) if n > 1 and var_d > 0 else 0.0
    t_stat = mean_d / se_d if se_d > 0 else 0.0

    n_days = len({r["date"] for r in usable if r.get("date")})
    pooled_v2 = _edge_roi(usable, v2_fn, 0.06)
    pooled_v0 = _edge_roi(usable, _v0_edge, 0.06)
    rmse_lead = v0_rmse < v2_rmse
    roi_lead = bool(pooled_v0 and pooled_v2 and pooled_v0["roi"] > pooled_v2["roi"])
    qual_months = [row for row in msplit if row["v0_n"] >= 20]
    regime_ok = bool(qual_months) and all(row["v0_leads"] == "yes" for row in qual_months)

    print(f"\n=== v0 flip verdict ===")
    print(f"    v2 RMSE {v2_rmse:.3f} vs v0 RMSE {v0_rmse:.3f}  (DM t = {t_stat:+.2f}, + → v0 better)")
    print(f"    (1) ≥40 slate days .......... {n_days}/40  {'PASS' if n_days >= 40 else 'pending'}")
    print(f"    (2) v0 RMSE < v2 RMSE ....... {'PASS' if rmse_lead else 'FAIL'}")
    print(f"    (3) v0 ROI > v2 ROI @0.06 ... {'PASS' if roi_lead else 'FAIL'}")
    print(f"    (4) v0 leads every ≥20-bet month {'PASS' if regime_ok else 'FAIL/pending'}")
    if n_days >= 40 and rmse_lead and roi_lead and regime_ok:
        print("\n>>> FLIP production v2→v0. All four criteria cleared.")
    elif n_days < 40:
        print(f"\n>>> HOLD — {n_days}/40 days. Re-check after ~40 days span a regime split.")
    else:
        print("\n>>> HOLD — day count met but a criterion failed; v2 stays production.")


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
    slice_table(rows)
    promotion_verdict(rows)
    v0_shadow_report(rows)


if __name__ == "__main__":
    run()
