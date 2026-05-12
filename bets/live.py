"""Slate-pitcher lookup + live K-count fetching for the Bets tab.

Two responsibilities, both backing the local-only Bets tab UI:

  1. `slate_pitchers(target_date)` reads today's projection CSV and
     returns one row per pitcher with the fields the form's dropdown
     needs (id, name, opp, line, our model recommendation).

  2. `live_ks(pitcher_ids, target_date)` calls the MLB Stats API to
     return current K count + game state per pitcher. Lookup pattern:
     map pitcher_id → game_pk via the slate CSV, fetch the schedule
     once for game statuses, then fetch a boxscore per unique game_pk
     for K counts. Boxscore + schedule responses are cached in-memory
     for 60s so rapid refreshes don't hammer the MLB API.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date
from pathlib import Path

import requests

from .config import OUTPUT_DIR
from .fetch import HTTP_TIMEOUT, MLB_STATS_BASE

# Edge bands — kept in sync with web.py's FOCUS_EDGE_MIN/MAX/INVESTIGATE.
FOCUS_EDGE_MIN = 0.05
FOCUS_EDGE_MAX = 0.15
INVESTIGATE_EDGE = 0.20

# In-memory cache for MLB API responses. Keyed by descriptive string.
# Single-process Flask dev server, so a plain dict is fine.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60  # seconds — MLB box updates per half-inning anyway


def _cached(key: str, fetcher) -> dict:
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    val = fetcher()
    _CACHE[key] = (now, val)
    return val


def _safe_float(v):
    if v in ("", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_is_home(v) -> bool | None:
    """CSVs store bools as 'True'/'False' strings via DictWriter; tolerate
    legacy rows missing the column by returning None."""
    if v in ("", None):
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _safe_int(v):
    if v in ("", None):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _classify_edge(edge: float | None) -> tuple[str, str]:
    """Returns (cls, dir) matching the dashboard's classifier.

    cls: focus | investigate | noise | noline
    dir: over | under | "" (when no clear direction)
    """
    if edge is None:
        return ("noline", "")
    a = abs(edge)
    direction = "over" if edge > 0 else ("under" if edge < 0 else "")
    if a >= INVESTIGATE_EDGE:
        return ("investigate", direction)
    if FOCUS_EDGE_MIN <= a <= FOCUS_EDGE_MAX:
        return ("focus", direction)
    return ("noise", direction)


def _our_pick_label(cls: str, direction: str) -> str:
    if cls == "focus" and direction:
        return f"Bet {direction.upper()}"
    if cls == "investigate" and direction:
        return f"Verify {direction.upper()}?"
    if cls == "noline":
        return "No line"
    return "—"


def _load_slate_snapshot(target_date: date) -> dict[int, dict]:
    """Load the morning's frozen projection snapshot keyed by pitcher_id.

    The `_slate.csv` is whatever the first main.py run of the day wrote —
    the line/odds/edge state we'd actually have bet at. Empty dict if
    the snapshot is missing (early days before the feature, or the
    morning run hasn't completed yet).
    """
    path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}_slate.csv"
    if not path.exists():
        return {}
    out: dict[int, dict] = {}
    with path.open() as f:
        for r in csv.DictReader(f):
            pid = _safe_int(r.get("pitcher_id"))
            if pid is None:
                continue
            out[pid] = r
    return out


def _started_game_pks(target_date_iso: str) -> set[int]:
    """Return the set of game_pks where first pitch has been thrown.

    We pin to the slate snapshot from first pitch onward — not just from
    Final — because the rolling K%/BF stats `main.py` re-pulls fold the
    just-started outing back into the window as soon as the pitcher
    records any IP, dragging the displayed edge for an already-bet
    pitcher.

    Wrapped in a try/except so a transient MLB API failure doesn't break
    /api/slate-pitchers — we just fall through and display live values.
    """
    try:
        statuses = _game_status_map(target_date_iso)
    except Exception:  # noqa: BLE001
        return set()
    return {
        gpk for gpk, gs in statuses.items()
        if (gs.get("abstract") or "").strip() in ("Live", "Final")
    }


def slate_pitchers(target_date: date | None = None) -> list[dict]:
    """Read today's projection CSV and project each row into the
    minimal shape the Bets-tab dropdown needs. Returns [] if no slate
    file exists for the date.

    From first pitch onward, we override the volatile fields (edge,
    p_over, novig_over, line, odds) with the morning-frozen slate
    snapshot so retrospective views match the state we'd actually
    have bet at. Rolling stats (last-5 K%, season K%) re-pull live
    from MLB Stats every refresh, so as soon as the pitcher records
    any IP today, the live CSV's edge silently drifts as the
    in-progress outing folds back into his own history.
    """
    target_date = target_date or date.today()
    path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    if not path.exists():
        return []
    snapshot = _load_slate_snapshot(target_date)
    started_pks = (
        _started_game_pks(target_date.isoformat()) if snapshot else set()
    )
    out: list[dict] = []
    with path.open() as f:
        for r in csv.DictReader(f):
            pid = _safe_int(r.get("pitcher_id"))
            game_pk = _safe_int(r.get("game_pk"))
            pinned = (
                pid is not None
                and game_pk in started_pks
                and pid in snapshot
            )
            src = snapshot[pid] if pinned else r
            edge = _safe_float(src.get("edge"))
            cls, direction = _classify_edge(edge)
            out.append({
                "pitcher_id": pid,
                "pitcher": (r.get("pitcher") or "").strip(),
                "opp": (r.get("opp") or "").strip(),
                "is_home": _parse_is_home(r.get("is_home")),
                "game_pk": game_pk,
                "line": _safe_float(src.get("line")),
                "edge": edge,
                "over_odds": _safe_int(src.get("over_odds")),
                "under_odds": _safe_int(src.get("under_odds")),
                "over_book": (src.get("over_book") or "").strip(),
                "under_book": (src.get("under_book") or "").strip(),
                "p_over": _safe_float(src.get("p_over")),
                "novig_over": _safe_float(src.get("novig_over")),
                "our_pick_class": cls,
                "our_pick_dir": direction,
                "our_pick_label": _our_pick_label(cls, direction),
                "pinned": pinned,
            })
    out.sort(key=lambda x: x["pitcher"].lower())
    return out


def pinned_csv_text(target_date: date) -> str | None:
    """Return the live `pitcher_ks_YYYY-MM-DD.csv` text with already-
    started pitchers' rows replaced by the morning slate snapshot.

    The dashboard JS (web.py) fetches this CSV directly off the file
    server to render its pitcher cards/tables — meaning the same
    silent-edge-drift problem `slate_pitchers()` solves for the Bets
    tab applies to every other view too. This function gives the
    Flask route a pinned-view of the same file without mutating disk.

    Returns None if the live CSV doesn't exist for the date.
    """
    path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    if not path.exists():
        return None
    snapshot = _load_slate_snapshot(target_date)
    if not snapshot:
        # No slate to pin against — caller can fall through to
        # send_from_directory and stream the file as-is.
        return path.read_text()
    started_pks = _started_game_pks(target_date.isoformat())
    if not started_pks:
        return path.read_text()
    with path.open() as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if not fieldnames:
        return path.read_text()
    for row in rows:
        pid = _safe_int(row.get("pitcher_id"))
        game_pk = _safe_int(row.get("game_pk"))
        if pid is None or game_pk not in started_pks or pid not in snapshot:
            continue
        slate_row = snapshot[pid]
        # Overwrite only fields we have in the slate. Leaves any
        # downstream-added columns (e.g., future settle artifacts) alone.
        for k in fieldnames:
            if k in slate_row:
                row[k] = slate_row[k]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _fetch_schedule(target_date_iso: str) -> dict:
    url = (
        f"{MLB_STATS_BASE}/schedule"
        f"?sportId=1&date={target_date_iso}&hydrate=linescore"
    )
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_boxscore(game_pk: int) -> dict:
    url = f"{MLB_STATS_BASE}/game/{game_pk}/boxscore"
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _slate_lookup(target_date: date) -> dict[int, dict]:
    """Map pitcher_id → {game_pk, pitcher, opp, line} from today's CSV.

    Reused by live_ks() to know which game each pitcher is in. Returns
    {} if no slate file exists.
    """
    path = OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}.csv"
    out: dict[int, dict] = {}
    if not path.exists():
        return out
    with path.open() as f:
        for r in csv.DictReader(f):
            pid = _safe_int(r.get("pitcher_id"))
            gpk = _safe_int(r.get("game_pk"))
            if pid is None or gpk is None:
                continue
            out[pid] = {
                "pitcher": (r.get("pitcher") or "").strip(),
                "opp": (r.get("opp") or "").strip(),
                "is_home": _parse_is_home(r.get("is_home")),
                "game_pk": gpk,
                "line": _safe_float(r.get("line")),
            }
    return out


def _game_status_map(target_date_iso: str) -> dict[int, dict]:
    """Hit the schedule once to get game state for all of today's
    games, returning {game_pk: status_info}. Cached 60s."""
    sched = _cached(
        f"sched:{target_date_iso}",
        lambda: _fetch_schedule(target_date_iso),
    )
    out: dict[int, dict] = {}
    for date_obj in sched.get("dates", []):
        for game in date_obj.get("games", []):
            gpk = game.get("gamePk")
            if gpk is None:
                continue
            status = game.get("status", {})
            ls = game.get("linescore", {})
            ls_teams = ls.get("teams", {}) or {}
            game_teams = game.get("teams", {}) or {}
            home_team = (((game_teams.get("home") or {}).get("team")) or {}).get("name")
            away_team = (((game_teams.get("away") or {}).get("team")) or {}).get("name")
            home_runs = (ls_teams.get("home") or {}).get("runs")
            away_runs = (ls_teams.get("away") or {}).get("runs")
            out[int(gpk)] = {
                "abstract": status.get("abstractGameState", "Unknown"),
                "detailed": status.get("detailedState", ""),
                "first_pitch": game.get("gameDate"),
                "current_inning": ls.get("currentInningOrdinal"),
                "inning_state": ls.get("inningState"),
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_runs,
                "away_score": away_runs,
            }
    return out


def _pitcher_state_from_boxscore(box: dict, pitcher_id: int) -> dict:
    """Return live state for a pitcher in a boxscore response.

    Returns a dict with keys: ks (int|None), done (bool), pitches
    (int|None), ip (str|None). `done` is True when the pitcher has been
    replaced — i.e. they appear in their team's `pitchers` array
    (ordered by appearance) but aren't the last entry. Once true their
    K count is locked even before the game goes Final, so the
    over/under can be called immediately.
    """
    blank = {"ks": None, "done": False, "pitches": None, "ip": None}
    key = f"ID{pitcher_id}"
    for side in ("home", "away"):
        team = box.get("teams", {}).get(side, {})
        players = team.get("players", {})
        if key not in players:
            continue
        pitching = players[key].get("stats", {}).get("pitching", {})
        ks: int | None = None
        raw_ks = pitching.get("strikeOuts")
        if raw_ks is not None:
            try:
                ks = int(raw_ks)
            except (TypeError, ValueError):
                ks = None
        pitches: int | None = None
        raw_p = pitching.get("numberOfPitches") or pitching.get("pitchesThrown")
        if raw_p is not None:
            try:
                pitches = int(raw_p)
            except (TypeError, ValueError):
                pitches = None
        ip_raw = pitching.get("inningsPitched")
        ip = str(ip_raw) if ip_raw not in (None, "") else None
        pitchers = team.get("pitchers") or []
        done = bool(pitchers) and pitcher_id in pitchers and pitchers[-1] != pitcher_id
        return {"ks": ks, "done": done, "pitches": pitches, "ip": ip}
    return blank


def live_ks(pitcher_ids: list[int], target_date: date | None = None) -> dict:
    """Look up live K count + game status for each requested pitcher.

    Returns a dict keyed by pitcher_id. Each value:
        {
          "pitcher_id":  int,
          "pitcher":     str,
          "opp":         str,
          "ks":          int | None,
          "line":        float | None,        # from slate, for HIT/MISS check
          "status":      "Preview"|"Live"|"Final"|"NotFound"|"Error",
          "detailed":    str,                  # e.g. "In Progress" / "Final"
          "current_inning": str | None,        # e.g. "5th" when Live
          "inning_state": str | None,          # "Top" / "Bottom" / "End"
          "first_pitch": ISO timestamp str | None,
          "done":        bool,                 # True once pitcher is pulled — Ks locked
          "pitches":     int | None,           # boxscore pitch count
          "ip":          str | None,           # innings pitched, e.g. "5.2"
          "home_team":   str | None,           # full home team name (for tooltip)
          "away_team":   str | None,
          "home_score":  int | None,           # live game score (None pre-game)
          "away_score":  int | None,
          "error":       str | None,
        }
    """
    target_date = target_date or date.today()
    target_iso = target_date.isoformat()
    slate = _slate_lookup(target_date)
    statuses = _game_status_map(target_iso)

    out: dict[int, dict] = {}
    for pid in pitcher_ids:
        pid = int(pid)
        info = slate.get(pid)
        result: dict = {
            "pitcher_id": pid,
            "pitcher": info["pitcher"] if info else None,
            "opp": info["opp"] if info else None,
            "is_home": info["is_home"] if info else None,
            "ks": None,
            "line": info["line"] if info else None,
            "status": "NotFound",
            "detailed": "",
            "current_inning": None,
            "inning_state": None,
            "first_pitch": None,
            "done": False,
            "pitches": None,
            "ip": None,
            "home_team": None,
            "away_team": None,
            "home_score": None,
            "away_score": None,
            "error": None,
        }
        if info is None:
            result["detailed"] = "Not in today's slate"
            out[pid] = result
            continue
        gpk = info["game_pk"]
        gs = statuses.get(gpk)
        if gs is None:
            # Game isn't in today's schedule (postponement, slate file
            # stale, doubleheader resolved differently). Don't fall
            # through to a 404 boxscore call — short-circuit cleanly.
            result["status"] = "NotFound"
            result["detailed"] = "Game not in today's schedule"
            out[pid] = result
            continue
        result["status"] = gs.get("abstract", "Preview")
        result["detailed"] = gs.get("detailed", "")
        result["current_inning"] = gs.get("current_inning")
        result["inning_state"] = gs.get("inning_state")
        result["first_pitch"] = gs.get("first_pitch")
        result["home_team"] = gs.get("home_team")
        result["away_team"] = gs.get("away_team")
        result["home_score"] = gs.get("home_score")
        result["away_score"] = gs.get("away_score")

        # Skip the boxscore call for games that haven't started — Ks
        # would just be None anyway and we save a round-trip.
        if result["status"] == "Preview":
            out[pid] = result
            continue
        try:
            box = _cached(f"box:{gpk}", lambda gpk=gpk: _fetch_boxscore(gpk))
            state = _pitcher_state_from_boxscore(box, pid)
            result["ks"] = state["ks"]
            result["done"] = state["done"]
            result["pitches"] = state["pitches"]
            result["ip"] = state["ip"]
        except Exception as e:  # noqa: BLE001
            result["status"] = "Error"
            result["error"] = str(e)
        out[pid] = result
    return out
