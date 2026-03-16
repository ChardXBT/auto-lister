Key Functions: 

The bot (bot.py):

Reads your items from config.json and credentials from .env
Calls GET /listings?user_id=... on CSFloat to check which of your items are currently on auction
If an item is listed → saves the expiry time and moves on
If an item isn't listed → lists it immediately via POST /listings
Sends a Discord embed summarising everything
Then exits

The cloud server (Digital Ocean Droplet):

$6/month Ubuntu VM running 24/7 in Toronto
Bot files live in /root/csfloat-bot
The bot runs via nohup ./run.sh & which keeps it alive in the background
run.sh pulls the latest code from GitHub before every run so it's always up to date

The 24h auction cycle:

Bot lists your items as 24h auctions on CSFloat
24h later the auction expires
Bot detects the listing is gone, waits 5 mins, relists it
Repeats forever


Bugs: 
If float fucks something up and the bot runs but cannot list etc it will place the orders in the config as sold or failed which fucks it up. If an item is in config but not listing check the bot state file
