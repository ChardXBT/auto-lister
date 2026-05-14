import os
import sys
from typing import Any

import requests
from dotenv import load_dotenv

# Run with python print_inventory_assets.py
load_dotenv()

INVENTORY_URL = "https://csfloat.com/api/v1/me/inventory"


def fetch_inventory() -> Any:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
    }

    api_key = os.getenv("CSFLOAT_API_KEY")
    if api_key:
        headers["Authorization"] = api_key

    cookie = os.getenv("CSFLOAT_COOKIE")
    if cookie:
        headers["Cookie"] = cookie

    response = requests.get(INVENTORY_URL, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def walk(value: Any):
    if isinstance(value, dict):
        if "asset_id" in value and "float_value" in value:
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def main() -> int:
    try:
        inventory = fetch_inventory()
    except requests.HTTPError as exc:
        body = exc.response.text[:500] if exc.response is not None else ""
        print(f"Failed to fetch inventory: {exc}\n{body}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to fetch inventory: {exc}", file=sys.stderr)
        return 1

    rows = []
    for item in walk(inventory):
        asset_id = str(item.get("asset_id") or "").strip()
        name = str(
            item.get("market_hash_name")
            or item.get("item_name")
            or item.get("name")
            or "Unknown Item"
        ).strip()

        try:
            float_value = float(item["float_value"])
        except (TypeError, ValueError):
            continue

        if asset_id:
            rows.append((name, float_value, asset_id))

    rows.sort(key=lambda row: (row[0].lower(), row[1], row[2]))

    if not rows:
        print("No inventory items with asset_id + float_value found.")
        return 1

    for name, float_value, asset_id in rows:
        print(f"{name} | {float_value:.12f} | {asset_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
