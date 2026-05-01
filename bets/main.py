"""CLI entry point: fetch today's slate, project pitcher K's, compare to lines.

Run with:
    python -m bets.main
"""

from __future__ import annotations

import csv
from datetime import date

from dotenv import load_dotenv
from tabulate import tabulate

from .config import (
    LEAGUE_K_PCT,
    OUTPUT_DIR,
    PARK_K_FACTORS,
    PARK_K_FACTOR_DEFAULT,
    PROJECT_ROOT,
)
from .fetch import (
    lineup_k_rate,
    pitcher_stats,
    pitcher_swstr_lookup,
    team_k_rate,
    todays_probable_starters,
)
from .model import (
    ev_per_dollar,
    prob_over_poisson,
    project_pitcher_ks_v0,
    project_pitcher_ks_v1,
    project_pitcher_ks_v2,
)
from .odds import fetch_pitcher_k_lines, has_api_key, match_line
from .web import generate as generate_dashboard

load_dotenv(PROJECT_ROOT / ".env")


def run(target_date: date | None = None) -> None:
    target_date = target_date or date.today()
    season = target_date.year
    starters = todays_probable_starters(target_date)
    if not starters:
        print(f"No probable starters listed for {target_date.isoformat()}.")
        return

    if has_api_key():
        try:
            lines = fetch_pitcher_k_lines(target_date)
            print(f"Fetched {len(lines)} pitcher K lines from sportsbook.\n")
        except Exception as e:  # noqa: BLE001
            print(f"Failed to fetch lines: {e}\n")
            lines = []
    else:
        lines = []
        print(
            "ODDS_API_KEY not set — skipping line comparison. "
            "See README for setup.\n"
        )

    swstr_lookup = pitcher_swstr_lookup(season)
    if swstr_lookup:
        print(f"Loaded SwStr% for {len(swstr_lookup)} pitchers.\n")
    else:
        print("SwStr% lookup unavailable — v2 will fall back to actual K%.\n")

    rows = []
    for s in starters:
        ps = pitcher_stats(s["pitcher_id"], season)
        team_opp_k = team_k_rate(s["opp_team_id"], season)

        opp_lineup_ids = s.get("opp_lineup_ids") or []
        if opp_lineup_ids:
            lineup_opp_k = lineup_k_rate(opp_lineup_ids, season)
            opp_k = lineup_opp_k if lineup_opp_k > 0 else team_opp_k
            opp_k_source = "lineup" if lineup_opp_k > 0 else "team"
        else:
            opp_k = team_opp_k
            opp_k_source = "team"

        swstr = swstr_lookup.get(s["pitcher_id"], 0.0)
        park_factor = PARK_K_FACTORS.get(s["home_team_id"], PARK_K_FACTOR_DEFAULT)

        v0 = project_pitcher_ks_v0(ps["season_k_pct"], ps["recent_k_pct"])
        v1 = project_pitcher_ks_v1(
            season_k_pct=ps["season_k_pct"],
            recent_k_pct=ps["recent_k_pct"],
            opp_team_k_pct=team_opp_k,
            season_bf_per_start=ps["season_bf_per_start"],
            recent_bf_per_start=ps["recent_bf_per_start"],
            league_k_pct=LEAGUE_K_PCT,
        )
        v2 = project_pitcher_ks_v2(
            season_k_pct=ps["season_k_pct"],
            recent_k_pct=ps["recent_k_pct"],
            swstr_pct=swstr,
            opp_k_pct=opp_k,
            season_bf_per_start=ps["season_bf_per_start"],
            recent_bf_per_start=ps["recent_bf_per_start"],
            park_factor=park_factor,
            league_k_pct=LEAGUE_K_PCT,
        )

        row: dict = {
            "date": target_date.isoformat(),
            "game_pk": s["game_pk"],
            "pitcher_id": s["pitcher_id"],
            "pitcher": s["pitcher_name"],
            "opp": s["opp_team_name"],
            "season_k_pct": round(ps["season_k_pct"], 3),
            "recent_k_pct": round(ps["recent_k_pct"], 3),
            "swstr_pct": round(swstr, 3),
            "opp_k_pct": round(opp_k, 3),
            "opp_k_source": opp_k_source,
            "park_factor": park_factor,
            "matchup_k_pct": round(v2["matchup_k_pct"], 3),
            "exp_bf": round(v2["expected_bf"], 1),
            "proj_ks_v0": round(v0, 2),
            "proj_ks_v1": round(v1["proj_ks"], 2),
            "proj_ks_v2": round(v2["proj_ks"], 2),
        }

        line_data = match_line(s["pitcher_name"], lines)
        if line_data and "line" in line_data:
            line = line_data["line"]
            over_odds = line_data.get("over_odds")
            under_odds = line_data.get("under_odds")
            # Edge / EV calc uses the latest model (v2).
            p_over = prob_over_poisson(line, v2["proj_ks"])
            # Multi-book consensus stripped of vig — better market estimate
            # than a single book's no-vig calc.
            novig_over = line_data.get("consensus_p_over", 0.0)
            row["line"] = line
            row["over_odds"] = over_odds
            row["over_book"] = line_data.get("over_book")
            row["under_odds"] = under_odds
            row["under_book"] = line_data.get("under_book")
            row["n_books"] = line_data.get("n_books", 0)
            row["p_over"] = round(p_over, 3)
            row["novig_over"] = round(novig_over, 3)
            row["edge"] = round(p_over - novig_over, 3)
            if over_odds is not None:
                row["ev_over"] = round(ev_per_dollar(over_odds, p_over), 3)
            else:
                row["ev_over"] = None
            if under_odds is not None:
                row["ev_under"] = round(ev_per_dollar(under_odds, 1 - p_over), 3)
            else:
                row["ev_under"] = None
        else:
            row["line"] = None
            row["over_odds"] = None
            row["over_book"] = None
            row["under_odds"] = None
            row["under_book"] = None
            row["n_books"] = 0
            row["p_over"] = None
            row["novig_over"] = None
            row["edge"] = None
            row["ev_over"] = None
            row["ev_under"] = None

        rows.append(row)

    rows.sort(
        key=lambda r: (
            r["edge"] if r["edge"] is not None else -999,
            r["proj_ks_v2"],
        ),
        reverse=True,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    if any(r["line"] is not None for r in rows):
        display_cols = [
            "pitcher",
            "opp",
            "proj_ks_v2",
            "line",
            "over_odds",
            "under_odds",
            "p_over",
            "novig_over",
            "edge",
            "ev_over",
        ]
    else:
        display_cols = [
            "pitcher",
            "opp",
            "swstr_pct",
            "opp_k_pct",
            "matchup_k_pct",
            "exp_bf",
            "proj_ks_v1",
            "proj_ks_v2",
        ]

    display_rows = [{k: r[k] for k in display_cols} for r in rows]
    print(tabulate(display_rows, headers="keys", floatfmt=".3f"))
    print(f"\nWrote {len(rows)} projections to {out_path}")

    generate_dashboard(target_date)


if __name__ == "__main__":
    run()
