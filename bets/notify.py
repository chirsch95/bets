"""Pushover notifications for bet settlement and live-game events.

Reads PUSHOVER_TOKEN + PUSHOVER_USER from env (loaded by server.py via
.env). If either is missing, send_pushover() is a silent no-op — the
dashboard keeps working without notifications configured.

Sends fire on a daemon thread so the HTTP handler doesn't block on
Pushover's API.

Live-game alerts (pulled-starter, parlay one-to-go, per-leg hit/miss)
piggyback on the dashboard's /api/live-ks polls. Dedup state lives in
data/notify_state.json so the same event fires at most once per day.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import date, timedelta

import requests

from .config import PROJECT_ROOT

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
NOTIFY_STATE_PATH = PROJECT_ROOT / "data" / "notify_state.json"

logger = logging.getLogger(__name__)


def send_pushover(title: str, message: str) -> None:
    token = os.getenv("PUSHOVER_TOKEN")
    user = os.getenv("PUSHOVER_USER")
    if not token or not user:
        return

    def _send():
        try:
            requests.post(
                PUSHOVER_URL,
                data={
                    "token": token,
                    "user": user,
                    "title": title,
                    "message": message,
                },
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("pushover notify failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


def format_bet_settle(bet: dict) -> tuple[str, str] | None:
    """Build (title, message) for a newly-settled bet, or None to skip."""
    result = bet.get("result")
    if result not in ("W", "L"):
        return None

    legs = bet.get("legs") or []
    leg_lines = []
    for l in legs:
        name = (l.get("pitcher") or "").strip() or "?"
        ou = l.get("ou") or ""
        line = l.get("line")
        line_str = f"{line:g}" if isinstance(line, (int, float)) else "?"
        leg_lines.append(f"{name} {ou}{line_str}")
    body = " + ".join(leg_lines) if leg_lines else "(no legs)"

    stake = bet.get("stake") or 0.0
    payout = bet.get("payout") or 0.0
    free = bool(bet.get("free_entry"))

    if result == "W":
        if free:
            title = f"Bet won (free): +${payout:.2f}"
        else:
            net = payout - stake
            title = f"Bet won: +${net:.2f}"
    else:
        title = "Bet lost (free entry)" if free else f"Bet lost: -${stake:.2f}"

    return title, body


# ---------- live-game alerts (pulled starter, one-to-go) ----------


def _load_notify_state() -> dict:
    if not NOTIFY_STATE_PATH.exists():
        return {"seen": {}}
    try:
        return json.loads(NOTIFY_STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {"seen": {}}


def _save_notify_state(state: dict) -> None:
    NOTIFY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _claim_key(key: str, today_iso: str) -> bool:
    """Atomic check-and-set on the dedup store. Returns True if the key
    was newly claimed (caller should fire), False if already seen.
    Prunes entries older than 7 days in passing.
    """
    state = _load_notify_state()
    seen = state.get("seen", {})
    try:
        cutoff = (date.fromisoformat(today_iso) - timedelta(days=7)).isoformat()
    except ValueError:
        cutoff = ""
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    if key in pruned:
        if pruned != seen:
            state["seen"] = pruned
            _save_notify_state(state)
        return False
    pruned[key] = today_iso
    state["seen"] = pruned
    _save_notify_state(state)
    return True


def _leg_state(ks, line, ou, status, done):
    """Python mirror of legHitState() in web.py — keep in sync."""
    if ks is None or line is None:
        return None
    if ks > line:
        return "hit" if ou == "O" else "miss"
    if status == "Final" or done:
        return "hit" if ou == "U" else "miss"
    return None


def _live_for(live_results: dict, pid: int) -> dict | None:
    """live_results is normally int-keyed (live.live_ks output) but
    survives a json round-trip as str-keyed — accept both."""
    return live_results.get(pid) or live_results.get(str(pid))


def check_live_alerts(live_results: dict, target_iso: str) -> None:
    """Scan today's pending bets against a live snapshot and fire
    Pushover alerts for: (1) any starter who just got pulled, (2)
    parlays with all-but-one legs hit, (3) per-leg hit/miss in
    multi-leg parlays. Deduped via notify_state.json.

    Bet-level filtering: only today's pending bets are considered.
    Settled bets and bets dated to other days are skipped. Wrapped in
    broad try/except by the caller so a notification bug never breaks
    the live-ks request.
    """
    # Local import: avoids a circular dep at module-import time.
    from . import wagers

    bets = wagers.load_bets()["bets"]
    todays_pending = [
        b for b in bets
        if b.get("result") not in ("W", "L") and (b.get("date") or "") == target_iso
    ]

    # ----- pulled-starter: any pending leg whose pitcher is now done
    #       AND the game hasn't reached Final (so we know it was a pull,
    #       not a complete game).
    pending_pids: set[int] = set()
    for b in todays_pending:
        for l in b.get("legs") or []:
            pid = l.get("pitcher_id")
            if pid is not None:
                pending_pids.add(int(pid))

    for pid in pending_pids:
        live = _live_for(live_results, pid)
        if not live or not live.get("done") or live.get("status") == "Final":
            continue
        if not _claim_key(f"{target_iso}:pulled:{pid}", target_iso):
            continue
        name = live.get("pitcher") or f"Pitcher {pid}"
        ks = live.get("ks")
        line = live.get("line")
        if ks is not None and line is not None:
            msg = f"Locked at {ks}K (line {line:g})"
        else:
            msg = "Pitcher pulled mid-game"
        send_pushover(f"Pulled: {name}", msg)

    # ----- one-to-go: multi-leg parlay with N-1 hit + 1 pending + 0 miss
    for b in todays_pending:
        legs = b.get("legs") or []
        if len(legs) < 2:
            continue
        states = []
        for l in legs:
            pid = l.get("pitcher_id")
            live = _live_for(live_results, pid) if pid is not None else None
            if not live:
                states.append((None, l, None))
                continue
            line = l.get("line") if l.get("line") is not None else live.get("line")
            s = _leg_state(live.get("ks"), line, l.get("ou"), live.get("status"), live.get("done"))
            states.append((s, l, live))

        miss = sum(1 for s, _, _ in states if s == "miss")
        hit = sum(1 for s, _, _ in states if s == "hit")
        pending = [(l, live) for s, l, live in states if s is None]
        if miss > 0 or hit != len(legs) - 1 or len(pending) != 1:
            continue

        leg, live = pending[0]
        # Only fire once the game is actually live — no point pushing
        # at slate-lock when nothing has happened yet.
        if not live or live.get("status") != "Live":
            continue
        if not _claim_key(f"{target_iso}:onetogo:{b.get('id')}", target_iso):
            continue

        name = leg.get("pitcher") or live.get("pitcher") or "?"
        line = leg.get("line") if leg.get("line") is not None else live.get("line")
        ks = live.get("ks") or 0
        ou = leg.get("ou") or "?"
        inning = live.get("current_inning") or "?"
        line_str = f"{line:g}" if line is not None else "?"

        if ou == "O" and line is not None:
            # Need ks > line. For .5 lines, ceil(line) is the target.
            needed = max(0, int(math.floor(line)) + 1 - ks)
            title = f"One leg to go: {name} needs {needed}"
            msg = f"{name} O{line_str} · {ks}K through {inning}"
        else:
            title = f"One leg to go: {name}"
            msg = f"{name} {ou}{line_str} · {ks}K through {inning}"
        send_pushover(title, msg)

    # ----- per-leg hit/miss: fire as soon as a leg's outcome is decided
    #       (line crossed, or game went final). Skips singletons since
    #       bet-settle already covers those.
    for b in todays_pending:
        legs = b.get("legs") or []
        if len(legs) < 2:
            continue
        bet_id = b.get("id")
        for l in legs:
            pid = l.get("pitcher_id")
            if pid is None:
                continue
            live = _live_for(live_results, pid)
            if not live:
                continue
            line = l.get("line") if l.get("line") is not None else live.get("line")
            s = _leg_state(live.get("ks"), line, l.get("ou"), live.get("status"), live.get("done"))
            if s not in ("hit", "miss"):
                continue
            if not _claim_key(f"{target_iso}:leg:{bet_id}:{pid}:{s}", target_iso):
                continue
            name = l.get("pitcher") or live.get("pitcher") or f"Pitcher {pid}"
            ou = l.get("ou") or "?"
            ks = live.get("ks") or 0
            line_str = f"{line:g}" if line is not None else "?"
            verb = "hit" if s == "hit" else "miss"
            send_pushover(f"Leg {verb}: {name}", f"{name} {ou}{line_str} · {ks}K")
