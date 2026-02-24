"""
CSFloat Auto-Auction Bot
========================
Automatically re-lists your CS2 skins as 24h auctions on CSFloat.

Instead of polling on a fixed interval, the bot reads expires_at from
each active listing and sleeps until exactly that time + 5 mins.
This means it always wakes up at the right moment regardless of daily drift.

# SSH in
ssh root@147.182.158.184

# Check logs
cat auction_bot.log

# Restart the bot
pkill -f bot.py
nohup ./run.sh &

# Check bot is running
pgrep -f bot.py

# Stop the bot
pkill -f bot.py
"""
"""
CSFloat Auto-Auction Bot
========================
Automatically re-lists your CS2 skins as 24h auctions on CSFloat.

Instead of polling on a fixed interval, the bot reads expires_at from
each active listing and sleeps until exactly that time + 5 mins.
This means it always wakes up at the right moment regardless of daily drift.

Setup:
  1. pip install requests python-dotenv
  2. Fill in .env  (API key, Steam ID, Discord webhook)
  3. Fill in config.json  (your items)
  4. python bot.py
"""
"""
CSFloat Auto-Auction Bot
========================
Automatically re-lists your CS2 skins as 24h auctions on CSFloat.

Instead of polling on a fixed interval, the bot reads expires_at from
each active listing and sleeps until exactly that time + 5 mins.
This means it always wakes up at the right moment regardless of daily drift.

Setup:
  1. pip install requests python-dotenv
  2. Fill in .env  (API key, Steam ID, Discord webhook)
  3. Fill in config.json  (your items)
  4. python bot.py
"""

import requests
import json
import time
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timezone
import os

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("auction_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("csfloat_bot")

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL     = "https://csfloat.com/api/v1"
CONFIG_FILE  = "config.json"
RELIST_DELAY = 300  # 5 mins after expiry before re-listing


# ── Config ─────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    api_key         = os.getenv("CSFLOAT_API_KEY")
    steam_id        = os.getenv("CSFLOAT_STEAM_ID")
    discord_webhook = os.getenv("DISCORD_WEBHOOK")

    if not api_key:
        log.error("CSFLOAT_API_KEY missing from .env"); sys.exit(1)
    if not steam_id:
        log.error("CSFLOAT_STEAM_ID missing from .env"); sys.exit(1)

    if not Path(CONFIG_FILE).exists():
        log.error(f"'{CONFIG_FILE}' not found — creating template, fill it in and restart.")
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "items": [
                    {
                        "name":          "My Gun",
                        "asset_id":      "PASTE_ASSET_ID_HERE",
                        "reserve_price": 200,
                        "description":   "Check Stall"
                    }
                ]
            }, f, indent=4)
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        cfg = json.load(f)

    if not cfg.get("items"):
        log.error("No items in config.json"); sys.exit(1)

    cfg["api_key"]         = api_key
    cfg["steam_id"]        = steam_id
    cfg["discord_webhook"] = discord_webhook
    return cfg


# ── Discord ────────────────────────────────────────────────────────────────────
def send_discord(webhook_url: str, updates: list[dict]):
    if not webhook_url or not updates:
        return

    statuses = [u["status"] for u in updates]
    if "failed" in statuses:
        color = 0xED4245
    elif "relisted" in statuses:
        color = 0xFEE75C
    else:
        color = 0x57F287

    icon_map = {"active": "🟢", "relisted": "🔄", "waiting": "⏳", "failed": "❌"}

    # Build all fields, chunked into groups of 25 (Discord limit per embed)
    all_fields = []
    for u in updates:
        icon = icon_map.get(u["status"], "❔")
        all_fields.append({
            "name":   f"{icon} {u['name'][:100]}",
            "value":  u["detail"][:100],
            "inline": True,
        })

    chunks = [all_fields[i:i+25] for i in range(0, len(all_fields), 25)]
    embeds = []
    for i, chunk in enumerate(chunks):
        embed = {
            "color":  color,
            "fields": chunk,
        }
        if i == 0:
            embed["title"]     = "CSFloat Auction Bot"
            embed["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if i == len(chunks) - 1:
            embed["footer"] = {"text": "CSFloat Auto-Lister"}
        embeds.append(embed)

    # Discord allows max 10 embeds per message
    payload = {"embeds": embeds[:10]}

    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        if not r.ok:
            log.warning(f"Discord webhook failed: {r.status_code} — {r.text}")
    except Exception as e:
        log.warning(f"Discord webhook error: {e}")


# ── API Client ─────────────────────────────────────────────────────────────────
class CSFloatClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": api_key,
            "Content-Type":  "application/json",
        })

    def _get(self, path: str, params: dict = None, _retries: int = 3):
        for attempt in range(_retries):
            try:
                r = self.session.get(f"{BASE_URL}{path}", params=params, timeout=15)
                if r.status_code == 429:
                    wait = 10 * (attempt + 1)
                    log.warning(f"Rate limited — waiting {wait}s before retry...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.HTTPError as e:
                log.error(f"GET {path} -> {e.response.status_code}: {e.response.text}")
                break
            except requests.RequestException as e:
                log.error(f"GET {path} failed: {e}")
                break
        return None

    def _post(self, path: str, payload: dict):
        try:
            r = self.session.post(f"{BASE_URL}{path}", json=payload, timeout=15)
            if r.status_code == 400:
                body = r.json()
                if body.get("code") == 4:
                    return {"__already_listed": True}
            if r.status_code == 403:
                body = r.json()
                if body.get("code") == 17:
                    return {"__not_in_inventory": True}
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            log.error(f"POST {path} -> {e.response.status_code}: {e.response.text}")
        except requests.RequestException as e:
            log.error(f"POST {path} failed: {e}")
        return None

    def get_my_active_auctions(self, steam_id: str) -> list:
        results = []
        params  = {"user_id": steam_id, "type": "auction", "limit": 50}
        while True:
            data = self._get("/listings", params=params)
            if not data:
                break
            batch  = data if isinstance(data, list) else data.get("data", [])
            results.extend(batch)
            cursor = data.get("cursor") if isinstance(data, dict) else None
            if not cursor or len(batch) < 50:
                break
            params["cursor"] = cursor
        return results

    def create_auction(self, asset_id: str, reserve_price: int, description: str) -> dict | None:
        payload = {
            "asset_id":      asset_id,
            "type":          "auction",
            "reserve_price": reserve_price,
            "duration_days": 1,
            "description":   description,
        }
        return self._post("/listings", payload)


# ── Bot ────────────────────────────────────────────────────────────────────────
class AuctionBot:
    def __init__(self, config: dict):
        self.client          = CSFloatClient(config["api_key"])
        self.steam_id        = config["steam_id"]
        self.discord_webhook = config.get("discord_webhook")

        # asset_id -> item config
        self.watched: dict[str, dict] = {
            item["asset_id"]: item for item in config["items"]
        }

        # asset_id -> listing_id
        self.active: dict[str, str] = {}

        # asset_id -> unix timestamp of expiry (so we know when to wake up)
        self.expires_at: dict[str, float] = {}

        # asset_id -> consecutive failure count (persisted to file)
        self.state_file = Path("bot_state.json")
        self.failures: dict[str, int] = {}
        self.sold: set = set()
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.failures = data.get("failures", {})
                self.sold     = set(data.get("sold", []))
                log.info(f"Loaded state: {len(self.sold)} sold, {len(self.failures)} tracked failures.")
            except Exception as e:
                log.warning(f"Could not load state file: {e}")

    def _save_state(self):
        try:
            self.state_file.write_text(json.dumps({
                "failures": self.failures,
                "sold":     list(self.sold),
            }, indent=2))
        except Exception as e:
            log.warning(f"Could not save state file: {e}")

    def _item_name(self, asset_id: str) -> str:
        return self.watched[asset_id].get("name", asset_id)

    def _parse_expiry(self, expires_str: str) -> float | None:
        """Parse CSFloat's expires_at string into a unix timestamp."""
        try:
            dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return None

    def run(self):
        log.info("Running.")
        try:
            self.tick()
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
        log.info("Done — exiting.")

    def tick(self):
        now     = time.time()
        updates = []

        # Fetch live auctions
        live = self.client.get_my_active_auctions(self.steam_id)
        if live is None:
            log.warning("Could not fetch listings — rate limited or network issue. Skipping relist to avoid false positives, will retry in 10 mins.")
            # Schedule a retry soon but do NOT relist anything
            self.expires_at["__retry"] = time.time() + 600
            return

        # Clear retry sentinel if present
        self.expires_at.pop("__retry", None)

        live_asset_ids = {l["item"]["asset_id"] for l in live}

        for i, (asset_id, item_cfg) in enumerate(self.watched.items()):
            name = self._item_name(asset_id)

            # Skip items marked as sold
            if asset_id in self.sold:
                continue

            if asset_id in live_asset_ids:
                # Active — save expiry time so we know when to wake up
                matched     = next(l for l in live if l["item"]["asset_id"] == asset_id)
                listing_id  = matched["id"]
                expires_str = matched.get("auction_details", {}).get("expires_at", "")
                expiry_ts   = self._parse_expiry(expires_str)

                self.active[asset_id] = listing_id
                if expiry_ts:
                    self.expires_at[asset_id] = expiry_ts

                log.info(f"[{name}] Active  listing={listing_id}  expires={expires_str}")
                updates.append({
                    "name":     name,
                    "asset_id": asset_id,
                    "status":   "active",
                    "detail":   f"Expires: {expires_str}",
                })

            else:
                # Only relist if we previously confirmed it was active
                # If we never saw it listed, it might be a fetch issue not a real expiry
                if asset_id in self.active:
                    self.expires_at.pop(asset_id, None)
                    log.info(f"[{name}] Auction ended — relisting now...")
                    result = self._do_relist(asset_id)
                    updates.append(result)
                    if i < len(self.watched) - 1:
                        time.sleep(45)
                else:
                    log.info(f"[{name}] Not listed — listing now...")
                    result = self._do_relist(asset_id)
                    updates.append(result)
                    if i < len(self.watched) - 1:
                        time.sleep(45)

        # Only ping Discord if something actually happened (relist or failure)
        notable = [u for u in updates if u["status"] in ("relisted", "failed")]
        if notable:
            send_discord(self.discord_webhook, notable)

        self._save_state()

    def _remove_from_config(self, asset_id: str):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            cfg["items"] = [i for i in cfg["items"] if i["asset_id"] != asset_id]
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=4)
            log.info(f"Removed asset {asset_id} from config.json")
        except Exception as e:
            log.warning(f"Could not remove asset {asset_id} from config: {e}")

    def _do_relist(self, asset_id: str) -> dict:
        item_cfg = self.watched[asset_id]
        name     = self._item_name(asset_id)
        log.info(f"[{name}] Listing @ reserve={item_cfg['reserve_price']} '{item_cfg['description']}'")

        result = self.client.create_auction(
            asset_id      = asset_id,
            reserve_price = item_cfg["reserve_price"],
            description   = item_cfg["description"],
        )

        if result and result.get("__not_in_inventory"):
            log.warning(f"[{name}] Item not in inventory — sold! Removing from config.")
            self._remove_from_config(asset_id)
            self.sold.add(asset_id)
            return {
                "name": name, "asset_id": asset_id,
                "status": "failed", "detail": "Item sold — removed from config",
            }

        if result and result.get("__already_listed"):
            log.info(f"[{name}] Already listed — marking as active.")
            self.active[asset_id] = "unknown"
            self.failures.pop(asset_id, None)
            return {
                "name": name, "asset_id": asset_id,
                "status": "active", "detail": "Already listed (picked up by bot)",
            }
        elif result:
            self.failures.pop(asset_id, None)
            new_id      = result.get("id", "?")
            expires_str = result.get("auction_details", {}).get("expires_at", "")
            expiry_ts   = self._parse_expiry(expires_str)
            self.active[asset_id] = new_id
            if expiry_ts:
                self.expires_at[asset_id] = expiry_ts
            log.info(f"[{name}] Listed! ID={new_id}  expires={expires_str}")
            return {
                "name": name, "asset_id": asset_id,
                "status": "relisted", "detail": f"New listing ID: `{new_id}`",
            }
        else:
            self.failures[asset_id] = self.failures.get(asset_id, 0) + 1
            fail_count = self.failures[asset_id]
            if fail_count >= 5:
                self.sold.add(asset_id)
                log.warning(f"[{name}] Failed {fail_count} times — marking as sold, will no longer relist.")
                return {
                    "name": name, "asset_id": asset_id,
                    "status": "failed", "detail": f"Marked as sold after {fail_count} failures",
                }
            log.error(f"[{name}] Listing failed ({fail_count}/5) — retrying next run.")
            return {
                "name": name, "asset_id": asset_id,
                "status": "failed", "detail": f"Failed to list ({fail_count}/5) — retrying next run",
            }


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config = load_config()
    bot    = AuctionBot(config)
    bot.run()