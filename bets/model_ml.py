"""XGBoost shadow model for pitcher K projections (phase 1).

Trains on settled CSV rows (output/pitcher_ks_*_settled.csv) where the
pitcher actually started (gs == 1). Produces proj_ks_ml as a shadow
column alongside the hand-tuned v0/v1/v2 projections — no betting
decisions are made from this output yet. The intent is to compare
ML accuracy/calibration against v2 over a multi-week sample before
promoting it.

CLI:
    python -m bets.main --train-ml      # retrain from all settled rows
"""

from __future__ import annotations

import glob
import math

import pandas as pd

from .config import DATA_DIR, OUTPUT_DIR

MODEL_PATH = DATA_DIR / "model_xgb.pkl"
# Phase 1 threshold: low enough to train on a single day's worth of post-
# is_home data so plumbing can be exercised end-to-end. Real lift requires
# hundreds of rows — see the warning emitted in train_cli().
MIN_TRAINING_ROWS = 25
TRUSTWORTHY_ROWS = 200

FEATURES = [
    "season_k_pct",
    "recent_k_pct",
    "swstr_pct",
    "opp_k_pct",
    "park_factor",
    "exp_bf",
    "is_home",
]

_model_cache = None
_model_load_attempted = False


def _coerce_is_home(value) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(bool(value))
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("true", "1", "yes") else 0
    return 0


def load_training_data(verbose: bool = False) -> pd.DataFrame:
    """Concatenate every settled pitcher CSV into one labeled DataFrame.

    Filters to rows where the pitcher actually started (gs == 1) and a
    real actual_ks was recorded. Files predating any feature column get
    skipped — schema drift over the season is silently tolerated this
    way, at the cost of dropping the older days from the training set.
    """
    files = sorted(glob.glob(str(OUTPUT_DIR / "pitcher_ks_*_settled.csv")))
    required = set(FEATURES) | {"actual_ks", "gs"}
    frames = []
    for f in files:
        df = pd.read_csv(f)
        missing = required - set(df.columns)
        if missing:
            if verbose:
                print(f"  skipping {f.split('/')[-1]} (missing: {sorted(missing)})")
            continue
        df = df[(df["gs"] == 1) & df["actual_ks"].notna()].copy()
        if df.empty:
            continue
        df["is_home"] = df["is_home"].apply(_coerce_is_home)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def train(min_rows: int = MIN_TRAINING_ROWS) -> dict:
    """Train + persist the XGBoost model. Returns a stats dict.

    Raises RuntimeError if there aren't enough labeled rows yet — useful
    early in phase 1 when settled history is too thin to fit anything.
    """
    from xgboost import XGBRegressor
    import joblib

    df = load_training_data(verbose=True)
    n = len(df)
    if n < min_rows:
        raise RuntimeError(
            f"Need at least {min_rows} settled rows to train; have {n}. "
            f"Keep running daily fetch + settle and retry later."
        )

    X = df[FEATURES].astype(float)
    y = df["actual_ks"].astype(float)

    # Time-ordered holdout: last 20% of rows. Filenames sort by date so
    # concat order ≈ chronological — random shuffling would leak future
    # info into training and inflate the holdout RMSE.
    cut = max(1, int(n * 0.8))
    X_train, X_test = X.iloc[:cut], X.iloc[cut:]
    y_train, y_test = y.iloc[:cut], y.iloc[cut:]

    model = XGBRegressor(
        n_estimators=300,
        max_depth=3,           # shallow — small dataset, easy to overfit
        learning_rate=0.05,
        objective="count:poisson",
        random_state=42,
    )
    model.fit(X_train, y_train)

    train_pred = model.predict(X_train)
    train_rmse = math.sqrt(((train_pred - y_train) ** 2).mean())
    if len(X_test):
        test_pred = model.predict(X_test)
        test_rmse = math.sqrt(((test_pred - y_test) ** 2).mean())
    else:
        test_rmse = float("nan")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)

    global _model_cache, _model_load_attempted
    _model_cache = None
    _model_load_attempted = False

    return {
        "n_rows": n,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "train_rmse": train_rmse,
        "test_rmse": test_rmse,
        "model_path": str(MODEL_PATH),
    }


def _get_model():
    global _model_cache, _model_load_attempted
    if _model_load_attempted:
        return _model_cache
    _model_load_attempted = True
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib
        _model_cache = joblib.load(MODEL_PATH)
    except Exception:
        _model_cache = None
    return _model_cache


def predict(features: dict) -> float | None:
    """Return ML K projection for one pitcher-game; None if no model trained."""
    model = _get_model()
    if model is None:
        return None
    row = {k: features.get(k, 0.0) for k in FEATURES}
    row["is_home"] = _coerce_is_home(row["is_home"])
    df = pd.DataFrame([row], columns=FEATURES).astype(float)
    try:
        return float(model.predict(df)[0])
    except Exception:
        return None


def train_cli() -> None:
    """Console-friendly wrapper used by `python -m bets.main --train-ml`."""
    try:
        result = train()
    except RuntimeError as e:
        print(f"WARNING: {e}")
        return
    print(
        f"Trained on {result['n_train']} rows "
        f"(holdout: {result['n_test']})."
    )
    print(f"  train RMSE:   {result['train_rmse']:.3f} K")
    if not math.isnan(result["test_rmse"]):
        print(f"  holdout RMSE: {result['test_rmse']:.3f} K")
    else:
        print("  holdout RMSE: n/a (not enough rows for a holdout)")
    print(f"  saved -> {result['model_path']}")
    if result["n_rows"] < TRUSTWORTHY_ROWS:
        print(
            f"\nNOTE: only {result['n_rows']} training rows. The shadow "
            f"projection will be noisy until you accumulate ~{TRUSTWORTHY_ROWS}+ "
            f"settled starts. Re-run --train-ml weekly as data grows."
        )
