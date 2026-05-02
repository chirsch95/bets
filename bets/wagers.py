"""Personal bet ledger backed by data/bets.json.

Local-only feature — the JSON file lives in data/ which is gitignored,
so it never reaches GitHub or Netlify. The Flask server exposes CRUD
endpoints; the dashboard renders a local-only "Bets" tab against them.

Schema for one bet (one row in the spreadsheet ≈ one parlay ticket):
    {
      "id":         "<short id>",
      "date":       "YYYY-MM-DD",
      "legs": [
        { "pitcher_id": int|null, "pitcher": str, "ou": "O"|"U",
          "line": float|null }
      ],
      "stake":      10.0,                  # dollars (notional, may be free)
      "odds":       2.29,                  # decimal odds
      "boost":      "Free entry",          # freeform note
      "free_entry": false,                 # if true, excluded from staked only
      "result":     "W" | "L" | null,      # null = pending
      "payout":     14.6 | 0 | null,       # null = pending; winnings count
                                            # toward returned even if free
    }

Legacy schema (pre-Phase-1 of structured parlays) had freeform `players`
and `ou` strings. Those are converted on read by `_migrate_legacy()`.
On the next write the bet is persisted in the new schema; until then
the legacy strings are kept for safety (migration is non-destructive).

CRUD is single-user / single-process — no locking. Read-modify-write is
fine for a personal local tracker.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from .config import PROJECT_ROOT

BETS_PATH = PROJECT_ROOT / "data" / "bets.json"


def _empty_state() -> dict:
    return {"bets": []}


def load_bets() -> dict:
    if not BETS_PATH.exists():
        return _empty_state()
    try:
        raw = json.loads(BETS_PATH.read_text())
    except json.JSONDecodeError:
        # Corrupted file — refuse to silently overwrite. Bubble up.
        raise
    # Normalize on read so legacy rows present as new-schema rows.
    # File on disk isn't rewritten until something else triggers a save.
    raw["bets"] = [_normalize(b) for b in raw.get("bets", [])]
    return raw


def save_bets(state: dict) -> None:
    BETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BETS_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _new_id() -> str:
    return secrets.token_hex(4)


def _coerce_float(value, default=None):
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_leg(leg: dict) -> dict:
    """Coerce one leg dict into the canonical shape."""
    if not isinstance(leg, dict):
        return {"pitcher_id": None, "pitcher": str(leg or "").strip(), "ou": "O", "line": None}
    pid = leg.get("pitcher_id")
    try:
        pid = int(pid) if pid not in ("", None) else None
    except (TypeError, ValueError):
        pid = None
    ou = (leg.get("ou") or "").strip().upper()
    if ou not in ("O", "U"):
        ou = "O"
    return {
        "pitcher_id": pid,
        "pitcher": (leg.get("pitcher") or "").strip(),
        "ou": ou,
        "line": _coerce_float(leg.get("line"), None),
    }


def _migrate_legacy(bet: dict) -> dict:
    """Rebuild a leg list from legacy `players`/`ou` strings if needed.

    Legacy rows have `players: "Gore/Perez"` and `ou: "O/U"`. New rows
    have `legs: [...]`. Migration is read-side and idempotent: we don't
    touch the file until something else triggers a write.
    """
    if isinstance(bet.get("legs"), list) and bet["legs"]:
        return bet
    players = (bet.get("players") or "").strip()
    ou_str = (bet.get("ou") or "").strip()
    if not players:
        return {**bet, "legs": []}
    parts = [p.strip() for p in players.split("/") if p.strip()]
    ou_chars = [c.strip().upper() for c in ou_str.split("/") if c.strip()]
    legs = []
    for i, name in enumerate(parts):
        ou = ou_chars[i] if i < len(ou_chars) else "O"
        if ou not in ("O", "U"):
            ou = "O"
        legs.append({"pitcher_id": None, "pitcher": name, "ou": ou, "line": None})
    return {**bet, "legs": legs}


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(v, (int, float)):
        return bool(v)
    return False


def _normalize(bet: dict) -> dict:
    """Apply field normalization: trim strings, coerce numerics, default
    missing keys. Migrates legacy schema in passing. Keeps the JSON
    shape stable regardless of how the caller (form post, importer,
    legacy file) hands data over."""
    bet = _migrate_legacy(bet)
    legs_raw = bet.get("legs") or []
    # Keep a leg if it identifies a pitcher in any way: name OR id (the
    # JS form may post a leg with pitcher_id set but the display name
    # still pending a slate-lookup round-trip). For non-dict legacy
    # entries (a stray string), keep any truthy value.
    def _has_identity(l):
        if isinstance(l, dict):
            return bool(l.get("pitcher")) or l.get("pitcher_id") not in ("", None)
        return bool(l)
    legs = [_normalize_leg(l) for l in legs_raw if _has_identity(l)]
    boost = (bet.get("boost") or "").strip()
    # Auto-detect free_entry from legacy boost text on first migration.
    # Once an explicit free_entry is set (true or false), trust it.
    if "free_entry" in bet:
        free_entry = _coerce_bool(bet.get("free_entry"))
    else:
        free_entry = "free entry" in boost.lower()
    return {
        "id": bet.get("id") or _new_id(),
        "date": (bet.get("date") or "").strip(),
        "legs": legs,
        "stake": _coerce_float(bet.get("stake"), 0.0) or 0.0,
        "odds": _coerce_float(bet.get("odds"), 0.0) or 0.0,
        "boost": boost,
        "free_entry": free_entry,
        "result": bet.get("result") if bet.get("result") in ("W", "L") else None,
        "payout": _coerce_float(bet.get("payout"), None),
    }


def add_bet(bet: dict) -> dict:
    state = load_bets()
    normalized = _normalize(bet)
    state["bets"].append(normalized)
    save_bets(state)
    return normalized


def update_bet(bet_id: str, updates: dict) -> dict | None:
    state = load_bets()
    for i, b in enumerate(state["bets"]):
        if b.get("id") == bet_id:
            merged = {**b, **updates, "id": bet_id}
            normalized = _normalize(merged)
            state["bets"][i] = normalized
            save_bets(state)
            return normalized
    return None


def delete_bet(bet_id: str) -> bool:
    state = load_bets()
    before = len(state["bets"])
    state["bets"] = [b for b in state["bets"] if b.get("id") != bet_id]
    if len(state["bets"]) == before:
        return False
    save_bets(state)
    return True


def totals(state: dict | None = None) -> dict:
    """Aggregate stats across all bets — drives the totals strip.

    Free-entry bets are excluded from staked / settled_staked (no money
    put up), but their winnings DO count toward returned, net, and ROI
    — a winning free entry is pure upside and shows up in P/L.
    Counts of wins / losses / pending include free entries. Bonus
    winnings from free entries are also reported separately for visibility.
    """
    state = state or load_bets()
    bets = state["bets"]
    paid = [b for b in bets if not b.get("free_entry")]
    free = [b for b in bets if b.get("free_entry")]

    staked = sum(b.get("stake") or 0.0 for b in paid)
    returned = sum(b.get("payout") or 0.0 for b in bets if b.get("result") == "W")
    wins = sum(1 for b in bets if b.get("result") == "W")
    losses = sum(1 for b in bets if b.get("result") == "L")
    pending = sum(1 for b in bets if b.get("result") is None)
    settled = wins + losses
    settled_staked = sum(
        b.get("stake") or 0.0 for b in paid if b.get("result") in ("W", "L")
    )
    paid_returned = sum(
        b.get("payout") or 0.0 for b in paid if b.get("result") == "W"
    )
    free_winnings = sum(
        b.get("payout") or 0.0 for b in free if b.get("result") == "W"
    )
    # Free wins are pure upside — payout flows into net, no stake to subtract.
    # Free losses contribute 0 (no money risked). Denominator (staked) stays
    # paid-only so ROI still reflects return on actual capital deployed.
    net = paid_returned + free_winnings - settled_staked
    roi = (net / settled_staked) if settled_staked else None

    free_wins = sum(1 for b in free if b.get("result") == "W")
    free_losses = sum(1 for b in free if b.get("result") == "L")
    free_pending = sum(1 for b in free if b.get("result") is None)

    return {
        "count": len(bets),
        "paid_count": len(paid),
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "net": round(net, 2),
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "settled": settled,
        "roi": round(roi, 4) if roi is not None else None,
        "free_count": len(free),
        "free_wins": free_wins,
        "free_losses": free_losses,
        "free_pending": free_pending,
        "free_winnings": round(free_winnings, 2),
    }
