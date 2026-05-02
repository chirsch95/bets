"""Sportsbook line fetching via The Odds API.

Sign up at https://the-odds-api.com to get a key (free tier: 500 req/month).
Set ODDS_API_KEY in the environment, or place it in a .env file at the
project root (loaded by config.py).

Lines are aggregated across every US bookmaker the API returns:
    - line          : median across books
    - over_odds     : best (max American) over price + sourcing book
    - under_odds    : best (max American) under price + sourcing book
    - consensus_p_over: median no-vig P(over) across books
    - n_books       : how many books contributed
"""

from __future__ import annotations

import csv
import os
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
HTTP_TIMEOUT = 10


def has_api_key() -> bool:
    return bool(os.environ.get("ODDS_API_KEY"))


def _american_to_decimal(american: int) -> float:
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / abs(american)


def _implied_prob(american: int) -> float:
    return 1 / _american_to_decimal(american)


def _novig_p_over(over_odds: int, under_odds: int) -> float:
    p_over = _implied_prob(over_odds)
    p_under = _implied_prob(under_odds)
    total = p_over + p_under
    if total <= 0:
        return 0.0
    return p_over / total


def _median(values: list[float]) -> float:
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def fetch_pitcher_k_lines(
    target_date: date | None = None,
    sport: str = "baseball_mlb",
    skip_team_pairs: set[frozenset[str]] | None = None,
) -> list[dict]:
    """Return aggregated pitcher_strikeouts O/U lines across all US books.

    Each entry: pitcher_name, line, over_odds, over_book, under_odds,
    under_book, consensus_p_over, n_books, books.

    skip_team_pairs: optional set of frozenset({home_team, away_team})
    pairs whose per-event odds call should be skipped (one credit per
    skipped game). Caller is responsible for deciding the game's
    pitchers are already covered by a previous run's preserved lines.
    """
    return _fetch_player_prop_lines(
        market_key="pitcher_strikeouts",
        name_key="pitcher_name",
        target_date=target_date,
        sport=sport,
        skip_team_pairs=skip_team_pairs,
    )


def fetch_hitter_k_lines(
    target_date: date | None = None,
    sport: str = "baseball_mlb",
) -> list[dict]:
    """Return aggregated batter_strikeouts O/U lines across all US books.

    Each entry: hitter_name, line, over_odds, over_book, under_odds,
    under_book, consensus_p_over, n_books, books.
    """
    return _fetch_player_prop_lines(
        market_key="batter_strikeouts",
        name_key="hitter_name",
        target_date=target_date,
        sport=sport,
    )


def _fetch_player_prop_lines(
    market_key: str,
    name_key: str,
    target_date: date | None,
    sport: str,
    skip_team_pairs: set[frozenset[str]] | None = None,
) -> list[dict]:
    """Generic over/under prop fetcher across every US book on the API."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return []

    target_date = target_date or date.today()

    # Filter events by US-local "today," not UTC. A 7 PM PT first pitch on
    # target_date has commence_time = (target_date+1) 02:00 UTC, which a
    # naive UTC-prefix check would drop. Window: 10:00 UTC of target →
    # 10:00 UTC of target+1 covers any plausible US start (5 AM ET earliest,
    # 1 AM ET next-day cushion for late West Coast first pitches).
    window_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)
    window_end = window_start + timedelta(hours=24)
    window_start_iso = window_start.isoformat().replace("+00:00", "Z")
    window_end_iso = window_end.isoformat().replace("+00:00", "Z")

    events_resp = requests.get(
        f"{THE_ODDS_API_BASE}/sports/{sport}/events",
        params={"apiKey": key, "dateFormat": "iso"},
        timeout=HTTP_TIMEOUT,
    )
    events_resp.raise_for_status()
    events = [
        e
        for e in events_resp.json()
        if window_start_iso <= e.get("commence_time", "") < window_end_iso
    ]

    # Skip games whose two teams were both already priced in a prior
    # run today — saves one credit per such game on Re-runs.
    if skip_team_pairs:
        events = [
            e
            for e in events
            if frozenset({e.get("home_team", ""), e.get("away_team", "")})
            not in skip_team_pairs
        ]

    per_player: dict[str, list[dict]] = defaultdict(list)

    for event in events:
        event_id = event["id"]
        odds_resp = requests.get(
            f"{THE_ODDS_API_BASE}/sports/{sport}/events/{event_id}/odds",
            params={
                "apiKey": key,
                "markets": market_key,
                "regions": "us",
                "oddsFormat": "american",
            },
            timeout=HTTP_TIMEOUT,
        )
        if not odds_resp.ok:
            continue

        for book in odds_resp.json().get("bookmakers", []):
            for entry in _parse_player_outcomes(book, market_key):
                per_player[entry["player_name"]].append(entry)

    return [
        _aggregate_player(name, entries, name_key)
        for name, entries in per_player.items()
    ]


def _parse_player_outcomes(bookmaker: dict, market_key: str) -> list[dict]:
    """Yield one record per player per market, joining over/under outcomes."""
    book_key = bookmaker.get("key", "?")
    by_player: dict[str, dict] = {}
    for market in bookmaker.get("markets", []):
        if market.get("key") != market_key:
            continue
        for outcome in market.get("outcomes", []):
            name = outcome.get("description", "")
            if not name:
                continue
            entry = by_player.setdefault(
                name,
                {"player_name": name, "book": book_key},
            )
            line = outcome.get("point")
            price = outcome.get("price")
            side = outcome.get("name", "").lower()
            if line is not None:
                entry["line"] = line
            if side == "over":
                entry["over_odds"] = price
            elif side == "under":
                entry["under_odds"] = price
    return [
        e
        for e in by_player.values()
        if "line" in e and "over_odds" in e and "under_odds" in e
    ]


def _aggregate_player(name: str, entries: list[dict], name_key: str) -> dict:
    """Median line, best (max American) odds per side, median consensus P(over)."""
    lines = [e["line"] for e in entries]
    median_line = _median(lines)
    best_over = max(entries, key=lambda e: e["over_odds"])
    best_under = max(entries, key=lambda e: e["under_odds"])
    p_overs = [_novig_p_over(e["over_odds"], e["under_odds"]) for e in entries]
    consensus_p_over = _median(p_overs)
    return {
        name_key: name,
        "line": median_line,
        "over_odds": best_over["over_odds"],
        "over_book": best_over["book"],
        "under_odds": best_under["under_odds"],
        "under_book": best_under["book"],
        "consensus_p_over": consensus_p_over,
        "n_books": len(entries),
        "books": sorted({e["book"] for e in entries}),
    }


def _normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.lower().strip()


# Public alias so callers (main.py) can reuse the same accent/case
# rules when matching preserved-line names against starter names.
normalize_name = _normalize_name


def match_line(pitcher_name: str, lines: list[dict]) -> dict | None:
    return _match_by_name(pitcher_name, lines, "pitcher_name")


def match_hitter_line(hitter_name: str, lines: list[dict]) -> dict | None:
    return _match_by_name(hitter_name, lines, "hitter_name")


def _match_by_name(name: str, lines: list[dict], key: str) -> dict | None:
    target = _normalize_name(name)
    for line in lines:
        if _normalize_name(line.get(key, "")) == target:
            return line
    return None


# ---------- Line preservation across same-day reruns ----------
#
# Books pull markets once games start, so a later run will fetch zero
# lines and would otherwise wipe out the morning's capture. These helpers
# load lines from a previously-written projection CSV and merge them with
# the fresh fetch, with fresh winning on conflict (more current) and
# preserved data filling the gaps.


def load_previous_pitcher_lines(csv_path: Path) -> list[dict]:
    """Reload pitcher lines from a previous projection CSV in the shape
    fetch_pitcher_k_lines() returns. Empty list if file is missing."""
    return _load_previous_lines(csv_path, csv_player_col="pitcher", line_name_field="pitcher_name")


def load_previous_hitter_lines(csv_path: Path) -> list[dict]:
    """Reload hitter lines from a previous projection CSV in the shape
    fetch_hitter_k_lines() returns. Empty list if file is missing."""
    return _load_previous_lines(csv_path, csv_player_col="hitter", line_name_field="hitter_name")


def _load_previous_lines(
    csv_path: Path,
    csv_player_col: str,
    line_name_field: str,
) -> list[dict]:
    if not csv_path.exists():
        return []
    out: list[dict] = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            n_books = _safe_int(row.get("n_books"))
            # Old schema (pre-multi-book aggregation) didn't write n_books;
            # treat any row with both prices as 1 book of preserved data.
            if n_books is None:
                if row.get("over_odds") and row.get("under_odds"):
                    n_books = 1
                else:
                    continue
            if n_books <= 0:
                continue

            line_val = _safe_float(row.get("line"))
            over_odds = _safe_int(row.get("over_odds"))
            under_odds = _safe_int(row.get("under_odds"))
            if line_val is None or over_odds is None or under_odds is None:
                continue

            consensus = _safe_float(row.get("novig_over"))
            out.append({
                line_name_field: row.get(csv_player_col, ""),
                "line": line_val,
                "over_odds": over_odds,
                "over_book": row.get("over_book") or "",
                "under_odds": under_odds,
                "under_book": row.get("under_book") or "",
                "consensus_p_over": consensus if consensus is not None else 0.0,
                "n_books": n_books,
                "books": [],
            })
    return out


def merge_lines(fresh: list[dict], preserved: list[dict], name_field: str) -> list[dict]:
    """Merge two line lists: fresh wins on player-name conflict, preserved
    fills gaps for players the fresh fetch didn't return."""
    by_name: dict[str, dict] = {}
    for entry in preserved:
        name = entry.get(name_field)
        if name:
            by_name[_normalize_name(name)] = entry
    for entry in fresh:
        name = entry.get(name_field)
        if name:
            by_name[_normalize_name(name)] = entry
    return list(by_name.values())


def _safe_int(value) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
