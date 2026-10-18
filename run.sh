#!/bin/bash
cd "$(dirname "$0")"

exec 200>/tmp/csfloat_bot.lock
flock -n 200 || { echo "Bot already running, skipping."; exit 1; }

git pull --rebase origin main

python3 bot.py

git add -A data/
git diff --cached --quiet || git commit -m "auto: remove sold items"
git pull --rebase origin main
git push origin HEAD:main
