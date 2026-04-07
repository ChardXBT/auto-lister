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

---

# Auto-Lister — System Overview

## Architecture

```mermaid
flowchart TD
    A["⏱️ Cron Scheduler\n(DigitalOcean / GitHub Actions)"] --> B["🐍 Python Controller\n(bot.py)"]
    B --> C["🌐 Market API\n(CSFloat REST)"]
    C --> D{"Listing Active?"}
    D -- Yes --> E["📝 Log Expiry Time"]
    D -- No --> F["📤 Post New Listing"]
    E --> G["💾 JSON State Persistence\n(bot_state.json / config.json)"]
    F --> G
    G --> H["🔔 Discord Notification\n(Embed Summary)"]
    G --> I["☁️ GitHub\n(State Sync via git push)"]
    I --> B
```

Every cycle: the scheduler triggers the controller, which checks live marketplace listings via REST, updates local JSON state, relists any expired items, and reports the outcome to Discord. Config and state are committed back to GitHub so every run starts from a clean, version-controlled snapshot.
human-readable, and requires no infrastructure. `bot_state.json` and `config.json` are committed to GitHub after every run, making state inspectable and recoverable without a database client. The tradeoff is acceptable: query complexity is low and the dataset is small.
