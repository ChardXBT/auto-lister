See https://github.com/ChardXBT/CS2-Marketplace-Trading-System for README.

Project layout:

- `bot.py` is the stable entrypoint used by local runs, VM scripts, and GitHub Actions.
- `src/auto_lister/` contains the bot code.
- `data/` contains encrypted state, logs, recovery snapshots, and workbook data.
- `data/workbooks/` contains the PnL workbooks.

Manual Excel queues processed at the start of every bot run:

- `data/manual_sales.json` appends new sale rows.
- `data/manual_price_updates.json` replaces sold price and supplied cost on one
  existing row matched by skin name + float. It never appends a row. Failed,
  missing, or ambiguous matches remain queued for the next run.

Example `manual_price_updates.json`:

```json
[
  {"name": "MP9 | Sand Dashed 0.130889981985", "sold": 450, "cost": 310}
]
```

`sold` and `cost` must be whole integer cents, matching `manual_sales.json`.
The name must include the float and the exact skin variant, including `StatTrak™`
or `Souvenir` when applicable. Notes after the float are ignored when matching.
