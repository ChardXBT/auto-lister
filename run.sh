#!/bin/bash
cd /root/csfloat-bot

git pull origin master:main

python3 bot.py

git add config.json
git diff --cached --quiet || git commit -m "auto: remove sold items" && git push origin master:main
