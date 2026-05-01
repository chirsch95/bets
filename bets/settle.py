"""Settle past projections with actual K outcomes from the MLB Stats API.

Run with:
    python -m bets.settle               # settles yesterday
    python -m bets.settle 2026-04-30    # settles a specific date

Reads output/pitcher_ks_<date>.csv (projections) and writes
output/pitcher_ks_<date>_settled.csv (projections + actuals).
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from .config import OUTPUT_DIR
from .fetch import HTTP_TIMEOUT, MLB_STATS_BASE


def actual_ks_for(pitcher_id: int, target_date: date) -> dict | None:
    """Look up a pitcher's actual stat line on a given date.

    Returns None if the pitcher didn't pitch (scratch, postponement, off day).
    """
    season = target_date.year
    url = (
        f"{MLB_STATS_BASE}/people/{pitcher_id}/stats"
        f"?stats=gameLog&group=pitching&season={season}"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        return None
    payload = resp.json()

    target_iso = target_date.isoformat()
    for stat_group in payload.get("stats", []):
        for split in stat_group.get("splits", []):
            if split.get("date", "") != target_iso:
                continue
            stat = split.get("stat", {})
            return {
                "actual_ks": int(stat.get("strikeOuts", 0) or 0),
                "actual_bf": int(stat.get("battersFaced", 0) or 0),
                "gs": int(stat.get("gamesStarted", 0) or 0),
                "ip": str(stat.get("inningsPitched", "0.0")),
            }
    return None


def actual_hitter_ks_for(player_id: int, target_date: date) -> dict | None:
    """Look up a hitter's actual stat line on a given date.

    Returns None if the hitter didn't play (scratch, off day, postponement).
    """
    season = target_date.year
    url = (
        f"{MLB_STATS_BASE}/people/{player_id}/stats"
        f"?stats=gameLog&group=hitting&season={season}"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        return None
    payload = resp.json()

    target_iso = target_date.isoformat()
    for stat_group in payload.get("stats", []):
        for split in stat_group.get("splits", []):
            if split.get("date", "") != target_iso:
                continue
            stat = split.get("stat", {})
            return {
                "actual_ks": int(stat.get("strikeOuts", 0) or 0),
                "actual_pa": int(stat.get("plateAppearances", 0) or 0),
                "actual_ab": int(stat.get("atBats", 0) or 0),
            }
    return None


def _pnl(american: int, won: bool) -> float:
    """Profit / loss per $1 wagered at American odds."""
    if not won:
        return -1.0
    if american > 0:
        return american / 100
    return 100 / abs(american)


def _maybe_float(value) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def settle_date(target_date: date) -> Path | None:
    proj_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    if not proj_path.exists():
        print(f"No projection file at {proj_path}")
        return None

    with proj_path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"Projection file is empty: {proj_path}")
        return None
    if "pitcher_id" not in rows[0]:
        print(
            f"Projection CSV is missing pitcher_id; cannot settle. "
            f"Re-run bets.main to regenerate {proj_path.name}."
        )
        return None

    settled_count = 0
    for row in rows:
        actual = actual_ks_for(int(row["pitcher_id"]), target_date)
        if actual is None:
            row.update(
                actual_ks="",
                actual_bf="",
                gs=0,
                over_hit="",
                error_v0="",
                error_v1="",
                error_v2="",
                over_pnl="",
                under_pnl="",
            )
            continue

        settled_count += 1
        ks = actual["actual_ks"]
        row["actual_ks"] = ks
        row["actual_bf"] = actual["actual_bf"]
        row["gs"] = actual["gs"]

        proj_v0 = _maybe_float(row.get("proj_ks_v0"))
        proj_v1 = _maybe_float(row.get("proj_ks_v1"))
        proj_v2 = _maybe_float(row.get("proj_ks_v2"))
        row["error_v0"] = round(ks - proj_v0, 2) if proj_v0 is not None else ""
        row["error_v1"] = round(ks - proj_v1, 2) if proj_v1 is not None else ""
        row["error_v2"] = round(ks - proj_v2, 2) if proj_v2 is not None else ""

        line = _maybe_float(row.get("line"))
        if line is None:
            row.update(over_hit="", over_pnl="", under_pnl="")
            continue

        over_hit = ks > line
        row["over_hit"] = int(over_hit)

        over_odds = _maybe_float(row.get("over_odds"))
        under_odds = _maybe_float(row.get("under_odds"))
        row["over_pnl"] = (
            round(_pnl(int(over_odds), over_hit), 3)
            if over_odds is not None
            else ""
        )
        row["under_pnl"] = (
            round(_pnl(int(under_odds), not over_hit), 3)
            if under_odds is not None
            else ""
        )

    out_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}_settled.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Settled {settled_count}/{len(rows)} pitchers → {out_path}")
    return out_path


def settle_hitters_date(target_date: date) -> Path | None:
    """Settle a day of hitter K projections against actual outcomes."""
    proj_path = OUTPUT_DIR / f"hitter_ks_{target_date.isoformat()}.csv"
    if not proj_path.exists():
        print(f"No hitter projection file at {proj_path}")
        return None

    with proj_path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"Hitter projection file is empty: {proj_path}")
        return None
    if "hitter_id" not in rows[0]:
        print(
            f"Projection CSV is missing hitter_id; cannot settle. "
            f"Re-run bets.hitters to regenerate {proj_path.name}."
        )
        return None

    settled_count = 0
    for row in rows:
        actual = actual_hitter_ks_for(int(row["hitter_id"]), target_date)
        if actual is None:
            row.update(
                actual_ks="",
                actual_pa="",
                over_hit="",
                error="",
                over_pnl="",
                under_pnl="",
            )
            continue

        settled_count += 1
        ks = actual["actual_ks"]
        row["actual_ks"] = ks
        row["actual_pa"] = actual["actual_pa"]

        proj = _maybe_float(row.get("proj_ks"))
        row["error"] = round(ks - proj, 2) if proj is not None else ""

        line = _maybe_float(row.get("line"))
        if line is None:
            row.update(over_hit="", over_pnl="", under_pnl="")
            continue

        over_hit = ks > line
        row["over_hit"] = int(over_hit)
        over_odds = _maybe_float(row.get("over_odds"))
        under_odds = _maybe_float(row.get("under_odds"))
        row["over_pnl"] = (
            round(_pnl(int(over_odds), over_hit), 3)
            if over_odds is not None
            else ""
        )
        row["under_pnl"] = (
            round(_pnl(int(under_odds), not over_hit), 3)
            if under_odds is not None
            else ""
        )

    out_path = OUTPUT_DIR / f"hitter_ks_{target_date.isoformat()}_settled.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Settled {settled_count}/{len(rows)} hitters → {out_path}")
    return out_path


def main() -> None:
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        target = date.today() - timedelta(days=1)
    settle_date(target)
    settle_hitters_date(target)


if __name__ == "__main__":
    main()
