# Auto Lister portfolio operations

Auto Lister has one process, one GitHub Actions job, and one final state publication. The process takes an OS-backed portfolio lock, performs read-only preflight for every enabled account, then runs each account phase sequentially. With both accounts enabled, `data/shared/portfolio_state.json` rotates the first account between runs.

## Feature gate

`AUTO_LISTER_ACCOUNTS` is a comma-separated subset of `main,rukia`. Missing or blank means `main`. The existing `python bot.py` command therefore remains Main-only. Rukia is never enabled merely because `CSFLOAT_RUKIA_API_KEY` exists.

The workflow's optional `accounts` dispatch input overrides the repository variable for that run. This is intended for read-only validation. Examples:

```powershell
python bot.py --accounts main --preflight-only
python bot.py --accounts rukia --preflight-only
python bot.py --accounts main,rukia --preflight-only
```

Full Rukia processing requires both `CSFLOAT_RUKIA_API_KEY` and `CSFLOAT_RUKIA_STEAM_ID`, plus a structurally valid, Rukia-tagged `data/accounts/rukia/config.json`. An empty `items` array is a valid configured-item no-op; it does not disable Rukia's independent stall tracker.

Read-only preflight validates that every Rukia config entry has a unique string asset ID and exact name/float identity, passes the existing price/cost rules, is absent from Main config, and resolves to Rukia inventory or an active Rukia stall listing. Successful Actions preflights print only identifier-free counts and validation booleans. A malformed or unsafe Rukia config fails before Rukia account state or marketplace mutations; an empty item list succeeds with zero configured items while the existing Main-equivalent stall tracker remains active.

## Mutable-state ownership

| Classification | Main compatibility path | Rukia/full-account path or shared path |
| --- | --- | --- |
| account-specific config, bot state, relist counts | `data/config.json`, `data/bot_state.json`, `data/relist_counts.json` | same names under `data/accounts/rukia/` |
| account-specific manual sale/update/removal queues | existing files under `data/` | same names under `data/accounts/rukia/`; new Rukia records require `"account_key": "rukia"` |
| account-specific ownership/history | asset archive, inventory snapshots, cost basis, pending cost backfill, listing ledger | same names under `data/accounts/rukia/` |
| account-specific recovery/tracker state | listing recovery JSON/text, stall state, daily tracker state/items, temp listings, disabled Steam guard snapshot | same names under `data/accounts/rukia/` |
| shared accounting | `data/workbooks/CS_GO_PnL_Tracker_Final.xlsx` and `data/marketplace_fees.json` | one combined workbook and fee configuration |
| shared operational log/lock | `data/auction_bot.log` | existing combined private log plus ignored `data/shared/.portfolio.lock`; account phase boundaries are tagged in the log |
| shared idempotency/reconciliation | n/a | `data/shared/sale_events.jsonl`, `transfer_events.jsonl`, and `portfolio_attention.jsonl` |
| shared run control/status | n/a | `data/shared/portfolio_state.json`, `portfolio_preflight.json`, and `portfolio_summary.json` |
| shared import projection | `data/transaction_import_state.json`, `data/unmatched_sales.csv`, `data/monthly_extra_sales.json`, import-backup workbook | account-tagged/scoped records in the same shared files |
| legacy Rukia notification sidecar | `data/rukia_daily_tradable_items.json`, `data/rukia_daily_tracker_state.json` | remains a portfolio sidecar and executes at most once per run |
| obsolete | none | no production state was deleted or mass-migrated |

Legacy unscoped manual records and untagged account state are treated as Main-owned. A Rukia phase fails closed if a copied state file claims Main ownership.

Portfolio preflight retains all enabled inventories in its encrypted shared snapshot for transfer comparison, but only explicitly configured Rukia assets seed Rukia's account-owned asset archive. Unconfigured Rukia inventory is not added to the full-account managed state.

## Transfer and sale safety

Preflight captures both enabled inventories and stalls before marketplace mutations. Cross-account matching uses canonical name plus exact float and, when present, paint seed, paint index, and sticker identity. Asset ID is supporting evidence only. A unique strong match becomes `internal_transfer`; ambiguity or incomplete identity becomes an attention event and cannot append a P&L sale.

Logical sales are fsync'd to the shared JSONL journal before terminal sale state is persisted. The workbook has a hidden `_AUTO_LISTER_SALES` sheet containing deterministic sale IDs and account ownership; columns A-J on `CSGO PnL Tracker` retain their existing meaning. Workbook projection uses a same-directory temporary file, reopens and validates it, then atomically replaces the live file.

## Failure behavior

- Missing/bad Rukia-only configuration skips Rukia and permits Main to continue.
- A shared/global preflight rate limit stops all marketplace phases. A global dependency failure detected during one phase defers the remaining account.
- Corrupt shared state or journal data stops shared writes.
- Lock contention exits with code 75 and performs zero marketplace mutations.
- Marketplace state and accounting evidence are persisted under `data/` before the workflow's one final normal Git commit/rebase/push retry sequence.

## Runtime budget

The 12 production runs inspected before this change were all externally dispatched and successful. They averaged 9.93 minutes and ranged from 3.18 to 37.73 minutes. Doubling the observed peak for two sequential account phases is about 75.5 minutes before portfolio preflight, shared accounting, and publication, so the workflow timeout is 120 minutes rather than the previous 90. Account-level API retry and listing-delay bounds remain unchanged.
