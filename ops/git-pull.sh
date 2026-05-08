#!/bin/zsh
# Run by launchd every 60s on Air (picks up dashboard builds) and on laptop
# (picks up Air's morning refresh so manual re-runs hit the all_covered
# guard with 0 Odds API credits).
# --ff-only refuses to merge — if there's ever a conflict we want it loud.
cd /Users/chadhirsch/bets || exit 1
# Resolve git per host: Air = /opt/homebrew/bin/git, laptop = /usr/bin/git.
GIT="$(command -v git || echo /usr/bin/git)"
"$GIT" pull --ff-only --quiet
