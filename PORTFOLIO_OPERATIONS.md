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
| account-specific manual sale/update/removal/reversal queues | existing files under `data/`, plus `manual_reversals.json` | same names under `data/accounts/rukia/`; new Rukia records require `"account_key": "rukia"` |
| account-specific removed-config archive | `data/removed_config_items.json` | same name under `data/accounts/rukia/` |
| account-specific ownership/history | asset archive, inventory snapshots, cost basis, pending cost backfill, listing ledger | same names under `data/accounts/rukia/` |
| account-specific recovery/tracker state | listing recovery JSON/text, stall state, daily tracker state/items, temp listings, disabled Steam guard snapshot | same names under `data/accounts/rukia/` |
| shared accounting | `data/workbooks/CS_GO_PnL_Tracker_Final.xlsx` and `data/marketplace_fees.json` | one combined workbook and fee configuration |
| shared operational log/lock | `data/auction_bot.log` | existing combined private log plus ignored `data/shared/.portfolio.lock`; account phase boundaries are tagged in the log |
| shared idempotency/reconciliation | n/a | `data/shared/sale_events.jsonl`, `transfer_events.jsonl`, `portfolio_attention.jsonl`, and `trade_reversals.json` |
| shared run control/status | n/a | `data/shared/portfolio_state.json`, `portfolio_preflight.json`, and `portfolio_summary.json` |
| shared import projection | `data/transaction_import_state.json`, `data/unmatched_sales.csv`, `data/monthly_extra_sales.json`, import-backup workbook | account-tagged/scoped records in the same shared files |
| legacy Rukia notification sidecar | `data/rukia_daily_tradable_items.json`, `data/rukia_daily_tracker_state.json` | remains a portfolio sidecar and executes at most once per run |
| obsolete | none | no production state was deleted or mass-migrated |

Legacy unscoped manual records and untagged account state are treated as Main-owned. A Rukia phase fails closed if a copied state file claims Main ownership.

Portfolio preflight retains all enabled inventories in its encrypted shared snapshot for transfer comparison, but only explicitly configured Rukia assets seed Rukia's account-owned asset archive. Unconfigured Rukia inventory is not added to the full-account managed state.

## Transfer and sale safety

Preflight captures both enabled inventories and stalls before marketplace mutations. Cross-account matching uses canonical name plus exact float and, when present, paint seed, paint index, and sticker identity. Asset ID is supporting evidence only. A unique strong match becomes `internal_transfer`; ambiguity or incomplete identity becomes an attention event and cannot append a P&L sale.

A configured item that leaves inventory and stall is confirmed against CSFloat's own record of its latest listing. A sold listing is logged at its final price; a delisted one after repeated inventory checks means the item left another way, so it is removed from config without a P&L sale and an alert is raised. Same-name items in the other account no longer count as transfer candidates when their float clearly differs.

Logical sales are fsync'd to the shared JSONL journal before terminal sale state is persisted. The workbook has a hidden `_AUTO_LISTER_SALES` sheet containing deterministic sale IDs and account ownership; columns A-J on `CSGO PnL Tracker` retain their existing meaning. Workbook projection uses a same-directory temporary file, reopens and validates it, then atomically replaces the live file.

Read-only workbook validation streams the hidden sales sheet once. Its cost grows in proportion to the sales history. Duplicate sale IDs, invalid statuses, and invalid row links still stop processing before marketplace mutations.

## Inventory delist guard

At the start of each account phase, every stall listing (buy-now and auction, configured or not) is checked against that account's own inventory. A listing whose asset has left the inventory, because it was sold or traded elsewhere, is deleted so a CSFloat sale cannot fail and incur a penalty. Main is only compared with Main's inventory and Rukia only with Rukia's.

- An asset counts as missing only if it is absent from two inventory reads 15 seconds apart. Items without a float (agents, charms, stickers, cases) count as present.
- An incomplete stall fetch, a failed or empty inventory read, or more missing assets than `INVENTORY_DELIST_MAX_PER_RUN` (default 10), or than half of a stall with more than 3 missing, deletes nothing. The last case sends a Discord alert.
- Each listing is re-fetched before deletion and must still be listed, owned by the account, and hold the same asset.
- An auction with a bid cannot be cancelled. It is reported once in Discord and left alone.
- A configured auction delisted this way is not treated as an ended auction: no price decrease, no relist that run, and no P&L sale.
- `INVENTORY_DELIST_ENABLED=false` disables the guard.

## Trade reversals

A sale whose Steam trade is reversed after it was logged is voided by the reversal phase, which runs after preflight and before every account phase. It deletes that sale's P&L row (rows below move up one) and, for a configured auction item, re-adds the exact removed config entry once the asset is back in inventory and tradable. Reversals are detected automatically by a read-only CSFloat trades scan at the start of every run (`REVERSAL_API_CHECK_INTERVAL_HOURS`, default 0 = every run), and can also come from a manual queue or a transaction CSV import; each matches exactly one logged sale by listing ID and is applied at most once. Refunds that cannot be matched safely raise one Discord review alert and change nothing, and a scan that keeps failing for 12 hours raises one alert. An auction a buyer won (listing `sold` or `refunded`) no longer gets the ended-auction price drop. The phase makes no marketplace mutations. A reversal-phase failure leaves listing running and fails the run. The operator guide is the encrypted `TRADE_REVERSALS.md`.

## Auction fallback

When an account phase lists no new config item, the bot moves up to `AUCTION_FALLBACK_MAX_PER_RUN` (default 5) buy-now stall listings to auction, Main only by default (`AUCTION_FALLBACK_ACCOUNTS`). Eligible: the asset's only live listing is buy-now, it has an exact float, and its purchase cost is known (config cost, or a non-ambiguous asset-ID record in the cost file). Config items stuck on buy-now go first, then the cheapest. Each listing is re-verified live, journaled, deleted, confirmed `delisted`, added to config with its buy-now price as reserve (blank description, no price drop) and listed through the normal auction call; the next run finishes any interrupted move. It is skipped when the listing pass did not complete or CSFloat is unhealthy, and finding nothing is not an error. The operator guide is the encrypted `AUCTION_FALLBACK.md`.

## Failure behavior

- Listing stops starting new auctions after `AUTO_LISTER_LISTING_BUDGET_MINUTES` (default 95) so every run finishes and commits its state inside the 120-minute job; the rest are listed by the next run. The run step has a 112-minute hard stop as a backstop.
- Preflight and config-validation failures are written to the encrypted log; Actions console output stays suppressed.
- Missing/bad Rukia-only configuration skips Rukia and permits Main to continue.
- Read-only preflight retries interrupted connections, timeouts, truncated/undecodable responses, invalid JSON, temporary HTTP failures, and CSFloat's intermittent HTTP 400. Each request has one three-attempt budget with 15- and 30-second waits. A later stall page is retried with the same cursor; completed pages are retained once.
- A shared/global preflight rate limit stops all marketplace phases. A global dependency failure detected during one phase defers the remaining account.
- Exhausted preflight dependency retries stop all marketplace phases before any bot is constructed. Authentication/permission failures and identity mismatches still fail closed. Retry diagnostics contain endpoint labels and exception types, without credentials, response bodies, or account identifiers.
- Corrupt shared state or journal data stops shared writes.
- Lock contention exits with code 75 and performs zero marketplace mutations.
- Marketplace state and accounting evidence are persisted under `data/` before the workflow's one final normal Git commit/rebase/push retry sequence.
- A rebase conflict at publication aborts the rebase, pushes the run's commit to a `rescue/run-*` branch, and fails the job instead of discarding the run's state.

## Runtime budget

The 12 production runs inspected before this change were all externally dispatched and successful. They averaged 9.93 minutes and ranged from 3.18 to 37.73 minutes. Doubling the observed peak for two sequential account phases is about 75.5 minutes before portfolio preflight, shared accounting, and publication, so the workflow timeout is 120 minutes rather than the previous 90. Account-level API retry and listing-delay bounds remain unchanged.
