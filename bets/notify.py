"""Pushover notifications for bet settlement.

Reads PUSHOVER_TOKEN + PUSHOVER_USER from env (loaded by server.py via
.env). If either is missing, send_pushover() is a silent no-op — the
dashboard keeps working without notifications configured.

Sends fire on a daemon thread so the HTTP handler doesn't block on
Pushover's API.
"""

from __future__ import annotations

import logging
import os
import threading

import requests

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

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
