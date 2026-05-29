"""Manually-entered Underdog pick'em pricing, per slate date.

Why this exists: the projection pipeline computes edge against the
*sportsbook* consensus line (DK/FanDuel via The Odds API), but Chad bets
exclusively on Underdog, whose pick'em lines AND per-pick payout
multipliers are set independently. UD's lines are often softer/slower than
the sharp consensus, and when UD shows asymmetric multipliers (e.g. Higher
1.04× / Lower 0.87×) it's pricing the pick — handing us its own implied
probability. Capturing both is what lets the UD Lab tab grade picks against
the market we actually bet (see PROJECT_REPORT blind-spot #1).

This module is the slate-level store the UD Lab tab writes to. It is NOT
per-user: an Underdog board is the same for everyone on the tailnet, so it
lives outside data/users/<id>/ alongside the other slate artifacts.

Storage: data/ud_lines_<date>.json maps pitcher_id (string) -> entry:
    { "<pid>": { "line": <float|null>, "hi": <float|null>, "lo": <float|null> } }
where `hi`/`lo` are UD's Higher/Lower payout multipliers (null when UD
shows a symmetric pick with no multiplier). Legacy entries were a bare
float (line only); load() normalizes those to {line, hi:null, lo:null}.

These are hand-captured inputs, never overwritten by the pipeline, so they
don't pass through the live.pinned_csv_text() slate-pin overlay.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .config import DATA_DIR

_FIELDS = ("line", "hi", "lo")


def _path(target: date) -> Path:
    return DATA_DIR / f"ud_lines_{target.isoformat()}.json"


def _coerce(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(value) -> dict:
    """Normalize a stored value (legacy float OR {line,hi,lo} dict) to a
    full {line, hi, lo} entry."""
    if isinstance(value, dict):
        return {k: _coerce(value.get(k)) for k in _FIELDS}
    # legacy: bare line float
    return {"line": _coerce(value), "hi": None, "lo": None}


def _empty(entry: dict) -> bool:
    return all(entry.get(k) is None for k in _FIELDS)


def load(target: date) -> dict[str, dict]:
    """Return {pid: {line, hi, lo}} for the date, or {} if none saved."""
    path = _path(target)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, dict] = {}
    for k, v in (raw or {}).items():
        entry = _norm(v)
        if not _empty(entry):
            out[str(k)] = entry
    return out


def _write(target: date, board: dict[str, dict]) -> None:
    path = _path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=2, sort_keys=True) + "\n")


def save_one(target: date, pitcher_id, fields: dict) -> dict[str, dict]:
    """Merge a partial {line?, hi?, lo?} into one pitcher's entry (PATCH
    semantics — only keys present in `fields` are applied; pass a key as
    null to clear it). Deletes the pitcher if the entry ends up empty.
    Returns the full updated board."""
    pid = str(pitcher_id).strip()
    if not pid:
        raise ValueError("pitcher_id required")
    board = load(target)
    entry = board.get(pid) or {"line": None, "hi": None, "lo": None}
    for k in _FIELDS:
        if k in fields:
            entry[k] = _coerce(fields[k])
    if _empty(entry):
        board.pop(pid, None)
    else:
        board[pid] = entry
    _write(target, board)
    return board


def save_many(target: date, incoming: dict) -> dict[str, dict]:
    """Upsert many pitchers at once (each value a full {line,hi,lo} entry)
    and return the updated board. Used by the UD Lab "Save all" button so
    the whole board — sportsbook-prefilled lines included — gets recorded
    for the #1 audit. An entry that normalizes to all-null deletes the pid."""
    board = load(target)
    for pid, value in (incoming or {}).items():
        key = str(pid).strip()
        if not key:
            continue
        entry = _norm(value)
        if _empty(entry):
            board.pop(key, None)
        else:
            board[key] = entry
    _write(target, board)
    return board
