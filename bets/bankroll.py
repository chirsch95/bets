"""Per-user bankroll ledger backed by data/users/<id>/bankroll.json.

Each user's ledger lives in their own directory (gitignored). All
functions take a `user_id`. The starting/pause/end values come from the
user's first-login wizard; the file is created with `init(user_id, ...)`
when the wizard finishes — it is NOT auto-created on first read, because
without wizard input there are no defaults that fit every user.

    {
      "starting":   300.0,            # initial bankroll, immutable
      "started_at": "2026-05-13",     # date the experiment began
      "pause_at":   150.0,            # drawdown — review trigger
      "end_at":     0.0,              # bust — experiment over
      "events": [
        {
          "ts":     "2026-05-13T12:34:56+00:00",
          "type":   "init"|"settle"|"deposit"|"withdrawal"|"adjustment",
          "delta":  -5.0,
          "bet_id": "abc12345" | null,
          "note":   ""
        },
        ...
      ]
    }

Current bankroll = starting + sum(event.delta). Pending bets are NOT
deducted — current reflects settled cash only. Pending exposure is
exposed separately as "pending_stake" so the UI can show both.

Settlement events are keyed by bet_id and idempotent: re-recording the
same bet replaces the prior settle event so W↔L flips and unsettles
remain consistent.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from . import auth


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(
    user_id: str,
    starting: float,
    pause_at: float,
    end_at: float,
    started_at: str | None = None,
) -> dict:
    """Create the bankroll file for a new user. Caller (the wizard)
    supplies starting/pause/end. Refuses to overwrite an existing file."""
    path = auth.bankroll_path(user_id)
    if path.exists():
        raise FileExistsError(f"bankroll already exists for {user_id!r}")
    state = {
        "starting": float(starting),
        "started_at": started_at or date.today().isoformat(),
        "pause_at": float(pause_at),
        "end_at": float(end_at),
        "events": [
            {
                "ts": _now_iso(),
                "type": "init",
                "delta": 0.0,
                "bet_id": None,
                "note": f"initial bankroll ${float(starting):.2f}",
            }
        ],
    }
    save(user_id, state)
    return state


def load(user_id: str) -> dict:
    path = auth.bankroll_path(user_id)
    if not path.exists():
        raise FileNotFoundError(
            f"bankroll not initialized for user {user_id!r} — "
            f"run setup wizard or call bankroll.init()"
        )
    raw = json.loads(path.read_text())
    raw.setdefault("events", [])
    return raw


def save(user_id: str, state: dict) -> None:
    path = auth.bankroll_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def _sum_delta(events: list[dict]) -> float:
    return sum(float(e.get("delta") or 0.0) for e in events)


def current_balance(user_id: str, state: dict | None = None) -> float:
    state = state or load(user_id)
    return float(state["starting"]) + _sum_delta(state["events"])


def status_for(balance: float, state: dict) -> str:
    """active | pause_review | ended"""
    if balance <= float(state["end_at"]):
        return "ended"
    if balance <= float(state["pause_at"]):
        return "pause_review"
    return "active"


def _settle_delta(bet: dict) -> float | None:
    """Net cash impact of a settled bet on the bankroll.

    - Paid W: +(payout - stake)
    - Paid L: -stake
    - Free W: +payout (no stake to subtract)
    - Free L: 0 (no money risked)
    - Pending or unknown: None (no event)
    """
    result = bet.get("result")
    if result not in ("W", "L"):
        return None
    stake = float(bet.get("stake") or 0.0)
    payout = float(bet.get("payout") or 0.0)
    free = bool(bet.get("free_entry"))
    if result == "W":
        return payout if free else (payout - stake)
    # result == "L"
    return 0.0 if free else -stake


def record_settlement(user_id: str, bet: dict, *, save_state: bool = True) -> dict:
    """Idempotent settle event for one bet. Replaces any prior settle
    event with the same bet_id so W↔L flips stay consistent."""
    state = load(user_id)
    bet_id = bet.get("id")
    state["events"] = [
        e for e in state["events"]
        if not (e.get("type") == "settle" and e.get("bet_id") == bet_id)
    ]
    delta = _settle_delta(bet)
    if delta is not None:
        state["events"].append(
            {
                "ts": _now_iso(),
                "type": "settle",
                "delta": round(float(delta), 2),
                "bet_id": bet_id,
                "note": f"{bet.get('result')} {'free ' if bet.get('free_entry') else ''}"
                f"stake=${float(bet.get('stake') or 0):.2f} payout=${float(bet.get('payout') or 0):.2f}",
            }
        )
    if save_state:
        save(user_id, state)
    return state


def record_event(
    user_id: str, type_: str, delta: float, note: str = "", bet_id: str | None = None
) -> dict:
    state = load(user_id)
    state["events"].append(
        {
            "ts": _now_iso(),
            "type": type_,
            "delta": round(float(delta), 2),
            "bet_id": bet_id,
            "note": note,
        }
    )
    save(user_id, state)
    return state


def snapshot(user_id: str, pending_stake: float = 0.0) -> dict:
    """Read-only view used by the dashboard / API."""
    state = load(user_id)
    current = current_balance(user_id, state)
    # Track bankroll-curve extremes by replaying events in order.
    running = float(state["starting"])
    low = high = running
    for e in state["events"]:
        running += float(e.get("delta") or 0.0)
        low = min(low, running)
        high = max(high, running)
    return {
        "starting": float(state["starting"]),
        "started_at": state["started_at"],
        "current": round(current, 2),
        "low_water": round(low, 2),
        "high_water": round(high, 2),
        "pause_at": float(state["pause_at"]),
        "end_at": float(state["end_at"]),
        "status": status_for(current, state),
        "events_count": len(state["events"]),
        "pending_stake": round(float(pending_stake), 2),
        "pct_of_starting": round(current / float(state["starting"]) * 100, 1)
        if state["starting"]
        else None,
    }


def backfill_from_bets(user_id: str, bets: list[dict]) -> dict:
    """Replay every settled bet in date order, recording a settle event
    for any bet that doesn't already have one. Idempotent — safe to run
    repeatedly. Returns the final state."""
    state = load(user_id)
    have = {
        e.get("bet_id")
        for e in state["events"]
        if e.get("type") == "settle" and e.get("bet_id")
    }

    def _sort_key(b):
        return (b.get("date") or "", b.get("id") or "")

    settled = [
        b for b in sorted(bets, key=_sort_key)
        if b.get("result") in ("W", "L") and b.get("id") not in have
    ]
    for b in settled:
        delta = _settle_delta(b)
        if delta is None:
            continue
        state["events"].append(
            {
                "ts": _now_iso(),
                "type": "settle",
                "delta": round(float(delta), 2),
                "bet_id": b.get("id"),
                "note": (
                    f"backfill {b.get('date')} {b.get('result')} "
                    f"{'free ' if b.get('free_entry') else ''}"
                    f"stake=${float(b.get('stake') or 0):.2f} "
                    f"payout=${float(b.get('payout') or 0):.2f}"
                ),
            }
        )
    save(user_id, state)
    return state


def main() -> None:
    """CLI: `python -m bets.bankroll <user> [show|backfill|reset]`"""
    import sys

    if len(sys.argv) < 2:
        print(
            "usage: python -m bets.bankroll <user_id> [show|backfill|reset]",
            file=sys.stderr,
        )
        sys.exit(2)
    user_id = sys.argv[1]
    cmd = sys.argv[2] if len(sys.argv) > 2 else "show"
    if cmd == "show":
        from . import wagers
        bets = wagers.load_bets(user_id)["bets"]
        pending = sum(
            float(b.get("stake") or 0)
            for b in bets
            if not b.get("result") and not b.get("free_entry")
        )
        snap = snapshot(user_id, pending)
        print(json.dumps(snap, indent=2))
    elif cmd == "backfill":
        from . import wagers
        before = current_balance(user_id)
        state = backfill_from_bets(user_id, wagers.load_bets(user_id)["bets"])
        after = current_balance(user_id, state)
        print(f"bankroll: ${before:.2f} → ${after:.2f}")
        print(f"events: {len(state['events'])}")
    elif cmd == "reset":
        path = auth.bankroll_path(user_id)
        if path.exists():
            path.unlink()
            print(f"removed {path}")
        else:
            print(f"no file at {path}")
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    main()
