"""Per-user bet ledger backed by data/users/<id>/bets.json.

Each user's ledger lives in their own directory (gitignored). All
functions take a `user_id` so the wrong user's bets can never be read or
written by accident — there is no implicit "current user" at this layer.

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
      "site":       "PP" | "UD" | "DK" | "",  # DFS site this ticket was placed on
      "free_entry": false,                 # if true, excluded from staked only
      "result":     "W" | "L" | null,      # null = pending
      "payout":     14.6 | 0 | null,       # null = pending; winnings count
                                            # toward returned even if free
      "stake_reason":      "default"|"focus"|"boost"|"free_entry"|"other",
      "bankroll_at_time":  300.0 | null,   # bankroll when bet was placed
    }

Legacy schema (pre-Phase-1 of structured parlays) had freeform `players`
and `ou` strings. Those are converted on read by `_migrate_legacy()`.
On the next write the bet is persisted in the new schema; until then
the legacy strings are kept for safety (migration is non-destructive).

CRUD is single-process — no locking. Read-modify-write is fine.
"""

from __future__ import annotations

import json
import secrets

from . import auth


def _empty_state() -> dict:
    return {"bets": []}


def load_bets(user_id: str) -> dict:
    path = auth.bets_path(user_id)
    if not path.exists():
        return _empty_state()
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        # Corrupted file — refuse to silently overwrite. Bubble up.
        raise
    # Normalize on read so legacy rows present as new-schema rows.
    # File on disk isn't rewritten until something else triggers a save.
    raw["bets"] = [_normalize(b) for b in raw.get("bets", [])]
    return raw


def save_bets(user_id: str, state: dict) -> None:
    path = auth.bets_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def _new_id() -> str:
    return secrets.token_hex(4)


def _coerce_float(value, default=None):
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_SLATE_CAPTURE_FIELDS = (
    "slate_proj_ks_v2",
    "slate_edge",
    "slate_p_over",
    "slate_novig_over",
    "slate_line",
    "slate_odds",
    "slate_book",
    "slate_ev_per_dollar",
    "slate_pick_class",
    "slate_pinned",
    "slate_captured_at",
)


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
    out = {
        "pitcher_id": pid,
        "pitcher": (leg.get("pitcher") or "").strip(),
        "ou": ou,
        "line": _coerce_float(leg.get("line"), None),
    }
    # Preserve bet-time slate capture if already stamped — these record
    # what the user saw when placing the bet and must never be re-derived
    # on subsequent updates.
    for k in _SLATE_CAPTURE_FIELDS:
        if k in leg and leg[k] not in ("", None):
            out[k] = leg[k]
    return out


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
    site = (bet.get("site") or "").strip().upper()
    if site not in ("PP", "UD", "DK"):
        site = ""
    reason = (bet.get("stake_reason") or "").strip().lower()
    if reason not in ("default", "focus", "boost", "free_entry", "other"):
        reason = "free_entry" if free_entry else "default"
    elif free_entry and reason != "free_entry":
        # A free entry should always tag as such; otherwise the analysis
        # buckets get muddled. Trust the free_entry flag over a stale reason.
        reason = "free_entry"
    return {
        "id": bet.get("id") or _new_id(),
        "date": (bet.get("date") or "").strip(),
        "legs": legs,
        "stake": _coerce_float(bet.get("stake"), 0.0) or 0.0,
        "odds": _coerce_float(bet.get("odds"), 0.0) or 0.0,
        "boost": boost,
        "site": site,
        "free_entry": free_entry,
        "result": bet.get("result") if bet.get("result") in ("W", "L") else None,
        "payout": _coerce_float(bet.get("payout"), None),
        "stake_reason": reason,
        "bankroll_at_time": _coerce_float(bet.get("bankroll_at_time"), None),
    }


def _capture_leg_slate(leg: dict, slate_by_pid: dict) -> None:
    """Stamp the leg with the current slate row for its pitcher.
    Idempotent: a leg already stamped (e.g. on an update round-trip)
    is left alone, so the original placement state is preserved.
    Records the bet-side odds (over vs under) per the leg direction."""
    if leg.get("slate_captured_at"):
        return
    pid = leg.get("pitcher_id")
    if pid is None:
        return
    s = slate_by_pid.get(pid)
    if not s:
        return
    ou = leg.get("ou") or "O"
    odds = s.get("over_odds") if ou == "O" else s.get("under_odds")
    book = s.get("over_book") if ou == "O" else s.get("under_book")
    ev = s.get("ev_over") if ou == "O" else s.get("ev_under")
    leg["slate_proj_ks_v2"] = s.get("proj_ks_v2")
    leg["slate_edge"] = s.get("edge")
    leg["slate_p_over"] = s.get("p_over")
    leg["slate_novig_over"] = s.get("novig_over")
    leg["slate_line"] = s.get("line")
    leg["slate_odds"] = odds
    leg["slate_book"] = book
    leg["slate_ev_per_dollar"] = ev
    leg["slate_pick_class"] = s.get("our_pick_class")
    leg["slate_pinned"] = bool(s.get("pinned"))
    from datetime import datetime, timezone
    leg["slate_captured_at"] = datetime.now(timezone.utc).isoformat()


def add_bet(user_id: str, bet: dict) -> dict:
    state = load_bets(user_id)
    normalized = _normalize(bet)
    # Stamp the bankroll number at placement time if the caller didn't
    # provide one. Records "what was my free cash when I sized this bet"
    # so future analysis can ask "did I size proportionally to bankroll".
    if normalized.get("bankroll_at_time") is None:
        try:
            from . import bankroll as _bk
            normalized["bankroll_at_time"] = round(_bk.current_balance(user_id), 2)
        except Exception as e:  # noqa: BLE001
            print(f"bankroll stamp failed: {e}")
    # Stamp each leg with the current slate row so the bet object records
    # what the user saw at placement (proj, edge, p_over, line, odds,
    # book, EV). Failures here are non-fatal — the bet still saves.
    try:
        from datetime import date as _date
        from . import live as _live
        target_iso = normalized.get("date") or _date.today().isoformat()
        target = _date.fromisoformat(target_iso)
        slate = _live.slate_pitchers(target)
        by_pid = {p["pitcher_id"]: p for p in slate if p.get("pitcher_id")}
        for leg in normalized["legs"]:
            _capture_leg_slate(leg, by_pid)
    except Exception as e:  # noqa: BLE001
        print(f"bet-time slate capture failed: {e}")
    state["bets"].append(normalized)
    save_bets(user_id, state)
    return normalized


def update_bet(user_id: str, bet_id: str, updates: dict) -> dict | None:
    state = load_bets(user_id)
    for i, b in enumerate(state["bets"]):
        if b.get("id") == bet_id:
            merged = {**b, **updates, "id": bet_id}
            normalized = _normalize(merged)
            state["bets"][i] = normalized
            save_bets(user_id, state)
            return normalized
    return None


def delete_bet(user_id: str, bet_id: str) -> bool:
    state = load_bets(user_id)
    before = len(state["bets"])
    state["bets"] = [b for b in state["bets"] if b.get("id") != bet_id]
    if len(state["bets"]) == before:
        return False
    save_bets(user_id, state)
    return True


def totals(user_id: str, state: dict | None = None) -> dict:
    """Aggregate stats across all bets — drives the totals strip.

    Free-entry bets are excluded from staked / settled_staked (no money
    put up), but their winnings DO count toward returned, net, and ROI
    — a winning free entry is pure upside and shows up in P/L.
    Counts of wins / losses / pending include free entries. Bonus
    winnings from free entries are also reported separately for visibility.
    """
    state = state or load_bets(user_id)
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

    # Per-site breakdown — only the three known DFS sites; bets with no
    # site tag are excluded so the row is comparing apples to apples.
    # Same net/ROI rules as the global totals: free winnings flow into
    # net, but free stakes never count toward the denominator.
    by_site: dict[str, dict] = {}
    for code in ("PP", "UD", "DK"):
        site_bets = [b for b in bets if b.get("site") == code]
        if not site_bets:
            continue
        s_paid = [b for b in site_bets if not b.get("free_entry")]
        s_free = [b for b in site_bets if b.get("free_entry")]
        s_settled_staked = sum(
            b.get("stake") or 0.0 for b in s_paid if b.get("result") in ("W", "L")
        )
        s_paid_returned = sum(
            b.get("payout") or 0.0 for b in s_paid if b.get("result") == "W"
        )
        s_free_winnings = sum(
            b.get("payout") or 0.0 for b in s_free if b.get("result") == "W"
        )
        s_net = s_paid_returned + s_free_winnings - s_settled_staked
        s_roi = (s_net / s_settled_staked) if s_settled_staked else None
        by_site[code] = {
            "count": len(site_bets),
            "wins": sum(1 for b in site_bets if b.get("result") == "W"),
            "losses": sum(1 for b in site_bets if b.get("result") == "L"),
            "pending": sum(1 for b in site_bets if b.get("result") is None),
            "staked": round(s_settled_staked, 2),
            "net": round(s_net, 2),
            "roi": round(s_roi, 4) if s_roi is not None else None,
        }

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
        "by_site": by_site,
    }
