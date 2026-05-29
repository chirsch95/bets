"""Manually-entered Underdog pick'em lines, per slate date.

Why this exists: the projection pipeline computes edge against the
*sportsbook* consensus line (DK/FanDuel via The Odds API), but Chad bets
exclusively on Underdog, whose pick'em lines are set independently and
are often softer/slower than the sharp consensus. Until 2026-05 nothing
in the system captured UD's actual line, so every "edge" number described
a market we don't bet (see PROJECT_REPORT blind-spot #1).

This module is the slate-level store the UD Lab tab writes to. It is NOT
per-user: an Underdog line is the same number for everyone on the tailnet,
so it lives outside data/users/<id>/ alongside the other slate artifacts.

Storage: data/ud_lines_<date>.json == { "<pitcher_id>": <line float>, ... }
A pitcher with no entry simply isn't a key. Setting a line to null/None
deletes the key (clears the entry).

These are inputs captured by hand, never overwritten by the pipeline, so
they don't pass through the live.pinned_csv_text() slate-pin overlay —
that overlay only touches the generated pitcher_ks CSV.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .config import DATA_DIR


def _path(target: date) -> Path:
    return DATA_DIR / f"ud_lines_{target.isoformat()}.json"


def load(target: date) -> dict[str, float]:
    """Return {pitcher_id_str: line} for the date, or {} if none saved."""
    path = _path(target)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, float] = {}
    for k, v in (raw or {}).items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def save_one(target: date, pitcher_id, line) -> dict[str, float]:
    """Upsert a single pitcher's UD line for the date and return the full
    updated map. A None/empty line deletes the entry. pitcher_id is keyed
    as a string so it round-trips cleanly through JSON."""
    pid = str(pitcher_id).strip()
    if not pid:
        raise ValueError("pitcher_id required")
    lines = load(target)
    if line is None or line == "":
        lines.pop(pid, None)
    else:
        lines[pid] = float(line)
    path = _path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lines, indent=2, sort_keys=True) + "\n")
    return lines


def save_many(target: date, incoming: dict) -> dict[str, float]:
    """Upsert many pitcher lines at once and return the full updated map.
    Used by the UD Lab "Save all" button to persist the whole board —
    including rows still sitting at the sportsbook prefill — so the #1
    audit has a genuine UD line on record for every pitcher, not just the
    ones that differed. A null/empty value deletes that pitcher's entry."""
    lines = load(target)
    for pid, line in (incoming or {}).items():
        key = str(pid).strip()
        if not key:
            continue
        if line is None or line == "":
            lines.pop(key, None)
            continue
        try:
            lines[key] = float(line)
        except (TypeError, ValueError):
            continue
    path = _path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lines, indent=2, sort_keys=True) + "\n")
    return lines
