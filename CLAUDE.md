## Build/Deploy Gotchas

- After editing source that produces `output/index.html` or other generated dashboards, ALWAYS regenerate the HTML before claiming the change is visible locally or on Air.
- When pushing to Air, check for dirty generated files (e.g., `output/index.html`) that can block `git pull` — commit or stash them first.
- If a Flask-served change doesn't appear after deploy, suspect a stale server process and restart it before deeper debugging.

## Git Workflow

- When a task is shipped, ALWAYS commit AND push in the same step unless explicitly told otherwise. State both clearly in the summary (e.g., 'Committed as <sha> and pushed to origin/main').
