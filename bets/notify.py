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

import csv
import json
import logging
import math
import os
import threading
from datetime import date, timedelta

import requests

from .config import OUTPUT_DIR, PROJECT_ROOT

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


# ---------- pre-game scratch alerts ----------


def check_scratch_alerts(target_iso: str) -> None:
    """Compare today's probable starters against pending bets; fire one
    Pushover per scratched pitcher. The slate snapshot is used to find
    the replacement on the same side of the matchup so the message can
    name who's starting instead.

    Bet-level filtering: only today's pending bets count (matches
    check_live_alerts). Wrapped in broad try/except by the caller so a
    transient MLB Stats API failure never breaks the alerts loop.
    """
    # Local imports: avoids a circular dep at module-import time and
    # keeps notify.py importable in contexts that don't want the full
    # data layer (e.g., tooling, simple settle scripts).
    from . import wagers
    from .fetch import todays_probable_starters

    try:
        target = date.fromisoformat(target_iso)
    except ValueError:
        return

    bets = wagers.load_bets()["bets"]
    todays_pending = [
        b for b in bets
        if b.get("result") not in ("W", "L") and (b.get("date") or "") == target_iso
    ]
    if not todays_pending:
        return

    bet_pids: set[int] = set()
    for b in todays_pending:
        for leg in b.get("legs") or []:
            pid = leg.get("pitcher_id")
            if isinstance(pid, int):
                bet_pids.add(pid)
    if not bet_pids:
        return

    try:
        starters = todays_probable_starters(target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scratch check: probable_starters failed: %s", exc)
        return

    starter_ids = {s["pitcher_id"] for s in starters}
    starters_by_game: dict[int, list[dict]] = {}
    for s in starters:
        starters_by_game.setdefault(s["game_pk"], []).append(s)

    # Slate snapshot maps the bet's original pitcher_id → game_pk + side,
    # so we can name the replacement starter on the same matchup side
    # (home vs away) rather than guessing.
    slate_path = OUTPUT_DIR / f"pitcher_ks_{target_iso}_slate.csv"
    slate_by_id: dict[int, dict] = {}
    if slate_path.exists():
        try:
            with slate_path.open() as f:
                for row in csv.DictReader(f):
                    try:
                        pid = int(row["pitcher_id"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    slate_by_id[pid] = row
        except OSError as exc:
            logger.warning("scratch check: slate read failed: %s", exc)

    for b in todays_pending:
        for leg in b.get("legs") or []:
            pid = leg.get("pitcher_id")
            if not isinstance(pid, int):
                continue
            if pid in starter_ids:
                continue  # still on the card
            if not _claim_key(f"{target_iso}:scratch:{pid}", target_iso):
                continue

            original_name = leg.get("pitcher") or "?"
            replacement_name = None
            slate_row = slate_by_id.get(pid)
            if slate_row:
                try:
                    game_pk = int(slate_row.get("game_pk") or 0) or None
                except (TypeError, ValueError):
                    game_pk = None
                if game_pk is not None:
                    same_side = str(slate_row.get("is_home", "")).strip().lower() in ("true", "1")
                    candidates = [
                        s for s in starters_by_game.get(game_pk, [])
                        if bool(s.get("is_home")) == same_side
                    ]
                    if candidates:
                        replacement_name = candidates[0].get("pitcher_name")

            line = leg.get("line")
            ou = leg.get("ou") or ""
            line_tag = ""
            if line is not None and ou:
                line_tag = f" ({ou}{line:g})"

            title = f"Scratched: {original_name}{line_tag}"
            if replacement_name:
                msg = f"Now starting: {replacement_name}"
            else:
                msg = "No replacement listed yet"
            send_pushover(title, msg)


# ---------- Odds API quota threshold alerts ----------


def _claim_quota_threshold(month_iso: str, label: str) -> bool:
    """Track which quota thresholds have already alerted this month.
    Separate namespace from the daily 'seen' map so the 7-day prune in
    _claim_key doesn't wipe monthly state."""
    state = _load_notify_state()
    quota = state.setdefault("quota_alerted", {})
    # Drop entries from any month other than current — keeps state lean
    # and ensures a new month resets the alert budget cleanly.
    for k in list(quota.keys()):
        if k != month_iso:
            del quota[k]
    fired = quota.setdefault(month_iso, [])
    if label in fired:
        return False
    fired.append(label)
    _save_notify_state(state)
    return True


def check_quota_alerts() -> None:
    """Fire one Pushover per crossed threshold (80% / 90% / 95%) per
    month based on output/odds_api_usage.json. Only the highest-yet
    threshold fires — lower ones already fired earlier in the month
    (or are claimed silently if usage jumped past them in one call).
    """
    path = OUTPUT_DIR / "odds_api_usage.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    try:
        used = float(data.get("used") or 0)
        cap = float(data.get("cap") or 0)
    except (TypeError, ValueError):
        return
    if cap <= 0:
        return
    pct = used / cap

    today = date.today()
    month_iso = f"{today.year:04d}-{today.month:02d}"

    # Highest first: a single tick that crosses 80→90 should alert at
    # 90, not 80 (90 is more useful info). Lower thresholds get marked
    # claimed silently so they don't fire later in the same month.
    THRESHOLDS = [(0.95, "95"), (0.90, "90"), (0.80, "80")]
    fired_label = None
    fired_threshold = None
    for threshold, label in THRESHOLDS:
        if pct >= threshold:
            fired_label = label
            fired_threshold = threshold
            break
    if fired_label is None:
        return

    if not _claim_quota_threshold(month_iso, fired_label):
        return
    # Silently claim every LOWER threshold so a later tick doesn't ping
    # at "80" once we're past 90 — they're redundant. Higher thresholds
    # stay open: usage might keep rising and warrant a more urgent ping.
    for threshold, label in THRESHOLDS:
        if threshold < fired_threshold:
            _claim_quota_threshold(month_iso, label)

    remaining = max(0, int(cap - used))
    send_pushover(
        f"Odds API: {int(pct * 100)}% used",
        f"{int(used)}/{int(cap)} this month · {remaining} credits left",
    )
