#!/usr/bin/env python3
"""Capture Underdog's CLOSING board for CLV (#4).

Usage (near first pitch, on the Air where ANTHROPIC_API_KEY lives):
    .venv/bin/python capture_ud_close.py screenshot1.png [screenshot2.png ...]
    .venv/bin/python capture_ud_close.py --date 2026-05-29 shot.png
    .venv/bin/python capture_ud_close.py --as-of 2026-05-29T22:30:00Z shot.png

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

Started-game guard: a screenshot taken late in the evening still shows pitchers
whose games have already started, but UD then displays a LIVE in-game line (or
a locked one), NOT a closing price. Capturing that would poison the CLV with a
non-close value. So each matched pitcher is accepted only if its game had NOT
yet started at the screenshot's capture time (file mtime, or --as-of). Started
games are skipped, leaving whatever earlier pre-game capture stands. This makes
multi-shot merging safe: a late shot contributes only the still-pre-game (late)
games and can't clobber an early shot's valid closes.

Extraction is not blind-trusted: every matched / skipped / unmatched row is
printed for review.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from bets import ud_vision
from bets.config import DATA_DIR, OUTPUT_DIR

_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
          "webp": "image/webp", "gif": "image/gif"}


def _load_game_times(target: date) -> dict[str, datetime]:
    """{pid: first-pitch datetime (UTC)} from the slate CSV, for the
    started-game guard. Empty if no slate file."""
    for name in (f"pitcher_ks_{target.isoformat()}_slate.csv",
                 f"pitcher_ks_{target.isoformat()}.csv"):
        p = OUTPUT_DIR / name
        if not p.exists():
            continue
        out: dict[str, datetime] = {}
        with p.open() as f:
            for r in csv.DictReader(f):
                pid = str(r.get("pitcher_id", "")).strip()
                iso = (r.get("game_datetime_utc") or "").strip()
                if not (pid and iso):
                    continue
                try:
                    out[pid] = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return out
    return {}


def _capture_time(path: Path, as_of: datetime | None) -> datetime:
    """When the screenshot was taken: explicit --as-of, else file mtime.
    (scp the originals with -p to preserve mtime, or pass --as-of.)"""
    if as_of is not None:
        return as_of
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def main(argv):
    target = date.today()
    as_of: datetime | None = None
    paths = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--date":
            target = date.fromisoformat(argv[i + 1]); i += 2; continue
        if a == "--as-of":
            as_of = datetime.fromisoformat(argv[i + 1].replace("Z", "+00:00"))
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
            i += 2; continue
        paths.append(Path(a)); i += 1

    if not paths:
        print(__doc__)
        return 1

    game_times = _load_game_times(target)
    if not game_times:
        print(f"  ! No slate CSV for {target} — cannot apply the started-game "
              "guard. Proceeding without it (verify late-game lines by hand).")

    # Merge into any existing closing board for the date. Each run upserts —
    # the latest PRE-GAME capture of a pitcher wins — so an early full-board
    # shot and a late shot (the late games near their own close) compose.
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
        cap = _capture_time(p, as_of)
        media = _MEDIA.get(p.suffix.lower().lstrip("."), "image/png")
        print(f"Extracting {p.name} … (captured {cap.astimezone().strftime('%-I:%M %p %Z')})")
        result = ud_vision.extract(p.read_bytes(), media, target)
        for m in result["matched"]:
            key = str(m["pitcher_id"])
            gt = game_times.get(key)
            if gt is not None and cap >= gt:
                # Game already underway at capture → UD's line is live/locked,
                # not a close. Never overwrite an earlier valid pre-game value.
                print(f"   ⏸ {m['slate_name']:<22} game already started "
                      f"({gt.astimezone().strftime('%-I:%M %p')}) — skipped (live line, not a close)")
                continue
            updated = key in board
            board[key] = {"line": m["line"], "hi": m["higher"], "lo": m["lower"]}
            mult = (f"  hi={m['higher']} lo={m['lower']}"
                    if (m["higher"] is not None or m["lower"] is not None) else "  (symmetric)")
            print(f"   ✓ {m['slate_name']:<22} line {m['line']}{mult}"
                  + ("  (updated)" if updated else ""))
        for u in result["unmatched"]:
            print(f"   ? UNMATCHED: {u['name']} line {u['line']} (skipped — not on slate)")

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
