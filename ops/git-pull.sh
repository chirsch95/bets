#!/bin/zsh
# Run by launchd every 60s on the M1 Air to pick up new dashboard builds.
# --ff-only refuses to merge — if there's ever a conflict we want it loud.
cd /Users/chadhirsch/bets || exit 1
/opt/homebrew/bin/git pull --ff-only --quiet
