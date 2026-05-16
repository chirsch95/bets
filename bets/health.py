"""Health watcher for the bets data pipeline.

Designed to run on the M1 Air via launchd every 30 min during slate
hours. For each source it performs a staleness check; if stale and
we're inside the active window, it auto-retries (up to RETRY_CAP per
source per day). When the cap is hit, fires a single Pushover alert
and stops retrying for the rest of the day.

Two sources today:

  pipeline — today's pitcher_ks CSV must exist by SLATE_EXPECTED_HOUR
    with at least one row carrying an Odds API line, the slate-snapshot
    twin must be present, and (after LINEUPS_EXPECTED_HOUR) at least
    one row must carry an opposing lineup. Retry POSTs localhost Flask
    /refresh, which runs the pipeline and (with BETS_AUTO_PUSH=1)
    commits+pushes atomically under Flask's pipeline lock — so the
    gitpull cron never observes a dirty working tree mid-refresh.

  swstr — data/swstr_<season>.json must be < 24h old. Retry refreshes
    the cache in-process (data/ is gitignored, so no conflicts).

Daily reset: state["date"] != today wipes the retry counters and
"alerted" flag — fresh budget every morning.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .config import DATA_DIR, OUTPUT_DIR, PROJECT_ROOT
from .notify import send_pushover

logger = logging.getLogger("bets.health")

HEALTH_STATE_PATH = DATA_DIR / "health_state.json"

# Budget per source per day. After this many retries without recovery,
# we alert once and stop — protects against runaway loops on auth
# failures, expired keys, etc.
RETRY_CAP = 3

# Active hours (local time). Outside this, staleness is recorded but
# retries are deferred — no point burning Odds API credits at 3am.
# Window starts at 7am so the first morning tick can auto-trigger
# a local /refresh (settle yesterday + project today) if the slate
# hasn't been produced yet.
ACTIVE_HOUR_START = 7
ACTIVE_HOUR_END = 21

# Hour after which a missing slate is "stale". Matched to ACTIVE_HOUR_START
# so the morning auto-trigger fires immediately rather than waiting until
# late morning. Idempotent: if the laptop already produced today's CSV
# before 7am, the watcher sees fresh and no-ops.
SLATE_EXPECTED_HOUR = 7

# Hour after which missing lineups are "stale". MLB usually posts
# lineups 2-3 hours before first pitch.
LINEUPS_EXPECTED_HOUR = 15

PIPELINE_SOURCE = "pipeline"
SWSTR_SOURCE = "swstr"


# ---------- staleness checks ----------


@dataclass
class CheckResult:
    fresh: bool
    detail: str
    last_updated: str | None = None


def _now_local() -> datetime:
    return datetime.now()


def _today() -> date:
    return _now_local().date()


def _csv_today_path() -> Path:
    return OUTPUT_DIR / f"pitcher_ks_{_today().isoformat()}.csv"


def _slate_today_path() -> Path:
    return OUTPUT_DIR / f"pitcher_ks_{_today().isoformat()}_slate.csv"


def _swstr_today_path() -> Path:
    return DATA_DIR / f"swstr_{_today().year}.json"


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()
    return ts.isoformat(timespec="seconds")


def _read_csv(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def check_pipeline() -> CheckResult:
    """Stale if today's slate CSV is missing or under-populated past
    its expected hour. The same CSV carries lines and lineups, so one
    file covers three logical aspects."""
    now = _now_local()
    csv_path = _csv_today_path()
    slate_path = _slate_today_path()
    last_updated = _mtime_iso(csv_path)

    if now.hour < SLATE_EXPECTED_HOUR:
        return CheckResult(True, "before expected slate hour", last_updated)

    if not csv_path.exists():
        return CheckResult(False, "today's pitcher_ks CSV missing", None)

    csv_mtime = datetime.fromtimestamp(csv_path.stat().st_mtime).date()
    if csv_mtime != _today():
        return CheckResult(False, "pitcher_ks CSV not refreshed today", last_updated)

    rows = _read_csv(csv_path)
    if not rows:
        return CheckResult(False, "pitcher_ks CSV has no rows", last_updated)

    lines_present = sum(1 for r in rows if (r.get("line") or "").strip())
    if lines_present == 0:
        return CheckResult(False, "no Odds API lines populated", last_updated)

    if not slate_path.exists():
        return CheckResult(False, "slate snapshot not yet written", last_updated)

    if now.hour >= LINEUPS_EXPECTED_HOUR:
        lineups_present = sum(
            1 for r in rows
            if (r.get("opp_lineup_json") or "").strip() not in ("", "[]")
        )
        if lineups_present == 0:
            return CheckResult(False, "no opposing lineups populated", last_updated)

    return CheckResult(True, "ok", last_updated)


def check_swstr() -> CheckResult:
    path = _swstr_today_path()
    last_updated = _mtime_iso(path)
    if not path.exists():
        return CheckResult(False, "swstr cache file missing", None)
    age_hours = (_now_local().timestamp() - path.stat().st_mtime) / 3600
    if age_hours > 24:
        return CheckResult(False, f"swstr cache {age_hours:.1f}h old", last_updated)
    return CheckResult(True, f"ok ({age_hours:.1f}h old)", last_updated)


# ---------- retry actions ----------


def retry_pipeline_local() -> tuple[bool, str]:
    """POST localhost Flask /refresh. Flask runs bets.main and (with
    BETS_AUTO_PUSH=1) commits+pushes under its pipeline lock — so this
    can't race a user-initiated /refresh, and the gitpull cron never
    observes a dirty working tree. Blocks until the pipeline finishes
    (~10-30s typical); fine because the watcher tick is every 30 min.
    A 409 from Flask means a refresh is already running — counted as
    fail here, but the next tick will see fresh state and recover."""
    port = os.environ.get("BETS_PORT", "8000")
    url = f"http://localhost:{port}/refresh"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            status = resp.status
        if 200 <= status < 400:
            return True, f"local /refresh ok ({status})"
        return False, f"local /refresh returned {status}"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(500).decode("utf-8", "replace") if exc.fp else ""
        except Exception:  # noqa: BLE001
            pass
        return False, f"local /refresh HTTP {exc.code}: {body[:200].strip()}"
    except Exception as exc:  # noqa: BLE001
        return False, f"local /refresh failed: {exc}"


def retry_swstr_local() -> tuple[bool, str]:
    """Force-refresh the SwStr cache by deleting the file and re-running
    the lookup. Hits Baseball Savant directly — no API key needed."""
    path = _swstr_today_path()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            return False, f"could not remove stale cache: {exc}"
    try:
        from .fetch import pitcher_swstr_lookup
        result = pitcher_swstr_lookup(_today().year)
    except Exception as exc:  # noqa: BLE001
        return False, f"swstr lookup raised: {exc}"
    if result:
        return True, f"refreshed swstr cache for {len(result)} pitchers"
    return False, "swstr lookup returned empty payload"


# ---------- state ----------


def _load_state() -> dict:
    if not HEALTH_STATE_PATH.exists():
        return {}
    try:
        return json.loads(HEALTH_STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    HEALTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _today_sources(state: dict) -> dict:
    today = _today().isoformat()
    if state.get("date") != today:
        state.clear()
        state["date"] = today
        state["sources"] = {}
    return state.setdefault("sources", {})


def _source_state(sources: dict, name: str) -> dict:
    return sources.setdefault(name, {
        "retries": 0,
        "alerted": False,
        "last_attempt": None,
        "last_attempt_detail": None,
        "last_check_at": None,
        "last_check_fresh": True,
        "last_check_detail": None,
        "last_updated": None,
    })


# ---------- main loop ----------


@dataclass
class SourceConfig:
    name: str
    description: str
    check: Callable[[], CheckResult]
    retry: Callable[[], tuple[bool, str]]


SOURCES = [
    SourceConfig(
        name=PIPELINE_SOURCE,
        description="Pipeline (lines + lineups + slate)",
        check=check_pipeline,
        retry=retry_pipeline_local,
    ),
    SourceConfig(
        name=SWSTR_SOURCE,
        description="SwStr% pitcher cache",
        check=check_swstr,
        retry=retry_swstr_local,
    ),
]


def _in_active_window() -> bool:
    h = _now_local().hour
    return ACTIVE_HOUR_START <= h < ACTIVE_HOUR_END


def run_once() -> dict:
    """One health-check tick. Returns a snapshot of every source's
    state; also persists it to data/health_state.json."""
    state = _load_state()
    sources_state = _today_sources(state)
    in_window = _in_active_window()
    now_iso = _now_local().astimezone().isoformat(timespec="seconds")
    results: dict[str, dict] = {}

    for src in SOURCES:
        ss = _source_state(sources_state, src.name)
        try:
            check = src.check()
        except Exception as exc:  # noqa: BLE001
            check = CheckResult(False, f"check raised: {exc}", None)

        ss["last_check_at"] = now_iso
        ss["last_check_fresh"] = check.fresh
        ss["last_check_detail"] = check.detail
        ss["last_updated"] = check.last_updated

        if check.fresh:
            results[src.name] = _summary(src, ss, "fresh")
            continue

        if not in_window:
            results[src.name] = _summary(src, ss, "stale_off_hours")
            continue

        if ss["alerted"]:
            results[src.name] = _summary(src, ss, "stale_capped")
            continue

        if ss["retries"] >= RETRY_CAP:
            send_pushover(
                f"bets health: giving up on {src.name}",
                f"{src.description} still stale after {RETRY_CAP} retries. "
                f"{check.detail}. Manual intervention needed.",
            )
            ss["alerted"] = True
            results[src.name] = _summary(src, ss, "stale_capped")
            _save_state(state)
            continue

        ss["retries"] += 1
        ss["last_attempt"] = now_iso
        try:
            ok, detail = src.retry()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"retry raised: {exc}"
        ss["last_attempt_detail"] = f"{'ok' if ok else 'fail'}: {detail}"
        results[src.name] = _summary(src, ss, "retried_ok" if ok else "retried_fail")

        _save_state(state)

    _save_state(state)
    return {
        "date": state.get("date"),
        "in_active_window": in_window,
        "checked_at": now_iso,
        "sources": results,
    }


def _summary(src: SourceConfig, ss: dict, status: str) -> dict:
    return {
        "description": src.description,
        "fresh": ss.get("last_check_fresh"),
        "detail": ss.get("last_check_detail"),
        "last_updated": ss.get("last_updated"),
        "last_check_at": ss.get("last_check_at"),
        "retries": ss.get("retries", 0),
        "retry_cap": RETRY_CAP,
        "alerted": ss.get("alerted", False),
        "last_attempt": ss.get("last_attempt"),
        "last_attempt_detail": ss.get("last_attempt_detail"),
        "status": status,
    }


def latest_snapshot() -> dict:
    """Read-only view of the current state file — used by /api/health
    so dashboard reads don't trigger checks."""
    state = _load_state()
    today = _today().isoformat()
    if state.get("date") != today:
        return {
            "date": today,
            "in_active_window": _in_active_window(),
            "checked_at": None,
            "sources": {},
        }
    sources_state = state.get("sources", {})
    src_by_name = {s.name: s for s in SOURCES}
    return {
        "date": state.get("date"),
        "in_active_window": _in_active_window(),
        "checked_at": max(
            (ss.get("last_check_at") or "" for ss in sources_state.values()),
            default=None,
        ) or None,
        "sources": {
            name: _summary(src_by_name[name], ss, _status_from_state(ss))
            for name, ss in sources_state.items()
            if name in src_by_name
        },
    }


def _status_from_state(ss: dict) -> str:
    if ss.get("last_check_fresh"):
        return "fresh"
    if ss.get("alerted"):
        return "stale_capped"
    return "stale"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    snap = run_once()
    print(json.dumps(snap, indent=2))


if __name__ == "__main__":
    main()
