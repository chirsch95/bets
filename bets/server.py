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
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, redirect, request, send_file, send_from_directory

from .config import OUTPUT_DIR, PROJECT_ROOT
from .hitters import run as run_hitter_projections
from .main import run as run_projections
from .settle import settle_date, settle_hitters_date
from .web import generate as generate_dashboard

load_dotenv(PROJECT_ROOT / ".env")

app = Flask(__name__)


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
    errors: list[str] = []
    try:
        run_projections()
    except Exception as e:  # noqa: BLE001
        errors.append(f"pitcher refresh failed: {e}")
    try:
        run_hitter_projections()
    except Exception as e:  # noqa: BLE001
        errors.append(f"hitter refresh failed: {e}")
    # Always regenerate so partial success still updates the dashboard.
    try:
        generate_dashboard(_today())
    except Exception as e:  # noqa: BLE001
        errors.append(f"dashboard regen failed: {e}")
    if errors:
        body = "<pre>" + "\n".join(errors) + "</pre>"
        return body, 500
    return redirect("/")


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


def _today() -> date:
    return date.today()


def main() -> None:
    port = int(os.environ.get("BETS_PORT", "8000"))
    print(f"Starting dashboard server at http://127.0.0.1:{port}")
    print("  GET  /         — view dashboard")
    print("  POST /refresh  — re-pull odds and recompute")
    print("  POST /settle   — settle yesterday")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
