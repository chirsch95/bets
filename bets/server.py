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

import base64
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
import re

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
)

from . import auth, bankroll, bet_record, health, live, notify, ud_lines, ud_vision, wagers
from .config import OUTPUT_DIR, PROJECT_ROOT
from .settle import settle_date, settle_hitters_date

# The pipeline (bets.main) and dashboard renderer (bets.web) are invoked
# as subprocesses, not direct calls. A long-running Flask process freezes
# its `from .web import generate` binding at startup, so an in-process
# call would clobber output/index.html with whatever web.py looked like
# when this server booted — even if the file has since been edited.

load_dotenv(PROJECT_ROOT / ".env")

# One-time migration: move legacy data/bets.json + data/bankroll.json
# into data/users/chad/. Idempotent — no-op once done.
auth.migrate_legacy_if_needed()

app = Flask(__name__)
app.secret_key = auth.get_or_create_secret_key()
# 30-day "remember me" sessions. Cookie itself is signed; lifetime is the
# permanent_session_lifetime cap, refreshed on activity by Flask.
from datetime import timedelta as _timedelta  # local: only needed for config
app.permanent_session_lifetime = _timedelta(days=30)

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


_PITCHER_KS_LIVE_RE = re.compile(r"^pitcher_ks_(\d{4}-\d{2}-\d{2})\.csv$")


@app.get("/<path:filename>")
def output_file(filename: str):
    """Serve any other file from output/ as a static asset (CSVs, etc.).

    The dashboard's JS fetches `./pitcher_ks_<date>.csv` etc. when running
    on localhost; this route handles those requests. Restricts to the
    output directory to prevent path-traversal escapes.

    Special case: the live `pitcher_ks_<date>.csv` is served through
    `live.pinned_csv_text()` so already-started pitchers display the
    morning slate's edge/p_over/line rather than the model's drifted
    in-progress recompute. Settled CSVs and the slate snapshot are
    served as-is.
    """
    if ".." in filename or filename.startswith("/"):
        return "forbidden", 403
    m = _PITCHER_KS_LIVE_RE.match(filename)
    if m:
        try:
            target = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            text = live.pinned_csv_text(target)
        except Exception:  # noqa: BLE001
            text = None
        if text is not None:
            return Response(
                text,
                mimetype="text/csv",
                headers={"Cache-Control": "no-cache"},
            )
        # Fall through to send_from_directory if pinning failed or the
        # file doesn't exist — preserves the existing 404 behavior.
    return send_from_directory(OUTPUT_DIR, filename)


def _commit_and_push_output() -> tuple[bool, str]:
    """Stage output/, commit if dirty, push to origin/main. Returns
    (ok, message). No-op (ok=True) when the working tree is clean."""
    add = subprocess.run(
        ["git", "add", "output/"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        return False, f"git add failed:\n{add.stderr}"
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
            return False, f"git commit failed:\n{commit.stderr}"
    push_proc = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if push_proc.returncode != 0:
        return False, f"git push failed:\n{push_proc.stderr}"
    return True, "pushed"


@app.post("/refresh")
def refresh():
    if not _pipeline_lock.acquire(blocking=False):
        return "<pre>A refresh is already running. Wait for it to finish.</pre>", 409
    try:
        # bets.main runs the pitcher pipeline and ends with a dashboard
        # regen, so this single subprocess covers both. Hitter pipeline
        # is paused — re-enable inside bets.main when ready.
        # force=1 (form param or query) appends --force-fetch, which
        # bypasses the all_covered short-circuit so books re-price.
        force = (request.form.get("force") or request.args.get("force") or "") == "1"
        argv = [sys.executable, "-m", "bets.main"]
        if force:
            argv.append("--force-fetch")
        proc = subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            body = (proc.stderr or proc.stdout or "pipeline exited non-zero").strip()
            return f"<pre>refresh failed:\n{body}</pre>", 500
        # On the prod host (Air), commit+push under the same lock so the
        # 60s gitpull cron never observes a dirty working tree mid-refresh.
        # Laptop dev runs leave BETS_AUTO_PUSH unset, so manual /push (or
        # plain git) still ships their work and dev cycles don't spam
        # commits.
        if os.environ.get("BETS_AUTO_PUSH") == "1":
            ok, push_msg = _commit_and_push_output()
            if not ok:
                return f"<pre>refresh ran, push failed:\n{push_msg}</pre>", 500
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
            from . import parlay_suggest as _pl
            _pl.settle_suggestions(target)
            _pl.settle_shadow_suggestions(target)
        except Exception as e:  # noqa: BLE001
            errors.append(f"parlay settle failed: {e}")
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
        if os.environ.get("BETS_AUTO_PUSH") == "1":
            ok, push_msg = _commit_and_push_output()
            if not ok:
                errors.append(f"auto-push failed: {push_msg}")
        if errors:
            body = "<pre>" + "\n".join(errors) + "</pre>"
            return body, 500
        return redirect("/")
    finally:
        _pipeline_lock.release()


@app.post("/push")
def push():
    """Stage output/, commit if there are changes, push to origin/main.
    Reuses the pipeline lock so we never push partial state mid-refresh."""
    if not _pipeline_lock.acquire(blocking=False):
        return "<pre>A pipeline run is already in progress. Wait for it to finish.</pre>", 409
    try:
        ok, msg = _commit_and_push_output()
        if not ok:
            return f"<pre>{msg}</pre>", 500
        return redirect("/")
    finally:
        _pipeline_lock.release()


@app.get("/api/whoami")
def api_whoami():
    """Returns auth + setup status. The bets-tab JS calls this on load
    to decide whether to render the login modal, the first-login wizard,
    or the normal bets UI. Always 200 — `user_id: null` means logged out."""
    uid = auth.current_user_id()
    if not uid or auth.get_user(uid) is None:
        return jsonify({"user_id": None, "display_name": None, "has_setup": False})
    prefs = auth.load_prefs(uid) or {}
    return jsonify({
        "user_id": uid,
        "display_name": prefs.get("display_name") or uid,
        "has_setup": auth.has_completed_setup(uid),
    })


@app.post("/api/login")
def api_login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    user = auth.verify_password(username, password)
    if user is None:
        return jsonify({"error": "invalid username or password"}), 401
    auth.login(user)
    prefs = auth.load_prefs(user["id"]) or {}
    return jsonify({
        "user_id": user["id"],
        "display_name": prefs.get("display_name") or user["id"],
        "has_setup": auth.has_completed_setup(user["id"]),
    })


@app.post("/api/logout")
def api_logout():
    auth.logout()
    return jsonify({"ok": True})


@app.post("/api/setup")
@auth.login_required
def api_setup():
    """First-login wizard target. Creates the user's bankroll + prefs.
    Idempotent on display_name / stake_units / pushover_user_key (those
    can be re-saved later). Refuses to overwrite an existing bankroll —
    seeding a starting balance is a one-time event."""
    uid = auth.current_user_id()
    payload = request.get_json(silent=True) or {}

    def _f(key, default):
        try:
            return float(payload.get(key, default))
        except (TypeError, ValueError):
            return default

    starting = _f("starting_bankroll", 0.0)
    if starting <= 0:
        return jsonify({"error": "starting bankroll must be > 0"}), 400
    pause_at = _f("pause_at", starting * 0.5)
    end_at = _f("end_at", 0.0)
    stake_1u = _f("stake_1u", 5.0)
    stake_2u = _f("stake_2u", 10.0)
    display_name = (payload.get("display_name") or "").strip() or uid
    pushover_key = (payload.get("pushover_user_key") or "").strip()
    acknowledged = bool(payload.get("rules_acknowledged"))

    # Bankroll: only init if it doesn't exist yet. Returning 409 lets the
    # UI catch the "already set up" case rather than silently overwriting.
    if auth.bankroll_path(uid).exists():
        return jsonify({"error": "bankroll already initialized"}), 409
    bankroll.init(uid, starting=starting, pause_at=pause_at, end_at=end_at)

    auth.save_prefs(uid, {
        "display_name": display_name,
        "stake_1u": stake_1u,
        "stake_2u": stake_2u,
        "pushover_user_key": pushover_key,
        "rules_acknowledged_at": (
            auth._now_iso() if acknowledged else None
        ),
    })
    return jsonify({"ok": True})


@app.get("/api/bets")
@auth.login_required
def api_list_bets():
    uid = auth.current_user_id()
    state = wagers.load_bets(uid)
    return jsonify({"bets": state["bets"], "totals": wagers.totals(uid, state)})


@app.get("/api/bet-record")
@auth.login_required
def api_bet_record():
    """Betting-record + edge-band tuning report for the logged-in user.
    Backs the UD Lab 'My record' panel. Per-user (own ledger only)."""
    uid = auth.current_user_id()
    return jsonify(bet_record.compute(uid))


@app.post("/api/bets")
@auth.login_required
def api_add_bet():
    uid = auth.current_user_id()
    payload = request.get_json(silent=True) or {}
    bet = wagers.add_bet(uid, payload)
    return jsonify({"bet": bet, "totals": wagers.totals(uid)})


def _settle_bet_with_notify(user_id: str, bet_id: str, payload: dict) -> dict | None:
    """Update a bet and fire the bet-settled Pushover on pending → W/L.
    Shared by PUT /api/bets and the background alerts loop so both paths
    hit the same notification flow."""
    prior = next(
        (b for b in wagers.load_bets(user_id)["bets"] if b.get("id") == bet_id),
        None,
    )
    prior_result = prior.get("result") if prior else None
    updated = wagers.update_bet(user_id, bet_id, payload)
    if updated is None:
        return None
    # Bankroll ledger: replay the settlement event for this bet (idempotent
    # on bet_id, so re-settles / W↔L flips / unsettles all stay consistent).
    # Run on every update — record_settlement removes the prior settle event
    # if the bet is no longer in W/L state.
    try:
        bankroll.record_settlement(user_id, updated)
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("bankroll settle for %s failed: %s", bet_id, exc)
    if prior_result is None and updated.get("result") in ("W", "L"):
        formatted = notify.format_bet_settle(updated)
        if formatted:
            title, body = formatted
            notify.send_pushover(title, body, user_id=user_id)
    return updated


@app.put("/api/bets/<bet_id>")
@auth.login_required
def api_update_bet(bet_id: str):
    uid = auth.current_user_id()
    payload = request.get_json(silent=True) or {}
    # Defense in depth against the bets-tab auto-settle bug fixed in
    # 04a3b9f: refuse to flip an already-settled bet between W and L
    # when the bet's date is not today. An open browser tab still
    # running pre-fix JS would otherwise re-grade old bets against
    # today's K count for the same pitcher_id.
    prior = next(
        (b for b in wagers.load_bets(uid)["bets"] if b.get("id") == bet_id),
        None,
    )
    if prior is not None and "result" in payload:
        prior_result = prior.get("result")
        new_result = payload.get("result")
        bet_date = prior.get("date") or ""
        today_iso = _today().isoformat()
        if (
            bet_date != today_iso
            and prior_result in ("W", "L")
            and new_result in ("W", "L")
            and new_result != prior_result
        ):
            return (
                jsonify(
                    {
                        "error": "settled-bet flip refused for non-today bet",
                        "bet_date": bet_date,
                        "today": today_iso,
                        "prior_result": prior_result,
                        "new_result": new_result,
                    }
                ),
                409,
            )
    updated = _settle_bet_with_notify(uid, bet_id, payload)
    if updated is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"bet": updated, "totals": wagers.totals(uid)})


@app.delete("/api/bets/<bet_id>")
@auth.login_required
def api_delete_bet(bet_id: str):
    uid = auth.current_user_id()
    if not wagers.delete_bet(uid, bet_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "totals": wagers.totals(uid)})


@app.get("/api/bankroll")
@auth.login_required
def api_bankroll():
    """Bankroll snapshot for the dashboard pill. Pending exposure is
    reported separately so the UI can show free cash vs deployed cash."""
    uid = auth.current_user_id()
    # If the user hasn't completed setup yet, there's no bankroll file
    # and load() would raise. Return a setup-needed marker so the JS can
    # show the wizard without a crashing fetch in the console.
    if not auth.bankroll_path(uid).exists():
        return jsonify({"setup_required": True}), 409
    bets = wagers.load_bets(uid)["bets"]
    pending_stake = sum(
        float(b.get("stake") or 0)
        for b in bets
        if not b.get("result") and not b.get("free_entry")
    )
    return jsonify(bankroll.snapshot(uid, pending_stake))


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


def _ud_target() -> date | tuple:
    """Resolve ?date= for the UD-lines endpoints. Returns a date, or a
    (body, status) error tuple the caller short-circuits on."""
    target_str = request.args.get("date", "").strip()
    if not target_str:
        return _today()
    try:
        return datetime.strptime(target_str, "%Y-%m-%d").date()
    except ValueError:
        return (jsonify({"error": f"bad date: {target_str}"}), 400)


# Field bounds for UD pricing inputs. line = strikeout number; hi/lo =
# Underdog Higher/Lower payout multipliers (e.g. 1.04 / 0.87).
_UD_BOUNDS = {"line": (0.0, 20.0), "hi": (0.1, 50.0), "lo": (0.1, 50.0)}


def _clean_ud_fields(payload: dict):
    """Pull {line, hi, lo} from a payload, validating bounds. Only keys
    present are returned (PATCH semantics). Returns (fields, error_or_None)."""
    fields: dict = {}
    for k, (lo, hi) in _UD_BOUNDS.items():
        if k not in payload:
            continue
        raw = payload.get(k)
        if raw in (None, ""):
            fields[k] = None  # explicit clear
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None, f"bad {k}: {raw}"
        if v < lo or v > hi:
            return None, f"{k} out of range"
        fields[k] = v
    return fields, None


@app.get("/api/ud-lines")
def api_ud_lines():
    """Return manually-entered Underdog pricing for the slate, keyed by
    pitcher_id (string): {pid: {line, hi, lo}}. Slate-level + ungated,
    matching /api/slate-pitchers — UD's board is the same for everyone."""
    target = _ud_target()
    if isinstance(target, tuple):
        return target
    return jsonify({"date": target.isoformat(), "board": ud_lines.load(target)})


@app.post("/api/ud-line")
def api_ud_line():
    """Upsert one pitcher's UD pricing. Body: {pitcher_id, line?, hi?, lo?}.
    Only fields present are applied; a null/empty value clears that field.
    Returns the full updated board."""
    target = _ud_target()
    if isinstance(target, tuple):
        return target
    payload = request.get_json(silent=True) or {}
    pid = payload.get("pitcher_id")
    if pid in (None, ""):
        return jsonify({"error": "pitcher_id required"}), 400
    fields, err = _clean_ud_fields(payload)
    if err:
        return jsonify({"error": err}), 400
    try:
        board = ud_lines.save_one(target, pid, fields)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"date": target.isoformat(), "board": board})


@app.post("/api/ud-lines")
def api_ud_lines_bulk():
    """Bulk upsert. Body: {board: {pid: {line, hi, lo}}}. Powers the UD Lab
    'Save all' button so prefilled (sportsbook-matching) lines get recorded
    too. Returns the full updated board."""
    target = _ud_target()
    if isinstance(target, tuple):
        return target
    payload = request.get_json(silent=True) or {}
    incoming = payload.get("board")
    if not isinstance(incoming, dict):
        return jsonify({"error": "board object required"}), 400
    board = ud_lines.save_many(target, incoming)
    return jsonify({"date": target.isoformat(), "board": board})


@app.post("/api/ud-screenshot")
def api_ud_screenshot():
    """Extract UD lines + Higher/Lower multipliers from an uploaded UD board
    screenshot via Claude vision, matched to the slate's pitcher_ids. Body:
    {image: <data-URL or base64 png/jpeg>}. Returns {matched, unmatched} for
    the UD Lab to drop into a review state. Does NOT save — the user confirms
    then hits Save all."""
    target = _ud_target()
    if isinstance(target, tuple):
        return target
    payload = request.get_json(silent=True) or {}
    img = payload.get("image") or ""
    if not img:
        return jsonify({"error": "image required"}), 400
    media_type = "image/png"
    if img.startswith("data:"):
        head, _, b64 = img.partition(",")
        m = re.match(r"data:([^;]+)", head)
        if m:
            media_type = m.group(1)
        img = b64
    try:
        raw = base64.b64decode(img)
    except Exception:  # noqa: BLE001
        return jsonify({"error": "bad image data"}), 400
    try:
        result = ud_vision.extract(raw, media_type, target)
    except RuntimeError as e:  # missing API key
        return jsonify({"error": str(e)}), 503
    except Exception as e:  # noqa: BLE001 — API/network/parse failure
        return jsonify({"error": f"extraction failed: {e}"}), 502
    return jsonify({"date": target.isoformat(), **result})


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
    # the dashboard's existing 60s poll, but only for the logged-in
    # user — otherwise we'd fire alerts when an anonymous tab on the
    # public Pitcher tab polls. The 60s daemon (_alerts_tick) covers
    # alerts for users who never visit the Bets tab.
    uid = auth.current_user_id()
    if uid:
        try:
            notify.check_live_alerts(uid, results, target.isoformat())
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

    # Iterate every user's pending bets — each gets their own alerts and
    # auto-settles into their own bankroll. A pitcher who appears in two
    # users' bets fires one Pushover per user (dedup keys are
    # user-scoped in notify.py). Pitcher-id set is unioned across users
    # so we make one MLB API call regardless of overlap.
    by_user_pending: dict[str, list[dict]] = {}
    for uid in auth.all_user_ids():
        try:
            user_state = wagers.load_bets(uid)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("alerts tick: load_bets(%s) failed: %s", uid, exc)
            continue
        by_user_pending[uid] = [b for b in user_state["bets"] if not b.get("result")]

    # Scratch check: per user, needs at least one pending bet today, and
    # the whole tick is rate-limited to 5 min so we don't pummel the MLB
    # Stats API every 60s. Scratches usually announce 1-3 hours pre-game
    # so the cooldown is plenty of resolution.
    todays_any = any(
        any((b.get("date") or "") == target_iso for b in pending)
        for pending in by_user_pending.values()
    )
    if todays_any and time.time() - _last_scratch_check_at >= 300:
        _last_scratch_check_at = time.time()
        for uid, pending in by_user_pending.items():
            if not any((b.get("date") or "") == target_iso for b in pending):
                continue
            try:
                notify.check_scratch_alerts(uid, target_iso)
            except Exception as exc:  # noqa: BLE001
                app.logger.warning(
                    "alerts tick: check_scratch_alerts(%s) failed: %s", uid, exc
                )

    pids: set[int] = set()
    for pending in by_user_pending.values():
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

    for uid, pending in by_user_pending.items():
        try:
            notify.check_live_alerts(uid, results, target_iso)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning(
                "alerts tick: check_live_alerts(%s) failed: %s", uid, exc
            )
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
                _settle_bet_with_notify(
                    uid, bet["id"], {"result": verdict, "payout": payout}
                )
            except Exception as exc:  # noqa: BLE001
                app.logger.warning(
                    "alerts tick: auto-settle for %s/%s failed: %s",
                    uid, bet.get("id"), exc
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
