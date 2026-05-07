"""Local Flask server for the dashboard with refresh + settle buttons.

Run with:
    python -m bets.server

Default URL: http://127.0.0.1:8000 (port 5000 is taken by macOS AirPlay
Receiver out of the box — change the port via the BETS_PORT env var if
you want a different one, e.g. BETS_PORT=5050 python -m bets.server).

Endpoints:
    GET  /          serves output/index.html (regenerates if missing)
    POST /refresh   runs bets.main + bets.hitters, redirects /
    POST /settle    settles yesterday's projections (both), redirects /
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_file, send_from_directory

from . import health, live, notify, wagers
from .config import OUTPUT_DIR, PROJECT_ROOT
from .settle import settle_date, settle_hitters_date

# The pipeline (bets.main) and dashboard renderer (bets.web) are invoked
# as subprocesses, not direct calls. A long-running Flask process freezes
# its `from .web import generate` binding at startup, so an in-process
# call would clobber output/index.html with whatever web.py looked like
# when this server booted — even if the file has since been edited.

load_dotenv(PROJECT_ROOT / ".env")

app = Flask(__name__)

# Serialize pipeline runs. Two simultaneous /refresh clicks would each
# burn ~16 Odds API credits and race to overwrite the same CSV (last
# writer wins, slate snapshot too because of TOCTOU). One run at a time.
_pipeline_lock = threading.Lock()


@app.get("/")
def index():
    target = _today()
    out_path = OUTPUT_DIR / "index.html"
    if not out_path.exists():
        subprocess.run(
            [sys.executable, "-m", "bets.web", target.isoformat()],
            cwd=PROJECT_ROOT,
            check=False,
        )
    if not out_path.exists():
        return (
            "<p>No dashboard yet. POST /refresh to generate today's slate.</p>",
            404,
        )
    return send_file(out_path)


@app.get("/<path:filename>")
def output_file(filename: str):
    """Serve any other file from output/ as a static asset (CSVs, etc.).

    The dashboard's JS fetches `./pitcher_ks_<date>.csv` etc. when running
    on localhost; this route handles those requests. Restricts to the
    output directory to prevent path-traversal escapes.
    """
    if ".." in filename or filename.startswith("/"):
        return "forbidden", 403
    return send_from_directory(OUTPUT_DIR, filename)


@app.post("/refresh")
def refresh():
    if not _pipeline_lock.acquire(blocking=False):
        return "<pre>A refresh is already running. Wait for it to finish.</pre>", 409
    try:
        # bets.main runs the pitcher pipeline and ends with a dashboard
        # regen, so this single subprocess covers both. Hitter pipeline
        # is paused — re-enable inside bets.main when ready.
        proc = subprocess.run(
            [sys.executable, "-m", "bets.main"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            body = (proc.stderr or proc.stdout or "pipeline exited non-zero").strip()
            return f"<pre>refresh failed:\n{body}</pre>", 500
        return redirect("/")
    finally:
        _pipeline_lock.release()


@app.post("/settle")
def settle():
    target_str = request.form.get("date", "").strip()
    if target_str:
        try:
            target = datetime.strptime(target_str, "%Y-%m-%d").date()
        except ValueError:
            return f"<pre>Bad date: {target_str}</pre>", 400
    else:
        target = _today() - timedelta(days=1)
    if not _pipeline_lock.acquire(blocking=False):
        return "<pre>A pipeline run is already in progress. Wait for it to finish.</pre>", 409
    try:
        errors: list[str] = []
        try:
            settle_date(target)
        except Exception as e:  # noqa: BLE001
            errors.append(f"pitcher settle failed: {e}")
        try:
            settle_hitters_date(target)
        except Exception as e:  # noqa: BLE001
            errors.append(f"hitter settle failed: {e}")
        proc = subprocess.run(
            [sys.executable, "-m", "bets.web", _today().isoformat()],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            errors.append(
                "dashboard regen failed: "
                + (proc.stderr or proc.stdout or "non-zero exit").strip()
            )
        if errors:
            body = "<pre>" + "\n".join(errors) + "</pre>"
            return body, 500
        return redirect("/")
    finally:
        _pipeline_lock.release()


@app.post("/push")
def push():
    """Stage output/, commit if there are changes, push to origin/main.
    The Air's gitpull picks it up within 60s. Reuses the pipeline lock
    so we never push partial state mid-refresh."""
    if not _pipeline_lock.acquire(blocking=False):
        return "<pre>A pipeline run is already in progress. Wait for it to finish.</pre>", 409
    try:
        add = subprocess.run(
            ["git", "add", "output/"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            return f"<pre>git add failed:\n{add.stderr}</pre>", 500
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=PROJECT_ROOT,
        )
        if diff.returncode != 0:
            msg = f"refresh: {_today().isoformat()} dashboard update"
            commit = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if commit.returncode != 0:
                return f"<pre>git commit failed:\n{commit.stderr}</pre>", 500
        push_proc = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if push_proc.returncode != 0:
            return f"<pre>git push failed:\n{push_proc.stderr}</pre>", 500
        return redirect("/")
    finally:
        _pipeline_lock.release()


@app.get("/api/bets")
def api_list_bets():
    state = wagers.load_bets()
    return jsonify({"bets": state["bets"], "totals": wagers.totals(state)})


@app.post("/api/bets")
def api_add_bet():
    payload = request.get_json(silent=True) or {}
    bet = wagers.add_bet(payload)
    return jsonify({"bet": bet, "totals": wagers.totals()})


def _settle_bet_with_notify(bet_id: str, payload: dict) -> dict | None:
    """Update a bet and fire the bet-settled Pushover on pending → W/L.
    Shared by PUT /api/bets and the background alerts loop so both paths
    hit the same notification flow."""
    prior = next(
        (b for b in wagers.load_bets()["bets"] if b.get("id") == bet_id),
        None,
    )
    prior_result = prior.get("result") if prior else None
    updated = wagers.update_bet(bet_id, payload)
    if updated is None:
        return None
    if prior_result is None and updated.get("result") in ("W", "L"):
        formatted = notify.format_bet_settle(updated)
        if formatted:
            notify.send_pushover(*formatted)
    return updated


@app.put("/api/bets/<bet_id>")
def api_update_bet(bet_id: str):
    payload = request.get_json(silent=True) or {}
    updated = _settle_bet_with_notify(bet_id, payload)
    if updated is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"bet": updated, "totals": wagers.totals()})


@app.delete("/api/bets/<bet_id>")
def api_delete_bet(bet_id: str):
    if not wagers.delete_bet(bet_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "totals": wagers.totals()})


@app.get("/api/slate-pitchers")
def api_slate_pitchers():
    """Returns today's pitchers as the Bets-tab dropdown source. Date
    overridable via ?date=YYYY-MM-DD for testing/historical entry."""
    target_str = request.args.get("date", "").strip()
    if target_str:
        try:
            target = datetime.strptime(target_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": f"bad date: {target_str}"}), 400
    else:
        target = _today()
    return jsonify({"date": target.isoformat(), "pitchers": live.slate_pitchers(target)})


@app.get("/api/health")
def api_health():
    """Read-only snapshot of the watcher's per-source state. Refreshed
    by the launchd job (bets.health), not by this read."""
    return jsonify(health.latest_snapshot())


@app.get("/api/live-ks")
def api_live_ks():
    """Look up live K + game status for ?ids=<csv of pitcher_ids>.
    60s in-memory cache shields the MLB API from refresh-button mash
    and the Bets tab's 60s auto-poll (cache TTL matches poll cadence
    so the second client tick usually hits the cache for free)."""
    ids_raw = request.args.get("ids", "").strip()
    if not ids_raw:
        return jsonify({"results": {}})
    pitcher_ids: list[int] = []
    for part in ids_raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pitcher_ids.append(int(part))
        except ValueError:
            return jsonify({"error": f"bad pitcher id: {part}"}), 400
    target_str = request.args.get("date", "").strip()
    if target_str:
        try:
            target = datetime.strptime(target_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": f"bad date: {target_str}"}), 400
    else:
        target = _today()
    results = live.live_ks(pitcher_ids, target)
    # Piggyback live-game alerts (pulled starter / parlay one-to-go) on
    # the dashboard's existing 60s poll. Wrapped broadly so a bug here
    # never breaks the live-ks fetch the UI depends on.
    try:
        notify.check_live_alerts(results, target.isoformat())
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("live alerts check failed: %s", exc)
    return jsonify({"date": target.isoformat(), "results": results})


def _today() -> date:
    return date.today()


def _leg_hit_state(ks, line, ou, status, done):
    """Python port of legHitState() in web.py. Returns 'hit' | 'miss' | None."""
    if ks is None or line is None:
        return None
    if ks > line:
        return "hit" if ou == "O" else "miss"
    if status == "Final" or done:
        return "hit" if ou == "U" else "miss"
    return None


def _parlay_verdict(leg_states: list) -> str | None:
    """Python port of parlayRollupClass(). Returns 'W' | 'L' | None."""
    if any(s == "miss" for s in leg_states):
        return "L"
    if leg_states and all(s == "hit" for s in leg_states):
        return "W"
    return None


_last_scratch_check_at = 0.0


def _alerts_tick() -> None:
    """One pass of the background loop: fetch live K for any pending
    bets, fire pulled-starter / parlay-one-to-go alerts, then auto-settle
    any bets with definitive verdicts (which fires the bet-settled alert
    via _settle_bet_with_notify). Also runs the bet-independent quota
    threshold check and (when there are pending bets today) the
    pre-game scratch check on a 5-min cooldown."""
    global _last_scratch_check_at
    target = _today()
    target_iso = target.isoformat()

    # Quota-threshold alerts run every tick — file read only, doesn't
    # depend on bets. Skipped on errors, never breaks the tick.
    try:
        notify.check_quota_alerts()
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("alerts tick: check_quota_alerts failed: %s", exc)

    state = wagers.load_bets()
    pending = [b for b in state["bets"] if not b.get("result")]

    # Scratch check: needs at least one pending bet for today, and is
    # rate-limited to 5 min so we don't pummel the MLB Stats API every
    # 60s. Scratches usually announce 1-3 hours pre-game so the cooldown
    # is plenty of resolution.
    todays_pending = [b for b in pending if (b.get("date") or "") == target_iso]
    if todays_pending and time.time() - _last_scratch_check_at >= 300:
        _last_scratch_check_at = time.time()
        try:
            notify.check_scratch_alerts(target_iso)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("alerts tick: check_scratch_alerts failed: %s", exc)

    pids: set[int] = set()
    for b in pending:
        for leg in b.get("legs") or []:
            pid = leg.get("pitcher_id")
            if isinstance(pid, int):
                pids.add(pid)
    if not pids:
        return
    try:
        results = live.live_ks(list(pids), target)
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("alerts tick: live_ks failed: %s", exc)
        return
    try:
        notify.check_live_alerts(results, target_iso)
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("alerts tick: check_live_alerts failed: %s", exc)
    for bet in pending:
        leg_states = []
        for leg in bet.get("legs") or []:
            pid = leg.get("pitcher_id")
            data = results.get(pid) if isinstance(pid, int) else None
            if not data:
                leg_states.append(None)
                continue
            leg_states.append(
                _leg_hit_state(
                    data.get("ks"),
                    leg.get("line"),
                    leg.get("ou"),
                    data.get("status"),
                    bool(data.get("done")),
                )
            )
        verdict = _parlay_verdict(leg_states)
        if verdict is None or bet.get("result") == verdict:
            continue
        payout = (
            round((bet.get("stake") or 0) * (bet.get("odds") or 0), 2)
            if verdict == "W"
            else 0
        )
        try:
            _settle_bet_with_notify(bet["id"], {"result": verdict, "payout": payout})
        except Exception as exc:  # noqa: BLE001
            app.logger.warning(
                "alerts tick: auto-settle for %s failed: %s", bet.get("id"), exc
            )


def _start_alerts_loop() -> None:
    """Daemon thread that runs _alerts_tick every 60s. Independent of
    the browser, so notifications (pulled / one-to-go / bet-settled) and
    auto-settle fire as long as the local server is running, even if the
    user is on the pitcher tab or has the browser closed."""
    def _loop():
        while True:
            try:
                _alerts_tick()
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("alerts loop tick crashed: %s", exc)
            time.sleep(60)
    threading.Thread(target=_loop, name="alerts-loop", daemon=True).start()


def main() -> None:
    port = int(os.environ.get("BETS_PORT", "8000"))
    print(f"Starting dashboard server at http://127.0.0.1:{port}")
    print("  GET  /         — view dashboard")
    print("  POST /refresh  — re-pull odds and recompute")
    print("  POST /settle   — settle yesterday")
    print("  POST /push     — commit output/ + push to origin/main (deploys to Air)")
    print("  *    /api/bets — local-only bet ledger CRUD")
    print("  GET  /api/slate-pitchers — today's pitcher list for picker")
    print("  GET  /api/live-ks?ids=… — live K + game status, 60s cache")
    print("  GET  /api/health — pipeline + swstr freshness + retry state")
    print("  +    background alerts loop — fires Pushover + auto-settles every 60s")
    _start_alerts_loop()
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
