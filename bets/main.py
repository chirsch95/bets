"""CLI entry point: fetch today's slate, project pitcher K's, compare to lines.

Run with:
    python -m bets.main
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date

from dotenv import load_dotenv
from tabulate import tabulate

from .config import (
    DATA_DIR,
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
from . import model_ml
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


def run(target_date: date | None = None, force_fetch: bool = False) -> None:
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
    covered = {normalize_name(p["pitcher_name"]) for p in preserved}

    skip_pairs: set[frozenset[str]] = set()
    if covered and not force_fetch:
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

    # Even when every event would be filtered by skip_pairs, the underlying
    # /events list call still costs 1 credit. Short-circuit when every
    # starter is already priced — preserved data flows through merge_lines
    # and we spend zero credits on the re-run. --force-fetch bypasses for
    # mid-day line-movement re-runs.
    all_covered = (
        not force_fetch
        and bool(covered)
        and all(normalize_name(s["pitcher_name"]) in covered for s in starters)
    )

    if force_fetch:
        # Books pull player prop markets once a game starts, so fetching
        # odds for started games wastes credits and returns nothing useful.
        # Skip them even in force-fetch mode.
        from . import live as _lv
        try:
            started_pks = _lv._started_game_pks(target_date.isoformat())
            if started_pks:
                by_game_f: dict[int, list[dict]] = {}
                for s in starters:
                    by_game_f.setdefault(s["game_pk"], []).append(s)
                for gpk, gs in by_game_f.items():
                    if gpk in started_pks and gs:
                        first = gs[0]
                        skip_pairs.add(frozenset({
                            canonical_team_name(first["home_team"]),
                            canonical_team_name(first["away_team"]),
                        }))
        except Exception:  # noqa: BLE001
            pass
        n_started = len(skip_pairs)
        if n_started:
            print(f"--force-fetch: re-pulling pre-game lines ({n_started} started game(s) skipped — props pulled mid-game).\n")
        else:
            print("--force-fetch: re-pulling every game (bypassing all_covered guard).\n")

    if all_covered:
        print(
            "Every starter already priced from an earlier run today — "
            "skipping Odds API call entirely (0 credits).\n"
        )
        lines = []
    elif has_api_key():
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

    # v2bc shadow (2026-06-09): v2 minus its own trailing 30-day mean bias.
    # v2 has been over-projecting Ks by a growing margin (+0.28 K May →
    # +0.49 K June), which inflates Over edges; the all-time Platt fit can't
    # track drift. Shadow-rollout discipline applies — v2bc rides along as
    # columns only (no production routing). Pre-committed promotion bar:
    # see SHADOW_V2BC_2026-06-09.md (check alongside the ~7/04 ML review).
    try:
        from .residuals import trailing_mean_bias
        v2_bias = trailing_mean_bias("v2", window_days=30, up_to=target_date)
    except Exception as e:  # noqa: BLE001
        print(f"v2bc shadow: bias lookup failed ({e}) — using 0.0")
        v2_bias = 0.0
    if v2_bias:
        print(f"v2bc shadow: correcting v2 by {-v2_bias:+.2f} K "
              f"(trailing 30-day mean bias {v2_bias:+.2f} K)\n")

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

        # Shadow-only ML projection. Returns None when no trained model
        # is on disk yet — proj_ks_ml is then written as an empty cell
        # and ignored by every downstream consumer (UI, edge calc, P&L).
        ml_proj = model_ml.predict({
            "season_k_pct": ps["season_k_pct"],
            "recent_k_pct": ps["recent_k_pct"],
            "swstr_pct": swstr,
            "opp_k_pct": opp_k,
            "park_factor": park_factor,
            "exp_bf": v2["expected_bf"],
            "is_home": s["is_home"],
        })

        # Persisted as JSON so we can later join against batter handedness /
        # splits without re-fetching the lineup card. Empty list when the
        # lineup wasn't posted yet at slate time.
        opp_lineup_json = json.dumps(s.get("opp_lineup") or [], ensure_ascii=False)

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
            # Bias-corrected v2 shadow — see the v2_bias comment above the
            # loop. Floor at 0.05 so the Poisson mean stays valid.
            "proj_ks_v2bc": round(max(0.05, v2["proj_ks"] - v2_bias), 2),
            "proj_ks_ml": round(ml_proj, 2) if ml_proj is not None else None,
            "opp_lineup_json": opp_lineup_json,
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
            # Platt-calibrated shadow columns. Raw v2/ML probabilities are
            # systematically overconfident — see 2026-05-11 calibration
            # analysis. These columns let us compare cal vs raw outcomes
            # over time without changing production routing today.
            from . import calibration as _cal
            cal_p_over_v2 = _cal.apply(p_over, "v2")
            row["cal_p_over_v2"] = round(cal_p_over_v2, 3) if cal_p_over_v2 is not None else None
            row["cal_edge_v2"] = round(cal_p_over_v2 - novig_over, 3) if cal_p_over_v2 is not None else None
            if ml_proj is not None:
                raw_p_over_ml = prob_over_poisson(line, ml_proj)
                cal_p_over_ml = _cal.apply(raw_p_over_ml, "ml")
                row["cal_p_over_ml"] = round(cal_p_over_ml, 3) if cal_p_over_ml is not None else None
                row["cal_edge_ml"] = round(cal_p_over_ml - novig_over, 3) if cal_p_over_ml is not None else None
            else:
                row["cal_p_over_ml"] = None
                row["cal_edge_ml"] = None
            # v2bc shadow probabilities. Until calibration.fit() has ≥30
            # graded v2bc rows, apply() falls back to the raw Poisson prob.
            raw_p_over_v2bc = prob_over_poisson(line, row["proj_ks_v2bc"])
            cal_p_over_v2bc = _cal.apply(raw_p_over_v2bc, "v2bc")
            row["cal_p_over_v2bc"] = round(cal_p_over_v2bc, 3) if cal_p_over_v2bc is not None else None
            row["cal_edge_v2bc"] = round(cal_p_over_v2bc - novig_over, 3) if cal_p_over_v2bc is not None else None
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
            row["cal_p_over_v2"] = None
            row["cal_edge_v2"] = None
            row["cal_p_over_ml"] = None
            row["cal_edge_ml"] = None
            row["cal_p_over_v2bc"] = None
            row["cal_edge_v2bc"] = None

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

    # Slate snapshot — the canonical "what we'd have bet at" record. Per
    # pitcher, this row freezes at first pitch; until then, every refresh
    # rewrites the row so a lineup post (the biggest single accuracy bump
    # of the day) lands in the snapshot. settle.py grades against this.
    # Multi-process safety: we hold _pipeline_lock in the server and CLI
    # is a single process, so no concurrent writer concern.
    slate_path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}_slate.csv"
    from . import live as _live
    try:
        started_pks = _live._started_game_pks(target_date.isoformat())
    except Exception as e:  # noqa: BLE001
        print(f"slate snapshot: couldn't fetch started games ({e}); freezing all rows")
        started_pks = set()
    existing: dict[int, dict] = {}
    if slate_path.exists():
        with slate_path.open() as f:
            for r in csv.DictReader(f):
                pid = r.get("pitcher_id")
                if pid:
                    try:
                        existing[int(pid)] = r
                    except ValueError:
                        pass
    fieldnames = list(rows[0].keys())
    merged_rows: list[dict] = []
    pinned_count = 0
    fresh_pids: set[int] = set()
    for r in rows:
        pid_raw = r.get("pitcher_id")
        gpk_raw = r.get("game_pk")
        try:
            pid = int(pid_raw) if pid_raw not in (None, "") else None
            gpk = int(gpk_raw) if gpk_raw not in (None, "") else None
        except (TypeError, ValueError):
            pid, gpk = None, None
        if pid is not None:
            fresh_pids.add(pid)
        if pid is not None and gpk in started_pks and pid in existing:
            merged_rows.append(existing[pid])
            pinned_count += 1
        else:
            merged_rows.append(r)
    # A pitcher who was in the morning slate but got scratched and dropped
    # from the live CSV still needs to settle — keep his snapshot row.
    for pid, r in existing.items():
        if pid not in fresh_pids:
            merged_rows.append(r)
            pinned_count += 1
    # Atomic write: build into a temp file, then rename. Snapshot is now
    # rewritten on every refresh (not write-once like before), so a crash
    # mid-write must not leave a partial slate that breaks settle/replay.
    tmp_path = slate_path.with_suffix(slate_path.suffix + ".tmp")
    with tmp_path.open("w", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged_rows)
    os.replace(tmp_path, slate_path)
    print(
        f"Wrote slate snapshot → {slate_path} "
        f"({pinned_count} row(s) pinned to prior snapshot, "
        f"{len(merged_rows) - pinned_count} refreshed)"
    )

    # Sportsbook CLOSING-line capture for CLV (2026-06-09). Every pipeline
    # run upserts each pitcher's CURRENT market numbers into
    # data/book_close_<date>.json — but only while his game hasn't started,
    # so the last pre-game refresh's value stands as the close (same guard
    # as capture_ud_close.py). Fully automatic, no screenshots: this is the
    # CLV instrument that replaced the dropped UD closing-screenshot
    # workflow (UD_LAB.md decisions). com.bets.refresh-close runs afternoon/
    # evening refreshes on the Air so the last snapshot lands near first
    # pitch. Failures never block the pipeline.
    try:
        from datetime import datetime, timezone
        close_path = DATA_DIR / f"book_close_{target_date.isoformat()}.json"
        close_board: dict[str, dict] = {}
        if close_path.exists():
            try:
                close_board = json.loads(close_path.read_text())
            except (json.JSONDecodeError, OSError):
                close_board = {}
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        close_updates = 0
        for r in rows:
            pid_raw = r.get("pitcher_id")
            gpk_raw = r.get("game_pk")
            try:
                pid = int(pid_raw) if pid_raw not in (None, "") else None
                gpk = int(gpk_raw) if gpk_raw not in (None, "") else None
            except (TypeError, ValueError):
                continue
            if pid is None or r.get("line") is None:
                continue
            if gpk is not None and gpk in started_pks:
                continue  # game underway — keep the earlier pre-game value
            close_board[str(pid)] = {
                "line": r.get("line"),
                "over_odds": r.get("over_odds"),
                "under_odds": r.get("under_odds"),
                "novig_over": r.get("novig_over"),
                "ts": now_iso,
            }
            close_updates += 1
        if close_board:
            tmp_close = close_path.with_suffix(".json.tmp")
            tmp_close.write_text(json.dumps(close_board, indent=2, sort_keys=True) + "\n")
            os.replace(tmp_close, close_path)
            print(f"Book-close board → {close_path.name} ({close_updates} pre-game row(s) updated)")
    except Exception as e:  # noqa: BLE001
        print(f"book-close capture skipped: {e}")

    # Snapshot the parlay-suggester output alongside the slate so we
    # later have a record of exactly which 2/3-leg cards the dashboard
    # showed at slate time. force=True so the suggestions track the
    # slate updates (e.g. when a lineup posts mid-day and shifts edges).
    # Failures don't block the slate snapshot.
    try:
        from . import parlay_suggest as _pl
        _pl.write_suggestions(target_date, force=True)
        _pl.write_shadow_suggestions(target_date, force=True)
    except Exception as e:
        print(f"parlay_suggest snapshot skipped: {e}")

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
    import sys

    if "--train-ml" in sys.argv:
        model_ml.train_cli()
    else:
        run(force_fetch="--force-fetch" in sys.argv)
