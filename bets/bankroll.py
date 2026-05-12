"""Personal bankroll ledger backed by data/bankroll.json.

Local-only feature, gitignored, single-user. Tracks a notional bankroll
that backs the bets ledger:

    {
      "starting":   300.0,            # initial bankroll, immutable
      "started_at": "2026-05-13",     # date the experiment began
      "pause_at":   150.0,            # 50% drawdown — review trigger
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
from pathlib import Path

from .config import PROJECT_ROOT

BANKROLL_PATH = PROJECT_ROOT / "data" / "bankroll.json"

DEFAULTS = {
    "starting": 300.0,
    "started_at": "2026-04-30",
    "pause_at": 150.0,
    "end_at": 0.0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> dict:
    return {
        **DEFAULTS,
        "events": [
            {
                "ts": _now_iso(),
                "type": "init",
                "delta": 0.0,
                "bet_id": None,
                "note": f"initial bankroll ${DEFAULTS['starting']:.2f}",
            }
        ],
    }


def load() -> dict:
    if not BANKROLL_PATH.exists():
        state = _empty_state()
        save(state)
        return state
    raw = json.loads(BANKROLL_PATH.read_text())
    # Backfill defaults if older file is missing keys.
    for k, v in DEFAULTS.items():
        raw.setdefault(k, v)
    raw.setdefault("events", [])
    return raw


def save(state: dict) -> None:
    BANKROLL_PATH.parent.mkdir(parents=True, exist_ok=True)
    BANKROLL_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _sum_delta(events: list[dict]) -> float:
    return sum(float(e.get("delta") or 0.0) for e in events)


def current_balance(state: dict | None = None) -> float:
    state = state or load()
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


def record_settlement(bet: dict, *, save_state: bool = True) -> dict:
    """Idempotent settle event for one bet. Replaces any prior settle
    event with the same bet_id so W↔L flips stay consistent."""
    state = load()
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
        save(state)
    return state


def record_event(
    type_: str, delta: float, note: str = "", bet_id: str | None = None
) -> dict:
    state = load()
    state["events"].append(
        {
            "ts": _now_iso(),
            "type": type_,
            "delta": round(float(delta), 2),
            "bet_id": bet_id,
            "note": note,
        }
    )
    save(state)
    return state


def snapshot(pending_stake: float = 0.0) -> dict:
    """Read-only view used by the dashboard / API."""
    state = load()
    current = current_balance(state)
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


def backfill_from_bets(bets: list[dict]) -> dict:
    """Replay every settled bet in date order, recording a settle event
    for any bet that doesn't already have one. Idempotent — safe to run
    repeatedly. Returns the final state."""
    state = load()
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
    save(state)
    return state


def main() -> None:
    """CLI: `python -m bets.bankroll [show|backfill|reset]`"""
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "show":
        from . import wagers
        bets = wagers.load_bets()["bets"]
        pending = sum(
            float(b.get("stake") or 0)
            for b in bets
            if not b.get("result") and not b.get("free_entry")
        )
        snap = snapshot(pending)
        print(json.dumps(snap, indent=2))
    elif cmd == "backfill":
        from . import wagers
        before = current_balance()
        state = backfill_from_bets(wagers.load_bets()["bets"])
        after = current_balance(state)
        print(f"bankroll: ${before:.2f} → ${after:.2f}")
        print(f"events: {len(state['events'])}")
    elif cmd == "reset":
        if BANKROLL_PATH.exists():
            BANKROLL_PATH.unlink()
            print(f"removed {BANKROLL_PATH}")
        else:
            print(f"no file at {BANKROLL_PATH}")
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    main()
