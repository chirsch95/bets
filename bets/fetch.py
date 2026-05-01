"""Data fetching for MLB schedules, probable starters, pitcher and team stats.

Most data flows through the public MLB Stats API (no auth).
SwStr% comes from FanGraphs via pybaseball, cached to disk.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

import requests

from .config import DATA_DIR

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
HTTP_TIMEOUT = 10
SWSTR_CACHE_TTL_HOURS = 12


def todays_probable_starters(target_date: date | None = None) -> list[dict]:
    """Return probable starters for the given date (default: today).

    Each entry has keys:
        game_pk, home_team, away_team, home_team_id, away_team_id,
        pitcher_id, pitcher_name, is_home, opp_team_id, opp_team_name
    """
    target_date = target_date or date.today()
    url = (
        f"{MLB_STATS_BASE}/schedule"
        f"?sportId=1&date={target_date.isoformat()}"
        f"&hydrate=probablePitcher,lineups"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()

    starters: list[dict] = []
    for date_block in payload.get("dates", []):
        for game in date_block.get("games", []):
            game_pk = game["gamePk"]
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_team_id = home["team"]["id"]
            away_team_id = away["team"]["id"]
            home_team_name = home["team"]["name"]
            away_team_name = away["team"]["name"]
            lineups = game.get("lineups", {}) or {}

            for side, is_home in (("home", True), ("away", False)):
                team = game["teams"][side]
                pitcher = team.get("probablePitcher")
                if not pitcher:
                    continue
                opp_side = "away" if is_home else "home"
                opp_team_id = away_team_id if is_home else home_team_id
                opp_team_name = away_team_name if is_home else home_team_name

                opp_players = lineups.get(f"{opp_side}Players") or []
                opp_lineup_ids = [p["id"] for p in opp_players if "id" in p]
                # Slot is the batting-order position (1 = leadoff). Order in
                # the API response is the order of the lineup card.
                opp_lineup = [
                    {
                        "id": p["id"],
                        "name": p.get("fullName", ""),
                        "slot": idx + 1,
                    }
                    for idx, p in enumerate(opp_players)
                    if "id" in p
                ]

                starters.append(
                    {
                        "game_pk": game_pk,
                        "home_team": home_team_name,
                        "away_team": away_team_name,
                        "home_team_id": home_team_id,
                        "away_team_id": away_team_id,
                        "pitcher_id": pitcher["id"],
                        "pitcher_name": pitcher["fullName"],
                        "is_home": is_home,
                        "opp_team_id": opp_team_id,
                        "opp_team_name": opp_team_name,
                        "opp_lineup_ids": opp_lineup_ids,
                        "opp_lineup": opp_lineup,
                    }
                )
    return starters


def pitcher_stats(pitcher_id: int, season: int | None = None) -> dict:
    """Return season + recent K rates and BF/start for a pitcher.

    Keys returned:
        season_k_pct, recent_k_pct, season_bf,
        season_bf_per_start, recent_bf_per_start, recent_starts
    Returns zeros if data is unavailable (e.g., MLB debut with no record).
    """
    season = season or date.today().year
    url = (
        f"{MLB_STATS_BASE}/people/{pitcher_id}/stats"
        f"?stats=season,gameLog&group=pitching&season={season}"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        return _empty_pitcher_stats()
    payload = resp.json()

    season_k_pct = 0.0
    season_bf = 0
    season_bf_per_start = 0.0
    recent_k_pct = 0.0
    recent_bf_per_start = 0.0
    recent_starts = 0

    for stat_group in payload.get("stats", []):
        type_name = stat_group.get("type", {}).get("displayName")
        splits = stat_group.get("splits", [])

        if type_name == "season":
            for split in splits:
                stat = split.get("stat", {})
                bf = int(stat.get("battersFaced", 0) or 0)
                ks = int(stat.get("strikeOuts", 0) or 0)
                gs = int(stat.get("gamesStarted", 0) or 0)
                if bf:
                    season_k_pct = ks / bf
                    season_bf = bf
                if gs:
                    season_bf_per_start = bf / gs

        elif type_name == "gameLog":
            # Only consider games the pitcher started.
            started = [
                s
                for s in splits
                if int(s.get("stat", {}).get("gamesStarted", 0) or 0) == 1
            ]
            recent = started[-5:]
            ks = sum(int(s.get("stat", {}).get("strikeOuts", 0) or 0) for s in recent)
            bf = sum(int(s.get("stat", {}).get("battersFaced", 0) or 0) for s in recent)
            recent_starts = len(recent)
            if bf:
                recent_k_pct = ks / bf
            if recent_starts:
                recent_bf_per_start = bf / recent_starts

    return {
        "season_k_pct": season_k_pct,
        "recent_k_pct": recent_k_pct,
        "season_bf": season_bf,
        "season_bf_per_start": season_bf_per_start,
        "recent_bf_per_start": recent_bf_per_start,
        "recent_starts": recent_starts,
    }


@lru_cache(maxsize=64)
def team_k_rate(team_id: int, season: int) -> float:
    """Return team-level batting K% (strikeOuts / plateAppearances) for the season.

    Cached per run to avoid duplicate calls for the same team.
    Returns 0.0 if data is unavailable.
    """
    url = (
        f"{MLB_STATS_BASE}/teams/{team_id}/stats"
        f"?stats=season&group=hitting&season={season}"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        return 0.0
    payload = resp.json()
    for stat_group in payload.get("stats", []):
        for split in stat_group.get("splits", []):
            stat = split.get("stat", {})
            ks = int(stat.get("strikeOuts", 0) or 0)
            pa = int(stat.get("plateAppearances", 0) or 0)
            if pa:
                return ks / pa
    return 0.0


def _empty_pitcher_stats() -> dict:
    return {
        "season_k_pct": 0.0,
        "recent_k_pct": 0.0,
        "season_bf": 0,
        "season_bf_per_start": 0.0,
        "recent_bf_per_start": 0.0,
        "recent_starts": 0,
    }


def hitter_stats_batch(player_ids: list[int], season: int) -> dict[int, dict]:
    """Return per-hitter season + recent K rates in a single batched call.

    Output: {player_id: {season_k_pct, recent_k_pct, season_pa, recent_pa, name}}
    Falls back to zeros for any hitter without recorded PAs.
    """
    if not player_ids:
        return {}
    ids_str = ",".join(str(p) for p in player_ids)
    url = (
        f"{MLB_STATS_BASE}/people"
        f"?personIds={ids_str}"
        f"&hydrate=stats(group=hitting,type=[season,gameLog],season={season})"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        return {}
    payload = resp.json()

    out: dict[int, dict] = {}
    for person in payload.get("people", []):
        pid = person.get("id")
        if pid is None:
            continue
        season_k_pct = 0.0
        season_pa = 0
        recent_k_pct = 0.0
        recent_pa = 0
        for stat_block in person.get("stats", []):
            type_name = stat_block.get("type", {}).get("displayName")
            splits = stat_block.get("splits", [])
            if type_name == "season":
                for split in splits:
                    stat = split.get("stat", {})
                    pa = int(stat.get("plateAppearances", 0) or 0)
                    ks = int(stat.get("strikeOuts", 0) or 0)
                    if pa:
                        season_k_pct = ks / pa
                        season_pa = pa
                    break
            elif type_name == "gameLog":
                recent = splits[-15:]  # last ~15 games of action
                ks = sum(
                    int(s.get("stat", {}).get("strikeOuts", 0) or 0)
                    for s in recent
                )
                pa = sum(
                    int(s.get("stat", {}).get("plateAppearances", 0) or 0)
                    for s in recent
                )
                if pa:
                    recent_k_pct = ks / pa
                    recent_pa = pa
        out[int(pid)] = {
            "name": person.get("fullName", ""),
            "season_k_pct": season_k_pct,
            "recent_k_pct": recent_k_pct,
            "season_pa": season_pa,
            "recent_pa": recent_pa,
        }
    return out


def lineup_k_rate(player_ids: list[int], season: int) -> float:
    """Average season K% (strikeOuts / plateAppearances) across the listed batters.

    One batched /people request rather than N separate calls.
    Returns 0.0 if no players or no data.
    """
    if not player_ids:
        return 0.0
    ids_str = ",".join(str(p) for p in player_ids)
    url = (
        f"{MLB_STATS_BASE}/people"
        f"?personIds={ids_str}"
        f"&hydrate=stats(group=hitting,type=season,season={season})"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        return 0.0
    payload = resp.json()

    rates: list[float] = []
    for person in payload.get("people", []):
        for stat_block in person.get("stats", []):
            for split in stat_block.get("splits", []):
                stat = split.get("stat", {})
                ks = int(stat.get("strikeOuts", 0) or 0)
                pa = int(stat.get("plateAppearances", 0) or 0)
                if pa:
                    rates.append(ks / pa)
                    break
            break  # only consider the first stat block per player

    return sum(rates) / len(rates) if rates else 0.0


# ---------- SwStr% lookup (FanGraphs via pybaseball, cached to disk) ----------


def _swstr_cache_path(season: int) -> Path:
    return DATA_DIR / f"swstr_{season}.json"


def _load_swstr_cache(season: int) -> dict[int, float] | None:
    path = _swstr_cache_path(season)
    if not path.exists():
        return None
    try:
        with path.open() as f:
            blob = json.load(f)
        gen_at = datetime.fromisoformat(blob["generated_at"])
        age_hours = (datetime.now(timezone.utc) - gen_at).total_seconds() / 3600
        if age_hours > SWSTR_CACHE_TTL_HOURS:
            return None
        return {int(k): float(v) for k, v in blob["data"].items()}
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def _save_swstr_cache(season: int, data: dict[int, float]) -> None:
    path = _swstr_cache_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data": {str(k): v for k, v in data.items()},
            },
            f,
        )


def pitcher_swstr_lookup(season: int) -> dict[int, float]:
    """Return {mlb_id: SwStr% (fraction)} from Baseball Savant.

    SwStr% (swinging strikes / total pitches) is computed from Savant's
    whiff_percent (per swing) × swing_percent (per pitch). Cached for 12h
    to avoid re-fetching the full leaderboard every run. Returns empty
    dict on failure (model falls back to actual K%).
    """
    cached = _load_swstr_cache(season)
    if cached is not None:
        return cached

    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={season}&type=pitcher&filter=&min=10"
        "&selections=pa,whiff_percent,swing_percent&csv=true"
    )
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    try:
        import io

        import pandas as pd
    except ImportError:
        return {}

    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception:  # noqa: BLE001
        return {}

    required = {"player_id", "whiff_percent", "swing_percent"}
    if df.empty or not required.issubset(df.columns):
        return {}

    result: dict[int, float] = {}
    for _, row in df.iterrows():
        pid = row.get("player_id")
        whiff = row.get("whiff_percent")
        swing = row.get("swing_percent")
        if pid is None or pd.isna(pid) or pd.isna(whiff) or pd.isna(swing):
            continue
        # Both metrics are in percentage points (e.g. 22.4 means 22.4%).
        result[int(pid)] = float(whiff) * float(swing) / 10000.0

    if result:
        _save_swstr_cache(season, result)
    return result
