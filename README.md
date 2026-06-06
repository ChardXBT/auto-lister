See https://github.com/ChardXBT/CS2-Marketplace-Trading-System for README.

Project layout:

- `bot.py` is the stable entrypoint used by local runs, VM scripts, and GitHub Actions.
- `src/auto_lister/` contains the bot code.
- `data/` contains encrypted state, logs, recovery snapshots, and workbook data.
- `data/workbooks/` contains the PnL workbooks.
