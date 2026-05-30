## Build/Deploy

- `bets/web.py` is the source of truth for the dashboard. Edit it (never `output/index.html`) and regenerate after each change.
- `bets/web.py` (JS suggester) and `bets/parlay_suggest.py` (Python snapshot) implement the same parlay logic. Change both when tuning either, or the snapshot CSV will silently diverge from the live dashboard.

## Git Workflow

- Ship = commit AND push in the same step unless explicitly told otherwise. State both in the summary (e.g., 'Committed as <sha> and pushed to origin/main').

## Docs

- **UD Lab** (Underdog-aware analysis tab + betting-record/bet-bar tooling): see `UD_LAB.md` — read it before touching the UD Lab, `ud_lines.py`, `ud_vision.py`, `bet_record.py`, the `/api/ud-*` or `/api/bet-record` endpoints, or the `grade_ud.py` / `grade_my_bets.py` / `capture_ud_close.py` CLIs.
- Editing `server.py`, `ud_lines.py`, `bet_record.py`, or `ud_vision.py` requires a Flask restart on the Air (`launchctl kickstart -k gui/$(id -u)/com.bets.flask`); UI changes in `web.py` require regenerating `index.html` on the Air.
