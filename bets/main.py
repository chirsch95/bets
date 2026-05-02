"""CLI entry point: fetch today's slate, project pitcher K's, compare to lines.

Run with:
    python -m bets.main
"""

from __future__ import annotations

import csv
import os
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
from .odds import (
    canonical_team_name,
    fetch_pitcher_k_lines,
    has_api_key,
    load_previous_pitcher_lines,
    match_line,
    merge_lines,
    normalize_name,
)
from .web import generate as generate_dashboard

load_dotenv(PROJECT_ROOT / ".env")


def run(target_date: date | None = None) -> None:
    target_date = target_date or date.today()
    season = target_date.year
    starters = todays_probable_starters(target_date)
    if not starters:
        print(f"No probable starters listed for {target_date.isoformat()}.")
        return

    # Load preserved lines first so we can tell the fetcher which games
    # are already fully covered — those events get skipped (one Odds API
    # credit each) and the existing data flows through merge_lines below.
    out_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    preserved = load_previous_pitcher_lines(out_path)

    skip_pairs: set[frozenset[str]] = set()
    if preserved:
        covered = {normalize_name(p["pitcher_name"]) for p in preserved}
        # Group starters by game and skip a game only if BOTH starters
        # are already covered (otherwise we still need the event call to
        # pick up the missing side).
        by_game: dict[int, list[dict]] = {}
        for s in starters:
            by_game.setdefault(s["game_pk"], []).append(s)
        for game_starters in by_game.values():
            if len(game_starters) == 2 and all(
                normalize_name(g["pitcher_name"]) in covered for g in game_starters
            ):
                first = game_starters[0]
                skip_pairs.add(frozenset({
                    canonical_team_name(first["home_team"]),
                    canonical_team_name(first["away_team"]),
                }))

    if has_api_key():
        try:
            lines = fetch_pitcher_k_lines(target_date, skip_team_pairs=skip_pairs)
            print(f"Fetched {len(lines)} pitcher K lines from sportsbook.")
            if skip_pairs:
                print(f"Skipped {len(skip_pairs)} game(s) already covered by earlier run today.")
            print()
        except Exception as e:  # noqa: BLE001
            print(f"Failed to fetch lines: {e}\n")
            lines = []
    else:
        lines = []
        print(
            "ODDS_API_KEY not set — skipping line comparison. "
            "See README for setup.\n"
        )

    # Books pull markets once games start, so a later run otherwise wipes
    # the morning capture. Fresh fetches still win on overlap; preserved
    # fills the gaps for skipped games + games that didn't return data.
    if preserved:
        merged = merge_lines(lines, preserved, "pitcher_name")
        added = len(merged) - len(lines)
        if added > 0:
            print(f"Preserved {added} line(s) from earlier run today.\n")
        lines = merged

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
            "game_datetime_utc": s.get("game_datetime_utc") or "",
            "pitcher_id": s["pitcher_id"],
            "pitcher": s["pitcher_name"],
            "opp": s["opp_team_name"],
            "is_home": s["is_home"],
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
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # Freeze the first run of the day as the canonical "slate" — the
    # state we'd actually have bet on. Later runs overwrite out_path as
    # lines move, but the slate file stays put so settle.py can grade
    # picks against the morning state, not whatever survived to gametime.
    # O_CREAT|O_EXCL races atomically against any concurrent run (CLI
    # vs server vs GH Actions), so we never overwrite an existing snapshot.
    slate_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}_slate.csv"
    try:
        fd = os.open(slate_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as dst, out_path.open("rb") as src:
            dst.write(src.read())
        print(f"Wrote slate snapshot → {slate_path}")

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
