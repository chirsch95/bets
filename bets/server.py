"""Local Flask server for the dashboard with refresh + settle buttons.

Run with:
    python -m bets.server

Then open http://127.0.0.1:5000 in a browser.

Endpoints:
    GET  /          serves output/index.html (regenerates if missing)
    POST /refresh   runs bets.main (re-fetches odds, recomputes), redirects /
    POST /settle    settles yesterday's projections, redirects /

Both POSTs are synchronous — the browser waits while the run completes
(typically 10–30s for /refresh, 5–15s for /settle).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, redirect, request, send_file

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
    print("Starting dashboard server at http://127.0.0.1:5000")
    print("  GET  /         — view dashboard")
    print("  POST /refresh  — re-pull odds and recompute")
    print("  POST /settle   — settle yesterday")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
