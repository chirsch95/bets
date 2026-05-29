#!/usr/bin/env python3
"""Capture Underdog's CLOSING board for CLV (#4).

Usage (near first pitch, on the Air where ANTHROPIC_API_KEY lives):
    .venv/bin/python capture_ud_close.py screenshot1.png [screenshot2.png ...]
    .venv/bin/python capture_ud_close.py --date 2026-05-29 shot.png

Reuses bets.ud_vision (the same vision extraction the UD Lab uses) to read the
line + Higher/Lower multipliers off each screenshot and match to today's
slate, then writes data/ud_lines_close_<date>.json in the SAME {line,hi,lo}
schema as the entry board (ud_lines_<date>.json). grade_ud.py reads it and
reports per-pick closing-line value.

Why a separate file: the entry board (ud_lines_<date>.json) is the price you
actually bet at, captured in the morning. CLV needs a SECOND snapshot near
lock; overwriting the entry board would destroy the comparison. This never
touches the entry board or any production state — it only writes the _close_
file.

Like the UD Lab import, extraction is not blind-trusted: it prints every
matched row for eyeball review before (and after) writing.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from bets import ud_vision
from bets.config import DATA_DIR

_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
          "webp": "image/webp", "gif": "image/gif"}


def main(argv):
    target = date.today()
    paths = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--date":
            target = date.fromisoformat(argv[i + 1])
            i += 2
            continue
        paths.append(Path(a))
        i += 1

    if not paths:
        print(__doc__)
        return 1

    # Merge into any existing closing board for the date: a slate's games are
    # staggered (tonight 5:40–9:15 CT) and UD locks each pick at its own first
    # pitch, so the most-mature pre-lock price is captured by shooting more
    # than once through the evening. Each run upserts — the latest capture of a
    # pitcher wins — so an early shot (full board) and a late shot (the late
    # games near their own close) compose instead of clobbering.
    out = DATA_DIR / f"ud_lines_close_{target.isoformat()}.json"
    board: dict[str, dict] = {}
    if out.exists():
        try:
            board = {str(k): v for k, v in json.loads(out.read_text()).items()}
            print(f"Merging into existing {out.name} ({len(board)} rows already captured).\n")
        except (json.JSONDecodeError, OSError):
            board = {}
    for p in paths:
        if not p.exists():
            print(f"  ! missing file: {p}")
            continue
        media = _MEDIA.get(p.suffix.lower().lstrip("."), "image/png")
        print(f"Extracting {p.name} …")
        result = ud_vision.extract(p.read_bytes(), media, target)
        for m in result["matched"]:
            key = str(m["pitcher_id"])
            updated = key in board
            board[key] = {"line": m["line"], "hi": m["higher"], "lo": m["lower"]}
            mult = (f"  hi={m['higher']} lo={m['lower']}"
                    if (m["higher"] is not None or m["lower"] is not None) else "  (symmetric)")
            print(f"   ✓ {m['slate_name']:<22} line {m['line']}{mult}"
                  + ("  (updated)" if updated else ""))
        for u in result["unmatched"]:
            print(f"   ? UNMATCHED: {u['name']} line {u['line']} (skipped)")

    if not board:
        print("No rows matched the slate — nothing written.")
        return 1

    out.write_text(json.dumps(board, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote {len(board)} closing rows → {out}")
    print("Now run:  .venv/bin/python grade_ud.py "
          + target.isoformat() + "   (CLV section will populate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
