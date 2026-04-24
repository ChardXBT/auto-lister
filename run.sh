#!/bin/bash
cd /root/csfloat-bot

# Prevent duplicate runs
LOCKFILE="/tmp/csfloat_bot.lock"
if [ -f "$LOCKFILE" ]; then
    echo "Bot already running, skipping."
    exit 1
fi
touch "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

# Pull latest code and config from GitHub
git fetch origin && git reset --hard origin/main

# Run the bot
python3 bot.py

# Push any config changes (e.g. sold items removed) back to GitHub
git add config.json bot_state.json CS_GO_PnL_Tracker_Final.xlsx
git diff --cached --quiet || git commit -m "auto: remove sold items" && git push origin master:main
