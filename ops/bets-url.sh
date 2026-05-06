#!/bin/zsh
# Print the current public quick-tunnel URL by scanning cloudflared's logs.
# The URL changes every time cloudflared restarts, so we want the most-recent
# one in the logs.
LOGS=(~/Library/Logs/bets-cloudflared.err ~/Library/Logs/bets-cloudflared.out)
URL=$(grep -hoE 'https://[a-z0-9-]+\.trycloudflare\.com' "${LOGS[@]}" 2>/dev/null | tail -1)
if [ -z "$URL" ]; then
    echo "No tunnel URL found in logs. Is cloudflared running?" >&2
    exit 1
fi
echo "$URL"
