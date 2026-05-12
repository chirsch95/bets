"""Probability calibration for pitcher-K projection models.

Background: the raw `p_over` we compute from a Poisson(proj_ks) is
poorly calibrated against actual outcomes — see the 2026-05-11
calibration analysis. ML in particular predicts ~82% confidence in its
strongest bucket but actually hits 50%.

This module fits a Platt scaler (2-parameter logistic regression on
logit-transformed raw probabilities) that maps raw p_over → calibrated
p_over. We use Platt rather than isotonic because at the current sample
size (~250 settled pitcher-games) isotonic overfits — leave-one-day-out
CV showed isotonic *worsens* v0 calibration. Platt's two parameters
generalize better at small N; when the sample grows beyond ~500 we
can revisit isotonic.

Production flow:
    1. `bets.settle` calls `fit()` at the end of each settlement so
       calibration refreshes daily with yesterday's outcomes.
    2. `bets.main` calls `apply(raw_p, model='v2')` to compute
       cal_p_over_v2 + cal_p_over_ml as shadow columns alongside raw.
    3. ML is shadow-only today; its cal_p_over_ml is the calibrated
       form used in the eventual promotion comparison.

CLI:
    python3 -m bets.calibration fit
    python3 -m bets.calibration evaluate
"""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

from .config import OUTPUT_DIR, PROJECT_ROOT

# JSON rather than pickle so it's inspectable, robust to sklearn
# version upgrades, and doesn't care whether fit() ran as `__main__`
# or as an import.
CALIBRATION_PATH = PROJECT_ROOT / "data" / "calibration.json"
MODELS = ("v0", "v1", "v2", "ml")


def _safe_float(s):
    try:
        v = float(s)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _prob_over_poisson(line: float, projected_ks: float) -> float | None:
    """P(K > floor(line)) under K ~ Poisson(projected_ks). Inlined here
    instead of importing bets.model to avoid a circular dep with main.py."""
    from scipy.stats import poisson
    if projected_ks is None or projected_ks <= 0 or line is None:
        return None
    threshold = math.floor(line)
    return float(poisson.sf(threshold, projected_ks))


def gather_training_data(model: str, up_to_date: date | None = None) -> tuple[list[float], list[int]]:
    """Walk every settled CSV through (and including) up_to_date and
    return (raw_p_overs, over_hits) for the requested model.

    Uses slate_line (the line we'd actually have bet) when present,
    falling back to the live line. We use ONLY the over-direction
    observation — under = 1 - over is deterministic, including both
    would double the data artificially without adding info."""
    xs: list[float] = []
    ys: list[int] = []
    cutoff = up_to_date.isoformat() if up_to_date else None
    for path in sorted(OUTPUT_DIR.glob("pitcher_ks_*_settled.csv")):
        # filename is pitcher_ks_<date>_settled.csv
        try:
            date_str = path.stem.split("_")[2]
        except IndexError:
            continue
        if cutoff is not None and date_str > cutoff:
            continue
        with path.open() as f:
            for r in csv.DictReader(f):
                line = _safe_float(r.get("slate_line")) or _safe_float(r.get("line"))
                hit_raw = r.get("slate_over_hit") or r.get("over_hit") or ""
                if line is None or hit_raw in ("", None):
                    continue
                try:
                    hit = int(hit_raw)
                except (ValueError, TypeError):
                    continue
                proj = _safe_float(r.get(f"proj_ks_{model}"))
                p = _prob_over_poisson(line, proj)
                if p is None:
                    continue
                xs.append(p)
                ys.append(hit)
    return xs, ys


def _logit(p: float) -> float:
    """Numerically safe logit — clamps so log(0) never blows up. The
    1e-9 clamp matches the Brier/log-loss eval we already use."""
    p = max(min(p, 1 - 1e-9), 1e-9)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class PlattScaler:
    """Two-parameter logistic calibrator: cal_p = sigmoid(a * logit(raw_p) + b).

    Stored as a JSON-friendly triple (a, b, n) so the persisted file is
    inspectable and survives Python/sklearn version churn. apply() has
    zero non-stdlib dependencies."""

    __slots__ = ("a", "b", "n")

    def __init__(self, a: float, b: float, n: int):
        self.a = float(a)
        self.b = float(b)
        self.n = int(n)

    def predict(self, raw_p: float) -> float:
        return _sigmoid(self.a * _logit(raw_p) + self.b)

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "n": self.n}

    @classmethod
    def from_dict(cls, d: dict) -> "PlattScaler":
        return cls(a=d["a"], b=d["b"], n=d.get("n", 0))


def fit(up_to_date: date | None = None, verbose: bool = True) -> dict:
    """Fit one Platt scaler per model variant on every settled day's
    (raw_p, over_hit) pairs. Pickles {model_key: PlattScaler} to
    CALIBRATION_PATH so main.py loads with zero sklearn dependency.

    Skips a model if it has < 30 samples — below that Platt's two
    coefficients aren't reliably identified.
    """
    from sklearn.linear_model import LogisticRegression
    import numpy as np
    fitted: dict = {}
    counts: dict = {}
    for m in MODELS:
        xs, ys = gather_training_data(m, up_to_date=up_to_date)
        if len(xs) < 30:
            if verbose:
                print(f"  {m}: only {len(xs)} samples — skipping fit (need ≥30)")
            continue
        # Need both classes present for logistic regression to fit
        if len(set(ys)) < 2:
            if verbose:
                print(f"  {m}: only one outcome class observed — skipping fit")
            continue
        X = np.array([[_logit(p)] for p in xs])
        y = np.array(ys)
        clf = LogisticRegression(C=1.0, solver="lbfgs").fit(X, y)
        a = float(clf.coef_[0][0])
        b = float(clf.intercept_[0])
        fitted[m] = PlattScaler(a=a, b=b, n=len(xs))
        counts[m] = len(xs)
        if verbose:
            print(f"  {m}: fit on {len(xs)} samples — a={a:.3f} b={b:.3f}")
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        "models": {k: v.to_dict() for k, v in fitted.items()},
        "counts": counts,
        "as_of": (up_to_date.isoformat() if up_to_date else date.today().isoformat()),
        "method": "platt",
    }
    CALIBRATION_PATH.write_text(json.dumps(serializable, indent=2) + "\n")
    # Invalidate the in-process cache so the very next apply() call
    # picks up the freshly written file. Otherwise a settle → main
    # run in the same process keeps applying yesterday's coefficients.
    global _loaded_cache
    _loaded_cache = None
    if verbose:
        print(f"Wrote {CALIBRATION_PATH}")
    return fitted


_loaded_cache: dict | None = None


def load(force_reload: bool = False) -> dict:
    """Return {model_key: PlattScaler, "as_of": ..., "counts": ...}.
    Returns {} if no calibration file exists yet — apply() then falls
    back to the raw probability so production never breaks on a fresh
    checkout that hasn't run fit() yet."""
    global _loaded_cache
    if _loaded_cache is not None and not force_reload:
        return _loaded_cache
    if not CALIBRATION_PATH.exists():
        _loaded_cache = {}
        return _loaded_cache
    raw = json.loads(CALIBRATION_PATH.read_text())
    raw["models"] = {k: PlattScaler.from_dict(v) for k, v in (raw.get("models") or {}).items()}
    _loaded_cache = raw
    return _loaded_cache


def apply(raw_p: float | None, model: str = "v2") -> float | None:
    """Map raw probability through the fitted calibrator for `model`.
    Returns raw_p unchanged if no calibration is loaded or the model
    isn't in the fit — safe to call from a fresh checkout that has
    no calibration.pkl yet."""
    if raw_p is None:
        return None
    cal = load()
    scaler = (cal.get("models") or {}).get(model)
    if scaler is None:
        return raw_p
    try:
        return float(scaler.predict(raw_p))
    except Exception:  # noqa: BLE001
        return raw_p


def evaluate(up_to_date: date | None = None) -> None:
    """Print a before/after Platt-calibration report per model. Uses
    leave-one-day-out CV so we don't evaluate on data we fit on —
    that distinction matters: in-sample numbers always look better than
    they should because the calibrator memorizes its own training set."""
    from sklearn.linear_model import LogisticRegression
    import numpy as np
    print("Leave-one-day-out CV calibration report (Platt scaling)")
    print("=" * 70)
    by_date: dict[str, list] = {}
    for path in sorted(OUTPUT_DIR.glob("pitcher_ks_*_settled.csv")):
        try:
            date_str = path.stem.split("_")[2]
        except IndexError:
            continue
        if up_to_date is not None and date_str > up_to_date.isoformat():
            continue
        with path.open() as f:
            for r in csv.DictReader(f):
                line = _safe_float(r.get("slate_line")) or _safe_float(r.get("line"))
                hit_raw = r.get("slate_over_hit") or r.get("over_hit") or ""
                if line is None or hit_raw in ("", None):
                    continue
                try:
                    hit = int(hit_raw)
                except (ValueError, TypeError):
                    continue
                by_date.setdefault(date_str, []).append((line, r, hit))
    dates_sorted = sorted(by_date)

    for m in MODELS:
        raw_p_all, cal_p_all, y_all = [], [], []
        for held_out_date in dates_sorted:
            train_x, train_y = [], []
            for d, rows in by_date.items():
                if d == held_out_date:
                    continue
                for line, r, hit in rows:
                    proj = _safe_float(r.get(f"proj_ks_{m}"))
                    p = _prob_over_poisson(line, proj)
                    if p is None:
                        continue
                    train_x.append([_logit(p)])
                    train_y.append(hit)
            if len(train_x) < 30 or len(set(train_y)) < 2:
                continue
            clf = LogisticRegression(C=1.0, solver="lbfgs").fit(np.array(train_x), np.array(train_y))
            a = float(clf.coef_[0][0])
            b = float(clf.intercept_[0])
            for line, r, hit in by_date[held_out_date]:
                proj = _safe_float(r.get(f"proj_ks_{m}"))
                p = _prob_over_poisson(line, proj)
                if p is None:
                    continue
                cal_p = _sigmoid(a * _logit(p) + b)
                raw_p_all.append(p)
                cal_p_all.append(cal_p)
                y_all.append(hit)
        if not raw_p_all:
            print(f"\n{m}: no data")
            continue
        n = len(y_all)
        raw_brier = sum((p - y) ** 2 for p, y in zip(raw_p_all, y_all)) / n
        cal_brier = sum((p - y) ** 2 for p, y in zip(cal_p_all, y_all)) / n

        def sl(x):
            return math.log(max(min(x, 1 - 1e-9), 1e-9))

        raw_ll = -sum(y * sl(p) + (1 - y) * sl(1 - p) for p, y in zip(raw_p_all, y_all)) / n
        cal_ll = -sum(y * sl(p) + (1 - y) * sl(1 - p) for p, y in zip(cal_p_all, y_all)) / n
        delta_b = cal_brier - raw_brier
        delta_l = cal_ll - raw_ll
        print(f"\n{m}  (n={n})")
        print(f"  Brier:    raw {raw_brier:.4f}  cal {cal_brier:.4f}  Δ {delta_b:+.4f}")
        print(f"  Log loss: raw {raw_ll:.4f}  cal {cal_ll:.4f}  Δ {delta_l:+.4f}")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "fit"
    if cmd == "fit":
        fit()
    elif cmd == "evaluate":
        evaluate()
    else:
        print(f"Usage: python3 -m bets.calibration [fit|evaluate]")
        sys.exit(1)


if __name__ == "__main__":
    main()
