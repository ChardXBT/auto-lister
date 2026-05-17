import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# Run with python print_inventory_assets.py
load_dotenv()

INVENTORY_URL = "https://csfloat.com/api/v1/me/inventory"
PREV_INVENTORY_FILE = Path("prev_inventory_assets.txt")
NEW_INVENTORY_FILE = Path("new_inventory_assets.txt")


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
            return
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def format_row(name: str, float_value: float, asset_id: str) -> str:
    return f"{name} | {float_value:.12f} | {asset_id}"


def load_previous_asset_ids() -> set[str]:
    if not PREV_INVENTORY_FILE.exists():
        return set()

    asset_ids = set()
    for line in PREV_INVENTORY_FILE.read_text(encoding="utf-8").splitlines():
        parts = [part.strip() for part in line.rsplit("|", maxsplit=2)]
        if len(parts) == 3 and parts[2]:
            asset_ids.add(parts[2])
    return asset_ids


def write_inventory_files(rows: list[tuple[str, float, str]]) -> int:
    previous_asset_ids = load_previous_asset_ids()
    new_rows = [row for row in rows if row[2] not in previous_asset_ids]

    # Always recreate this file from scratch so it only reflects the latest run.
    NEW_INVENTORY_FILE.write_text(
        "\n".join(format_row(*row) for row in new_rows),
        encoding="utf-8",
    )

    PREV_INVENTORY_FILE.write_text(
        "\n".join(format_row(*row) for row in rows),
        encoding="utf-8",
    )

    return len(new_rows)


def build_inventory_rows(inventory: Any) -> list[tuple[str, float, str]]:
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
    return rows


def refresh_inventory_files() -> tuple[int, int]:
    rows = build_inventory_rows(fetch_inventory())
    if not rows:
        return 0, 0
    return len(rows), write_inventory_files(rows)


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

    rows = build_inventory_rows(inventory)

    if not rows:
        print("No inventory items with asset_id + float_value found.")
        return 1

    new_count = write_inventory_files(rows)

    for name, float_value, asset_id in rows:
        print(format_row(name, float_value, asset_id))

    print()
    print(f"Saved full inventory snapshot to {PREV_INVENTORY_FILE}")
    print(f"Saved {new_count} new item(s) to {NEW_INVENTORY_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
