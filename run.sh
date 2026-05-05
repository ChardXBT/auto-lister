#!/bin/bash
cd /root/csfloat-bot

exec 200>/tmp/csfloat_bot.lock
flock -n 200 || { echo "Bot already running, skipping."; exit 1; }

git pull origin main

python3 bot.py

git add config.json bot_state.json CS_GO_PnL_Tracker_Final.xlsx
git diff --cached --quiet || git commit -m "auto: remove sold items" && git push origin HEAD:main
