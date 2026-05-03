"""CLI entry point: fetch today's slate, project hitter K's, compare to lines.

Run with:
    python -m bets.hitters

Mirrors bets.main but for batter strikeouts instead of pitcher strikeouts.
"""

from __future__ import annotations

import csv
from datetime import date

from dotenv import load_dotenv
from tabulate import tabulate

from .config import (
    DEFAULT_LINEUP_PA,
    LEAGUE_K_PCT,
    LINEUP_PA,
    OUTPUT_DIR,
    PARK_K_FACTORS,
    PARK_K_FACTOR_DEFAULT,
    PROJECT_ROOT,
)
from .fetch import (
    hitter_stats_batch,
    pitcher_stats,
    todays_probable_starters,
)
from .model import (
    blended_k_rate,
    ev_per_dollar,
    prob_over_poisson,
    project_hitter_ks_v0,
)
from .odds import (
    canonical_team_name,
    fetch_hitter_k_lines,
    has_api_key,
    load_previous_hitter_lines,
    match_hitter_line,
    merge_lines,
    normalize_name,
)

load_dotenv(PROJECT_ROOT / ".env")


def run(target_date: date | None = None) -> None:
    target_date = target_date or date.today()
    season = target_date.year
    starters = todays_probable_starters(target_date)
    if not starters:
        print(f"No probable starters listed for {target_date.isoformat()}.")
        return

    # Load preserved lines first so we can tell the fetcher which games
    # are already covered by an earlier run today — those events get
    # skipped (one Odds API credit each).
    out_path = OUTPUT_DIR / f"hitter_ks_{target_date.isoformat()}.csv"
    preserved = load_previous_hitter_lines(out_path)

    # Map each batter (by normalized name) to (game_pk, batter_team) using
    # today's lineup cards. Lets us attribute preserved lines back to a
    # game without storing extra columns in the line dict.
    name_to_game_team: dict[str, tuple[int, str]] = {}
    for s in starters:
        # Batter's team = the pitcher's opponent. canonical_team_name
        # ensures the comparison matches the form home_team/away_team
        # come back in.
        team = canonical_team_name(s.get("opp_team_name", ""))
        for batter in s.get("opp_lineup") or []:
            bname = normalize_name(batter.get("name") or "")
            if bname:
                name_to_game_team[bname] = (s["game_pk"], team)

    covered_teams_by_game: dict[int, set[str]] = {}
    for entry in preserved:
        info = name_to_game_team.get(normalize_name(entry.get("hitter_name", "")))
        if not info:
            continue
        game_pk, team = info
        covered_teams_by_game.setdefault(game_pk, set()).add(team)

    skip_pairs: set[frozenset[str]] = set()
    for s in starters:
        home = canonical_team_name(s.get("home_team", ""))
        away = canonical_team_name(s.get("away_team", ""))
        if {home, away} <= covered_teams_by_game.get(s["game_pk"], set()):
            skip_pairs.add(frozenset({home, away}))

    # Skip the API entirely when every game is already covered — the
    # /events list call alone would still cost 1 credit otherwise.
    all_game_pks = {s["game_pk"] for s in starters}
    all_covered = bool(skip_pairs) and len(skip_pairs) == len(all_game_pks)

    if all_covered:
        print(
            "Every game's hitter lines already pulled by an earlier run today — "
            "skipping Odds API call entirely (0 credits).\n"
        )
        lines = []
    elif has_api_key():
        try:
            lines = fetch_hitter_k_lines(target_date, skip_team_pairs=skip_pairs)
            print(f"Fetched {len(lines)} hitter K lines from sportsbook.")
            if skip_pairs:
                print(f"Skipped {len(skip_pairs)} game(s) already covered by earlier run today.")
            print()
        except Exception as e:  # noqa: BLE001
            print(f"Failed to fetch hitter K lines: {e}\n")
            lines = []
    else:
        lines = []
        print(
            "ODDS_API_KEY not set — skipping line comparison. "
            "See README for setup.\n"
        )

    if preserved:
        merged = merge_lines(lines, preserved, "hitter_name")
        added = len(merged) - len(lines)
        if added > 0:
            print(f"Preserved {added} line(s) from earlier run today.\n")
        lines = merged

    # Collect every batter ID across confirmed lineups, then batch-fetch
    # their hitting stats in one MLB API call.
    all_ids = sorted(
        {
            entry["id"]
            for s in starters
            for entry in s.get("opp_lineup", [])
        }
    )
    if not all_ids:
        print("No confirmed lineups posted yet — hitter projections need lineups.")
        return

    hitter_stats = hitter_stats_batch(all_ids, season)

    # Per-pitcher K%: blend season + recent the same way pitcher pipeline does.
    # We need this as the matchup partner in log5.
    pitcher_k_cache: dict[int, float] = {}

    rows: list[dict] = []
    for s in starters:
        opp_lineup = s.get("opp_lineup") or []
        if not opp_lineup:
            continue

        pid = s["pitcher_id"]
        if pid not in pitcher_k_cache:
            ps = pitcher_stats(pid, season)
            pitcher_k_cache[pid] = blended_k_rate(
                ps["season_k_pct"], ps["recent_k_pct"]
            )
        opp_pitcher_k_pct = pitcher_k_cache[pid]

        # Park is set by the home team — same lookup the pitcher pipeline uses.
        park_factor = PARK_K_FACTORS.get(
            s["home_team_id"], PARK_K_FACTOR_DEFAULT
        )

        for batter in opp_lineup:
            bid = batter["id"]
            slot = batter.get("slot") or 0
            stats = hitter_stats.get(bid, {})
            season_k = stats.get("season_k_pct", 0.0)
            recent_k = stats.get("recent_k_pct", 0.0)
            expected_pa = LINEUP_PA.get(slot, DEFAULT_LINEUP_PA)
            name = stats.get("name") or batter.get("name") or ""

            proj = project_hitter_ks_v0(
                season_k_pct=season_k,
                recent_k_pct=recent_k,
                opp_pitcher_k_pct=opp_pitcher_k_pct,
                expected_pa=expected_pa,
                park_factor=park_factor,
                league_k_pct=LEAGUE_K_PCT,
            )

            row: dict = {
                "date": target_date.isoformat(),
                "game_pk": s["game_pk"],
                "hitter_id": bid,
                "hitter": name,
                "slot": slot,
                "team": s["opp_team_name"],  # batter's team = pitcher's opp
                "opp_pitcher_id": pid,
                "opp_pitcher": s["pitcher_name"],
                "season_k_pct": round(season_k, 3),
                "recent_k_pct": round(recent_k, 3),
                "opp_pitcher_k_pct": round(opp_pitcher_k_pct, 3),
                "park_factor": park_factor,
                "matchup_k_pct": round(proj["matchup_k_pct"], 3),
                "expected_pa": round(expected_pa, 2),
                "proj_ks": round(proj["proj_ks"], 2),
            }

            line_data = match_hitter_line(name, lines) if name else None
            if line_data and "line" in line_data:
                line = line_data["line"]
                over_odds = line_data.get("over_odds")
                under_odds = line_data.get("under_odds")
                p_over = prob_over_poisson(line, proj["proj_ks"])
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
                row["ev_over"] = (
                    round(ev_per_dollar(over_odds, p_over), 3)
                    if over_odds is not None
                    else None
                )
                row["ev_under"] = (
                    round(ev_per_dollar(under_odds, 1 - p_over), 3)
                    if under_odds is not None
                    else None
                )
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

    if not rows:
        print("No hitter rows produced.")
        return

    rows.sort(
        key=lambda r: (
            r["edge"] if r["edge"] is not None else -999,
            r["proj_ks"],
        ),
        reverse=True,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    has_lines = any(r["line"] is not None for r in rows)
    if has_lines:
        display_cols = [
            "hitter",
            "team",
            "slot",
            "opp_pitcher",
            "proj_ks",
            "line",
            "over_odds",
            "under_odds",
            "p_over",
            "novig_over",
            "edge",
        ]
    else:
        display_cols = [
            "hitter",
            "team",
            "slot",
            "opp_pitcher",
            "season_k_pct",
            "recent_k_pct",
            "matchup_k_pct",
            "expected_pa",
            "proj_ks",
        ]
    display_rows = [
        {k: r[k] for k in display_cols} for r in rows[:30]
    ]
    print(tabulate(display_rows, headers="keys", floatfmt=".3f"))
    print(f"\nWrote {len(rows)} hitter projections to {out_path}")


if __name__ == "__main__":
    run()
