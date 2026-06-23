"""Snapshot and settle the daily parlay-suggester output.

Mirrors bets/web.py's renderParlaySuggestions JS logic so we capture
exactly what the dashboard would have shown at slate time, then grade
each card against actual K outcomes once the day settles.

Run with:
    python -m bets.parlay_suggest                  # snapshot + settle today
    python -m bets.parlay_suggest 2026-05-10       # specific date
    python -m bets.parlay_suggest --backfill       # every slate file with a
                                                    # matching settled pitcher CSV
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

from .config import OUTPUT_DIR
from .live import FOCUS_EDGE_MAX, FOCUS_EDGE_MIN, INVESTIGATE_EDGE, MIN_LINE_FOR_FOCUS

# Suggester knobs. Mirror the JS constants in web.py's
# renderParlaySuggestions — update both sides if you tune one.
PARLAY_INPUT_CAP = 8       # cap focus pool before exploding combos
TOP_TWO = 5                # top 2-leg cards per day
TOP_THREE = 3              # top 3-leg cards per day
MAX_APPEARANCES = 1        # any one pitcher in at most this many cards
                            # per section
MAX_GAP_SECONDS = 3 * 3600  # skip combos whose game times span more than this
                            # — by the time the first game starts, the later
                            # game's lineup still won't be posted

# Underdog flat power-play payouts and Chad's recurring 30% promo boost.
# Mirror of web.py's UD_PAYOUTS / UD_BOOST (2026-06-09): cards are ranked
# and filtered on EV at these payouts, NOT the sportsbook decimal product —
# UD is the only book in play and its payout is flat per leg count.
UD_PAYOUTS = {2: 3.0, 3: 6.0}
UD_BOOST = 1.3


def _safe_float(v) -> float | None:
    if v in ("", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    if v in ("", None):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _pick_edge(r: dict) -> float | None:
    """Mirror of the JS pickEdge() helper: prefer v2bc calibrated edge
    (primary since 2026-06-22), fall back to v2 cal edge, then raw edge
    for pre-calibration rows."""
    bc = _safe_float(r.get("cal_edge_v2bc"))
    if bc is not None:
        return bc
    cal = _safe_float(r.get("cal_edge_v2"))
    if cal is not None:
        return cal
    return _safe_float(r.get("edge"))


def _is_focus(edge: float | None) -> bool:
    if edge is None:
        return False
    a = abs(edge)
    return FOCUS_EDGE_MIN <= a <= FOCUS_EDGE_MAX


# Shadow floor stays at the ORIGINAL 0.065 even though the production floor
# rose to 0.10 (2026-06-06) — the shadow track record is how we keep grading
# the bands production no longer bets (0.065–0.10 low band + 0.15–0.20 gap),
# so the floor decision stays revisitable on data instead of memory.
SHADOW_EDGE_MIN = 0.065


def _is_focus_or_gap(edge: float | None) -> bool:
    # Shadow band: focus PLUS the bands the production suggester filters out
    # (0.065–0.10 low band since 2026-06-06, 0.15–0.20 "noise gap" since
    # inception). The 2026-05-11 dead-band audit found the gap had +45%
    # single-leg ROI (n=32) over 11 settled days — strong enough to track
    # but not yet promoted to production selection.
    if edge is None:
        return False
    a = abs(edge)
    return SHADOW_EDGE_MIN <= a < INVESTIGATE_EDGE


def _american_to_decimal(odds: float | None) -> float | None:
    if odds is None or odds == 0:
        return None
    if odds > 0:
        return odds / 100 + 1
    return 100 / abs(odds) + 1


def _decimal_to_american(dec: float | None) -> int | None:
    if dec is None or dec <= 1:
        return None
    if dec >= 2:
        return round((dec - 1) * 100)
    return round(-100 / (dec - 1))


def _parse_game_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pick_leg_from_row(r: dict) -> dict | None:
    """Mirror pickLegFromRow: turn a slate row into a normalized leg, or
    None if it can't be priced (no odds on the picked side, no novig)."""
    edge = _pick_edge(r)
    if edge is None:
        return None
    direction = "over" if edge > 0 else "under"
    odds = (
        _safe_float(r.get("over_odds")) if direction == "over"
        else _safe_float(r.get("under_odds"))
    )
    dec = _american_to_decimal(odds)
    if dec is None:
        return None
    p_over = _safe_float(r.get("p_over"))
    novig_over = _safe_float(r.get("novig_over"))
    if p_over is None or novig_over is None:
        return None
    # Calibrated P(over): prefer v2bc (primary since 2026-06-22), fall back
    # to v2, then raw Poisson for pre-calibration rows. At UD's flat payout
    # the hit probability IS the ranking, so model honesty is everything.
    cal_p_over = _safe_float(r.get("cal_p_over_v2bc")) or _safe_float(r.get("cal_p_over_v2"))
    p_src = cal_p_over if cal_p_over is not None else p_over
    hit_prob = p_src if direction == "over" else 1 - p_src
    novig_p = novig_over if direction == "over" else 1 - novig_over
    return {
        "pitcher": r.get("pitcher", "") or "",
        "pitcher_id": _safe_int(r.get("pitcher_id")),
        "game_pk": _safe_int(r.get("game_pk")),
        "game_ts": _parse_game_ts(r.get("game_datetime_utc")),
        "line": r.get("line", ""),
        "dir": direction,
        "odds": odds,
        "decOdds": dec,
        "hitProb": hit_prob,
        "novigP": novig_p,
        "edge": edge,
    }


def _evaluate_parlay(legs: list[dict]) -> dict:
    dec = 1.0
    hit = 1.0
    novig = 1.0
    for l in legs:
        dec *= l["decOdds"]
        hit *= l["hitProb"]
        novig *= l["novigP"]
    # evUD/evUDBoost are the money numbers (UD's flat payout, with/without
    # the 30% promo); book-odds ev is kept for reference only. Mirrors
    # web.py evaluateParlay (2026-06-09).
    ud_mult = UD_PAYOUTS.get(len(legs))
    return {
        "legs": legs,
        "combinedAmer": _decimal_to_american(dec),
        "combinedDec": dec,
        "combinedHit": hit,
        "combinedNovig": novig,
        "combinedEdge": hit - novig,
        "ev": hit * (dec - 1) - (1 - hit),
        "udPayout": ud_mult,
        "evUD": None if ud_mult is None else hit * ud_mult - 1,
        "evUDBoost": None if ud_mult is None else hit * ud_mult * UD_BOOST - 1,
    }


def _unique_games(combo: list[dict]) -> bool:
    seen = set()
    for l in combo:
        gpk = l["game_pk"]
        if gpk is None:
            continue
        if gpk in seen:
            return False
        seen.add(gpk)
    return True


def _game_times_within_window(combo: list[dict]) -> bool:
    times = [l["game_ts"] for l in combo if l.get("game_ts") is not None]
    if len(times) < 2:
        return True
    return max(times) - min(times) <= MAX_GAP_SECONDS


def _select_diverse(sorted_evals: list[dict], top: int, max_per: int) -> list[dict]:
    """Greedy: walk EV-sorted parlays, skip any whose pitchers already
    hit the appearance cap. Stops at top selections or when exhausted."""
    counts: dict[int, int] = {}
    out: list[dict] = []
    for p in sorted_evals:
        if len(out) >= top:
            break
        ok = True
        for l in p["legs"]:
            pid = l["pitcher_id"]
            if pid is not None and counts.get(pid, 0) >= max_per:
                ok = False
                break
        if not ok:
            continue
        for l in p["legs"]:
            pid = l["pitcher_id"]
            if pid is not None:
                counts[pid] = counts.get(pid, 0) + 1
        out.append(p)
    return out


def _build_section(
    legs: list[dict],
    k: int,
    top: int,
    exclude_pids: set[int] | None = None,
) -> list[dict]:
    if len(legs) < k:
        return []
    parlays: list[dict] = []
    for combo in combinations(legs, k):
        combo_list = list(combo)
        if not _unique_games(combo_list):
            continue
        if not _game_times_within_window(combo_list):
            continue
        if exclude_pids:
            combo_pids = {l["pitcher_id"] for l in combo_list if l["pitcher_id"] is not None}
            if combo_pids & exclude_pids:
                continue
        ev = _evaluate_parlay(combo_list)
        # Keep any card playable with the 30% boost; rank by UD-payout EV
        # (= win probability at a flat payout). Mirrors web.py (2026-06-09).
        if ev["evUDBoost"] is None or ev["evUDBoost"] <= 0:
            continue
        parlays.append(ev)
    parlays.sort(key=lambda p: p["evUD"], reverse=True)
    return _select_diverse(parlays, top, MAX_APPEARANCES)


def suggest_parlays(
    slate_rows: list[dict],
    is_eligible=_is_focus,
) -> dict[str, list[dict]]:
    """Generate the same top-N two-leg + three-leg cards the dashboard
    suggester would render from this slate, in EV-ranked order.

    `is_eligible(edge)` controls the band: defaults to the production focus
    band; pass `_is_focus_or_gap` for the shadow expanded band.
    """
    # Filter by edge band AND by the role-mismatch line gate. See web.py
    # MIN_LINE_FOR_FOCUS comment — books pricing a starter < 3.0 K signal
    # a reliever/opener role our model isn't equipped to project.
    def _eligible(r: dict) -> bool:
        if not is_eligible(_pick_edge(r)):
            return False
        line = _safe_float(r.get("line"))
        return line is not None and line >= MIN_LINE_FOR_FOCUS
    eligible_rows = [r for r in slate_rows if _eligible(r)]
    if len(eligible_rows) < 2:
        return {"two_leg": [], "three_leg": []}
    legs = [_pick_leg_from_row(r) for r in eligible_rows]
    legs = [l for l in legs if l is not None]
    # Seed the combo pool with the most LIKELY legs, not the biggest
    # model-market disagreements — sorting by |edge| pre-loaded the pool
    # with phantom-edge legs. Mirrors web.py (2026-06-09).
    legs.sort(key=lambda l: l["hitProb"], reverse=True)
    legs = legs[:PARLAY_INPUT_CAP]
    if len(legs) < 2:
        return {"two_leg": [], "three_leg": []}
    two_leg = _build_section(legs, 2, TOP_TWO)
    # Disjoint rule dropped 2026-05-16 for visibility (Chad wanted to see
    # all possible 3-legs, not just disjoint ones). The 2026-05-15 audit's
    # finding (-78% ROI on overlapping 3-legs vs +22% on disjoint, small n)
    # is preserved by tagging each card with `overlaps_top_2leg` for both
    # the dashboard UI and the snapshot CSV — so we can re-test the audit
    # under the corrected v2 model with a larger sample.
    three_leg = _build_section(legs, 3, TOP_THREE)
    top2_pids: set[int] = set()
    top2_by_pid: dict[int, str] = {}
    if two_leg:
        for l in two_leg[0]["legs"]:
            if l["pitcher_id"] is not None:
                top2_pids.add(l["pitcher_id"])
                top2_by_pid[l["pitcher_id"]] = l["pitcher"]
    for card in three_leg:
        shared = next(
            (l for l in card["legs"]
             if l["pitcher_id"] is not None and l["pitcher_id"] in top2_pids),
            None,
        )
        card["overlaps_top_2leg"] = (
            top2_by_pid.get(shared["pitcher_id"], shared["pitcher"])
            if shared else ""
        )
    return {"two_leg": two_leg, "three_leg": three_leg}


# ---------- CSV serialization ----------

LEG_FIELDS = ("pitcher", "pitcher_id", "dir", "line", "odds", "p_hit", "novig_p")


def _suggestion_fieldnames() -> list[str]:
    base = ["date", "section", "rank", "leg_count"]
    for i in (1, 2, 3):
        base.extend([f"leg{i}_{f}" for f in LEG_FIELDS])
    base.extend([
        "combined_amer", "combined_dec", "combined_hit",
        "combined_novig", "combined_edge", "ev",
        # UD-payout economics (2026-06-09): the flat power-play multiplier,
        # EV at that payout, and EV with the 30% promo boost. These are the
        # selection/ranking columns; `ev` (book odds) is legacy reference.
        # Empty in snapshots written before 2026-06-09.
        "ud_payout", "ev_ud", "ev_ud_boost",
        # Empty for two_leg cards; for three_leg, the shared pitcher name
        # if this 3-leg shares a pitcher with the top 2-leg card, else "".
        # Lets the eventual audit compare overlapping vs disjoint 3-leg ROI
        # at larger n now that the disjoint rule has been dropped.
        "overlaps_top_2leg",
    ])
    return base


def _suggestion_to_row(target_iso: str, section: str, rank: int, p: dict) -> dict:
    row = {
        "date": target_iso,
        "section": section,
        "rank": rank,
        "leg_count": len(p["legs"]),
        "combined_amer": p["combinedAmer"] if p["combinedAmer"] is not None else "",
        "combined_dec": round(p["combinedDec"], 6),
        "combined_hit": round(p["combinedHit"], 6),
        "combined_novig": round(p["combinedNovig"], 6),
        "combined_edge": round(p["combinedEdge"], 6),
        "ev": round(p["ev"], 6),
        "ud_payout": p["udPayout"] if p.get("udPayout") is not None else "",
        "ev_ud": round(p["evUD"], 6) if p.get("evUD") is not None else "",
        "ev_ud_boost": round(p["evUDBoost"], 6) if p.get("evUDBoost") is not None else "",
        "overlaps_top_2leg": p.get("overlaps_top_2leg", ""),
    }
    for i in (1, 2, 3):
        idx = i - 1
        if idx < len(p["legs"]):
            l = p["legs"][idx]
            row[f"leg{i}_pitcher"] = l["pitcher"]
            row[f"leg{i}_pitcher_id"] = l["pitcher_id"] if l["pitcher_id"] is not None else ""
            row[f"leg{i}_dir"] = l["dir"]
            row[f"leg{i}_line"] = l["line"]
            row[f"leg{i}_odds"] = l["odds"] if l["odds"] is not None else ""
            row[f"leg{i}_p_hit"] = round(l["hitProb"], 6)
            row[f"leg{i}_novig_p"] = round(l["novigP"], 6)
        else:
            for f in LEG_FIELDS:
                row[f"leg{i}_{f}"] = ""
    return row


def _slate_path(target_date: date) -> Path:
    return OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}_slate.csv"


def _suggestions_path(target_date: date) -> Path:
    return OUTPUT_DIR / f"parlay_suggestions_{target_date.isoformat()}.csv"


def _shadow_suggestions_path(target_date: date) -> Path:
    return OUTPUT_DIR / f"parlay_suggestions_{target_date.isoformat()}_shadow.csv"


def _settled_pitcher_path(target_date: date) -> Path:
    return OUTPUT_DIR / f"pitcher_ks_{target_date.isoformat()}_settled.csv"


def _settled_suggestions_path(target_date: date) -> Path:
    return OUTPUT_DIR / f"parlay_suggestions_{target_date.isoformat()}_settled.csv"


def _settled_shadow_suggestions_path(target_date: date) -> Path:
    return OUTPUT_DIR / f"parlay_suggestions_{target_date.isoformat()}_shadow_settled.csv"


def _read_slate(target_date: date) -> list[dict] | None:
    path = _slate_path(target_date)
    if not path.exists():
        return None
    with path.open() as f:
        return list(csv.DictReader(f))


def _write_suggestions_impl(
    target_date: date,
    out_path: Path,
    is_eligible,
    force: bool,
) -> Path | None:
    if out_path.exists() and not force:
        return out_path
    slate_rows = _read_slate(target_date)
    if slate_rows is None:
        return None
    sug = suggest_parlays(slate_rows, is_eligible=is_eligible)
    rows = []
    for rank, p in enumerate(sug["two_leg"], start=1):
        rows.append(_suggestion_to_row(target_date.isoformat(), "two_leg", rank, p))
    for rank, p in enumerate(sug["three_leg"], start=1):
        rows.append(_suggestion_to_row(target_date.isoformat(), "three_leg", rank, p))
    fieldnames = _suggestion_fieldnames()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} parlay suggestions → {out_path}")
    return out_path


def write_suggestions(target_date: date, force: bool = False) -> Path | None:
    """Snapshot today's parlay-suggester output to
    parlay_suggestions_<date>.csv. Skips silently if the file already
    exists (slate is frozen → re-runs would produce the same output)
    unless force=True."""
    return _write_suggestions_impl(
        target_date, _suggestions_path(target_date), _is_focus, force
    )


def write_shadow_suggestions(target_date: date, force: bool = False) -> Path | None:
    """Shadow snapshot using the expanded focus+gap band (0.05 <= |edge| <
    0.20). Mirrors write_suggestions but writes to
    parlay_suggestions_<date>_shadow.csv so the production file is
    untouched."""
    return _write_suggestions_impl(
        target_date, _shadow_suggestions_path(target_date), _is_focus_or_gap, force
    )


def _settle_suggestions_impl(
    sug_path: Path,
    out_path: Path,
    settled_pitcher_path: Path,
    force: bool,
) -> Path | None:
    if not sug_path.exists() or not settled_pitcher_path.exists():
        return None
    if out_path.exists() and not force:
        out_mtime = out_path.stat().st_mtime
        if (sug_path.stat().st_mtime <= out_mtime
                and settled_pitcher_path.stat().st_mtime <= out_mtime):
            return out_path

    settled_by_pid: dict[int, dict] = {}
    with settled_pitcher_path.open() as f:
        for row in csv.DictReader(f):
            pid = _safe_int(row.get("pitcher_id"))
            if pid is None:
                continue
            settled_by_pid[pid] = row

    with sug_path.open() as f:
        reader = csv.DictReader(f)
        snapshot_fields = list(reader.fieldnames or [])
        sug_rows = list(reader)

    extra_fields: list[str] = []
    for i in (1, 2, 3):
        extra_fields.extend([f"leg{i}_actual_ks", f"leg{i}_hit"])
    extra_fields.extend(["parlay_hit", "realized_pnl"])
    # Base the output columns on the snapshot's *own* header rather than the
    # current _suggestion_fieldnames(). `out = dict(s)` preserves every source
    # column, so if the snapshot ever carries a column this process doesn't
    # know about (e.g. a stale long-running Flask after a schema change, or a
    # newer snapshot format), DictWriter would otherwise raise "fields not in
    # fieldnames". Falling back to the canonical list keeps old snapshots that
    # predate this change working.
    base_fields = snapshot_fields or _suggestion_fieldnames()
    fieldnames = base_fields + [f for f in extra_fields if f not in base_fields]

    out_rows: list[dict] = []
    for s in sug_rows:
        out = dict(s)
        leg_count = _safe_int(s.get("leg_count")) or 0
        any_unsettled = False
        any_miss = False
        for i in (1, 2, 3):
            out[f"leg{i}_actual_ks"] = ""
            out[f"leg{i}_hit"] = ""
            if i > leg_count:
                continue
            pid = _safe_int(s.get(f"leg{i}_pitcher_id"))
            if pid is None:
                any_unsettled = True
                continue
            settled = settled_by_pid.get(pid)
            if settled is None:
                any_unsettled = True
                continue
            actual_ks = _safe_float(settled.get("actual_ks"))
            line = _safe_float(s.get(f"leg{i}_line"))
            if actual_ks is None or line is None:
                any_unsettled = True
                continue
            out[f"leg{i}_actual_ks"] = int(actual_ks)
            direction = s.get(f"leg{i}_dir") or ""
            over_hit = actual_ks > line
            leg_hit = (
                (direction == "over" and over_hit)
                or (direction == "under" and not over_hit)
            )
            out[f"leg{i}_hit"] = 1 if leg_hit else 0
            if not leg_hit:
                any_miss = True

        if any_miss:
            # Any losing leg sinks the parlay regardless of un-graded
            # legs (a missed leg already loses the wager).
            out["parlay_hit"] = 0
            out["realized_pnl"] = -1.0
        elif any_unsettled:
            # No miss yet but at least one leg without an outcome → leave
            # the card as no-action so it doesn't pollute hit-rate stats.
            out["parlay_hit"] = ""
            out["realized_pnl"] = ""
        else:
            out["parlay_hit"] = 1
            dec = _safe_float(s.get("combined_dec"))
            out["realized_pnl"] = round(dec - 1, 6) if dec is not None else ""
        out_rows.append(out)

    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Settled {len(out_rows)} parlay suggestions → {out_path}")
    return out_path


def settle_suggestions(target_date: date, force: bool = False) -> Path | None:
    """Grade each suggested parlay against the day's actual K outcomes.

    Reads parlay_suggestions_<date>.csv + pitcher_ks_<date>_settled.csv,
    writes parlay_suggestions_<date>_settled.csv. Each leg is graded
    against the slate-time line stored in the suggestion row (matches
    the slate_over_hit convention used elsewhere). A leg whose pitcher
    was scratched (no actual_ks) leaves the parlay un-graded.

    Skips silently if no suggestions snapshot exists. Re-runs if
    force=True or if either upstream file is newer than the settled file.
    """
    return _settle_suggestions_impl(
        _suggestions_path(target_date),
        _settled_suggestions_path(target_date),
        _settled_pitcher_path(target_date),
        force,
    )


def settle_shadow_suggestions(target_date: date, force: bool = False) -> Path | None:
    """Settle the shadow snapshot (focus+gap band). Mirrors
    settle_suggestions but reads/writes the *_shadow*.csv pair."""
    return _settle_suggestions_impl(
        _shadow_suggestions_path(target_date),
        _settled_shadow_suggestions_path(target_date),
        _settled_pitcher_path(target_date),
        force,
    )


# ---------- CLI ----------


def _backfill() -> None:
    """Replay against every slate snapshot in OUTPUT_DIR. Settles whatever
    has a matching pitcher_ks_*_settled.csv. Runs both production and
    shadow tracks."""
    slate_files = sorted(OUTPUT_DIR.glob("pitcher_ks_*_slate.csv"))
    today = date.today()
    for path in slate_files:
        # parse date from filename: pitcher_ks_YYYY-MM-DD_slate.csv
        try:
            date_part = path.stem.split("_")[2]
            target = datetime.strptime(date_part, "%Y-%m-%d").date()
        except (IndexError, ValueError):
            continue
        if target > today:
            continue
        write_suggestions(target)
        write_shadow_suggestions(target)
        settle_suggestions(target)
        settle_shadow_suggestions(target)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--backfill":
        _backfill()
        return
    if args:
        target = datetime.strptime(args[0], "%Y-%m-%d").date()
    else:
        target = date.today()
    write_suggestions(target)
    write_shadow_suggestions(target)
    settle_suggestions(target)
    settle_shadow_suggestions(target)


if __name__ == "__main__":
    main()
