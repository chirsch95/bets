"""Vision extraction of Underdog screenshots → structured UD pricing.

Sends an uploaded UD pick'em board screenshot to the Claude API (vision),
parses each pitcher's strikeout line + Higher/Lower payout multipliers, and
matches the names to today's slate pitcher_ids so the UD Lab can prefill
without hand-typing. Powers POST /api/ud-screenshot.

Uses raw `requests` against the Anthropic Messages API (no extra dependency
— `requests` is already used throughout). Requires ANTHROPIC_API_KEY in the
environment (Air .env); raises a clear error if absent so the UI can tell
the user to add it.

Extraction is never blind-trusted: the endpoint returns matched + unmatched
rows and the UD Lab drops them into a *review* state for confirmation before
Save. A misread multiplier silently corrupts EV, so the human check matters.
"""

from __future__ import annotations

import base64
import json
import os
import re
import unicodedata
from datetime import date

import requests

from . import live

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# Sonnet is plenty for this OCR-grade extraction and far cheaper/faster than
# Opus. Pin a current vision-capable model.
VISION_MODEL = "claude-sonnet-4-6"
HTTP_TIMEOUT = 60

_PROMPT = (
    "This is a screenshot of the Underdog Fantasy pick'em board for MLB "
    "pitcher strikeouts. Extract EVERY pitcher row that is fully visible.\n\n"
    "For each pitcher return:\n"
    "  name   — the player name exactly as shown (e.g. \"C. Rodón\")\n"
    "  line   — the strikeout number shown under the name (e.g. 6.5)\n"
    "  higher — the payout multiplier on the Higher button (e.g. 1.04), or "
    "null if the button shows no multiplier number\n"
    "  lower  — the payout multiplier on the Lower button (e.g. 0.87), or "
    "null if none is shown\n\n"
    "Rules:\n"
    "- If a row is clipped at the top/bottom so you cannot read its line "
    "confidently, set line to null (do not guess).\n"
    "- Multipliers look like \"1.04x\" or \"0.87x\"; return just the number.\n"
    "- If both Higher and Lower show no multiplier, the pick is symmetric — "
    "set both to null.\n"
    "- Return ONLY a JSON array, no prose, no code fences. Example:\n"
    '[{"name":"C. Rodón","line":6.5,"higher":1.04,"lower":0.87},'
    '{"name":"G. Kirby","line":5.5,"higher":null,"lower":null}]'
)


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", _strip_accents(s or "").lower())


def _slate_index(target: date) -> dict:
    """Build {normalized_last_name: [(pid, full_name, first_initial)]} from
    today's slate so extracted "C. Rodón" can resolve to a pitcher_id."""
    idx: dict[str, list] = {}
    for p in live.slate_pitchers(target):
        full = (p.get("pitcher") or "").strip()
        pid = p.get("pitcher_id")
        if not full or pid is None:
            continue
        parts = [w for w in re.split(r"\s+", full) if w]
        if not parts:
            continue
        last = _norm(parts[-1])
        first_init = _norm(parts[0])[:1]
        idx.setdefault(last, []).append((pid, full, first_init))
    return idx


def _match(name: str, idx: dict):
    """Resolve an extracted UD name to (pid, full_name) or None."""
    parts = [w for w in re.split(r"\s+", (name or "").strip()) if w]
    if not parts:
        return None
    last = _norm(parts[-1])
    cands = idx.get(last)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][0], cands[0][1]
    # Multiple share a last name — disambiguate on first initial.
    first_init = _norm(parts[0])[:1]
    for pid, full, fi in cands:
        if fi and first_init and fi == first_init:
            return pid, full
    return None


def _parse_rows(text: str) -> list:
    """Pull the JSON array out of the model's reply, tolerating code fences."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t).rsplit("```", 1)[0]
    # Grab the first [...] block if there's any stray prose.
    m = re.search(r"\[.*\]", t, re.S)
    if m:
        t = m.group(0)
    data = json.loads(t)
    return data if isinstance(data, list) else []


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract(image_bytes: bytes, media_type: str, target: date) -> dict:
    """Run vision extraction on one screenshot and match to the slate.

    Returns {matched: [{pitcher_id, name, slate_name, line, higher, lower}],
             unmatched: [{name, line, higher, lower}]}.
    Raises RuntimeError if no API key is configured."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set on this host")
    if media_type not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        media_type = "image/png"
    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": VISION_MODEL,
        "max_tokens": 2000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    }
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json=body,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    blocks = resp.json().get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    rows = _parse_rows(text)

    idx = _slate_index(target)
    matched, unmatched = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = (row.get("name") or "").strip()
        rec = {
            "name": name,
            "line": _num(row.get("line")),
            "higher": _num(row.get("higher")),
            "lower": _num(row.get("lower")),
        }
        hit = _match(name, idx)
        if hit:
            pid, slate_name = hit
            matched.append({"pitcher_id": pid, "slate_name": slate_name, **rec})
        else:
            unmatched.append(rec)
    return {"matched": matched, "unmatched": unmatched}
