## Build/Deploy

- `bets/web.py` is the source of truth for the dashboard. Edit it (never `output/index.html`) and regenerate after each change.
- `bets/web.py` (JS suggester) and `bets/parlay_suggest.py` (Python snapshot) implement the same parlay logic. Change both when tuning either, or the snapshot CSV will silently diverge from the live dashboard.

## Git Workflow

- Ship = commit AND push in the same step unless explicitly told otherwise. State both in the summary (e.g., 'Committed as <sha> and pushed to origin/main').
