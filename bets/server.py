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
import threading
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_file, send_from_directory

from . import live, wagers
from .config import OUTPUT_DIR, PROJECT_ROOT
from .hitters import run as run_hitter_projections
from .main import run as run_projections
from .settle import settle_date, settle_hitters_date
from .web import generate as generate_dashboard

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
        generate_dashboard(target)
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
        errors: list[str] = []
        try:
            run_projections()
        except Exception as e:  # noqa: BLE001
            errors.append(f"pitcher refresh failed: {e}")
        # Hitter pipeline paused — re-enable by un-commenting once the
        # pitcher model is validated and you've flipped SHOW_HITTERS in web.py.
        # try:
        #     run_hitter_projections()
        # except Exception as e:  # noqa: BLE001
        #     errors.append(f"hitter refresh failed: {e}")
        try:
            generate_dashboard(_today())
        except Exception as e:  # noqa: BLE001
            errors.append(f"dashboard regen failed: {e}")
        if errors:
            body = "<pre>" + "\n".join(errors) + "</pre>"
            return body, 500
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
        try:
            generate_dashboard(_today())
        except Exception as e:  # noqa: BLE001
            errors.append(f"dashboard regen failed: {e}")
        if errors:
            body = "<pre>" + "\n".join(errors) + "</pre>"
            return body, 500
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


@app.put("/api/bets/<bet_id>")
def api_update_bet(bet_id: str):
    payload = request.get_json(silent=True) or {}
    updated = wagers.update_bet(bet_id, payload)
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
    return jsonify({
        "date": target.isoformat(),
        "results": live.live_ks(pitcher_ids, target),
    })


def _today() -> date:
    return date.today()


def main() -> None:
    port = int(os.environ.get("BETS_PORT", "8000"))
    print(f"Starting dashboard server at http://127.0.0.1:{port}")
    print("  GET  /         — view dashboard")
    print("  POST /refresh  — re-pull odds and recompute")
    print("  POST /settle   — settle yesterday")
    print("  *    /api/bets — local-only bet ledger CRUD")
    print("  GET  /api/slate-pitchers — today's pitcher list for picker")
    print("  GET  /api/live-ks?ids=… — live K + game status, 60s cache")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
