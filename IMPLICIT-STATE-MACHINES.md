# Implicit State Machines — auto-lister

Audit of tracked source files (worktree of `origin/main`, git-crypt unlocked).
No production code was changed. Anchors are `file:line` at commit `83066b8`.

## Stall-tracker listing lifecycle

**States**
`active` → `delisted_or_returned_to_inventory` | `sold_or_missing` →
`relisted_by_tracker` | `relist_skipped` | `relist_failed` → `converted_to_auction`.
Also a never-produced `due_for_relist` and a parallel `items[...].status` mirror.

**Encoded as**
Raw strings in `entry["status"]` of `stall_tracker_state.json`, combined with the
`relist_due` boolean and `tracker_runs_seen`/`relist_due_after_runs` counters
(`src/auto_lister/stall_tracker.py:247-270`). Recoverability of a failure is
encoded in the *text prefix* of `tracker_relist_detail`
(`RECOVERABLE_RELIST_FAILURE_PREFIXES`, `stall_tracker.py:31-35,455-459`).

**Invalid combos**
- `status == "due_for_relist"` is accepted at `stall_tracker.py:353` but no code
  path ever assigns it — a dead branch that silently excludes nothing.
- `items[item_key].status` stays `"active"` with `current_listing_id` set while
  `listings[listing_id].status == "relist_failed"`: `mark_relist_result`
  (`stall_tracker.py:462-509`) only updates the `items` mirror on
  `relisted_by_tracker`, so the two per-item states diverge on every failure.
- `relist_failed` recoverability depends on free-text detail strings produced in
  `auction_bot.py:2776,2801,2808,2835,2859,2863,2896,2901,2905`; rewording a log
  message changes whether a deleted listing is ever recreated.
- `status == "relist_failed"` + `relist_due is True` is rejected by
  `_validate_relist_candidate_shape` (`auction_bot.py:2918-2920`) unless the
  detail prefix matches — state + message-text flag combo.

**Transitions**
Set: `stall_tracker.py:312` (→active), `361-366` (→delisted/sold_or_missing),
`469-472` (→caller-supplied status), `480,488-498` (new listing →active),
`522-530` (→converted_to_auction). Read: `stall_tracker.py:319,353,419,436-437,452,456`,
`auction_bot.py:2691,2918,2748-2760` (`finish()` maps tracker status → update
status). `mark_converted_to_auction` called from `auction_fallback.py:735-738`.

**Risk**
A stuck `relist_failed` entry whose detail text doesn't match a prefix is never
retried: the item was already deleted from CSFloat and stays unlisted forever —
silent loss of sale exposure. Conversely the items-mirror divergence makes
reporting/recovery code believe a dead listing is still live. **High.**


## Per-asset bot lifecycle via parallel map membership

**States**
`unlisted` / `active` (listed) / `sale pending` / `sold` / `failed` /
`departed-suspicious`, plus a numeric strike phase
(`not_in_inventory_counts`, threshold `NOT_IN_INVENTORY_THRESHOLD`).

**Encoded as**
Membership in ~7 parallel per-asset dicts declared at
`src/auto_lister/auction_bot.py:1200-1209`, persisted at `1806-1811`,
loaded at `1268-1271`: `active` (asset→listing id), `sold` (set),
`expires_at`, `failures`, `not_in_inventory_counts`, `pending_sale_prices`,
`pending_sale_contracts`. An asset's state = which maps contain it; there is no
single field.

**Invalid combos**
- `sold` ∩ `active` is representable — defended against at `2494-2495`
  (`in active and not in sold`), and `3530` writes `active[asset_id]` on
  `delisted` without clearing `sold`.
- `pending_sale_prices` without `pending_sale_contracts`: `3617` sets the price
  before `3619` sets the contract; a crash between them leaves a drop pending
  for a contract the bot can't attribute.
- `active` entries can outlive `expires_at` (or vice-versa) because every
  cleanup site hand-maintains its own pop list: `2016-2019`, `2054-2059`,
  `2279-2282`, `2374-2379`, `3291-3298`, `3343-3349`, `3831-3835`,
  `3962-3965`, `3972-3976`, and cross-module at
  `auction_fallback.py:800-804`.
- `sold` restored by `_reconcile_configured_sold_assets` (`2352-2385`) clears
  only 4 of the maps.

**Transitions**
Adds: `3530,3560,3617-3619,3272,3831` (→active/pending),
`3293,3929` (→sold). Removes: the pop lists above. Membership reads drive the
main loop: `3504,3585,3602,1967` plus `auction_fallback.py:467` (`bot.failures`)
and `smart_sell.py:515` (`bot.sold`/`bot.watched`).

**Risk**
Forgotten pops leave an asset simultaneously "sold" and "listed": the bot may
relist an item it no longer owns (double-listing / failed-trade), or treat a
returned item as sold and never list it again (stuck inventory). **High.**


## Listing-outcome pseudo-states share one `state` field with CSFloat API states

**States**
API states `listed`, `delisted`, `sold`, `refunded` mixed with locally-invented
`lookup_failed` and `lookup_deferred`.

**Encoded as**
`outcome["state"]` produced by `_csfloat_listing_outcome`
(`auction_bot.py:3187` → `lookup_deferred`, `3192` → `lookup_failed`,
`3199` → passthrough `record.get("state")`); raw strings, no enum.

**Invalid combos**
- `lookup_deferred` is not guarded at `3613` (only `lookup_failed` is), so at
  `3627` a deferred lookup fails the `in {"sold","refunded","listed"}` check and
  falls into `_apply_price_decrease` (`3630`) — the reserve is dropped even
  though the auction outcome is *unknown*.
- A cached `None` outcome vs a `lookup_*` dict vs a real record all flow through
  the same `outcome.get("state")` reads (`3533,3580,3613,3627,3830,3840,3887,
  3895,3899,3908`), so any new pseudo-state silently changes several branches.
- `3536` fabricates `{"state": "sold"}` inline — a fourth producer of the same
  field.

**Transitions**
Set: `3187,3192,3199,3536`. Read: `2232,2941,2974,3529,3533,3580,3613,3627,
3830,3840,3887-3908,3956` (auction_bot) and `auction_fallback.py:645,701,719-725`,
`trade_reversals.py:986`, `print_inventory_assets.py:565`.

**Risk**
When the per-run lookup budget is exhausted exactly as an auction ends, the bot
can't tell "sold while settling" from "ended unsold" — and currently resolves it
by cutting the price. Wrong-direction price action / masked sale. **High.**


## Magic-key sentinel results from the CSFloat write client

**States**
`success` (listing dict) / `failed` (`None`) / `account suspended` /
`already listed` / `not in inventory`.

**Encoded as**
Presence of private keys in the returned dict — `"__account_suspended"`,
`"__already_listed"`, `"__not_in_inventory"` — produced at
`auction_bot.py:879,889,893` (HTTP 403/code 17, 400-message sniffing,
suspension code) and consumed at `2852-2866,3854,3863,3949`. `None` is a fourth
state (transport failure).

**Invalid combos**
- A sentinel dict that is also truthy (`new_listing and ...` guards rely on
  callers checking every key in the right order; missing a check treats a
  sentinel as a real listing).
- `__already_listed` detection depends on substring matching of an English API
  message (`888`); any API wording change silently reclassifies the outcome to
  generic failure → infinite delete/relist retry loop.
- `create_auction` and `create_buy_now_listing` share the sentinel convention
  but the consumers check different subsets (`2852-2866` vs `3854-3949`).

**Transitions**
Set: `879,889,893`. Read: `2852,2860,2866,3854,3863,3949`; `finish()`→tracker
statuses at `2859,2863,2879,2886,2891,2896,2901,2905,2909`.

**Risk**
A missed sentinel branch means the bot proceeds as if a listing was created
when it wasn't (or vice versa): items can be deleted from CSFloat and never
relisted, or double-listed. **Medium.**


## Auction-fallback move journal

**States**
`deleting` → `deleted` → `configured` | `kept_buy_now` | `sold_as_buy_now` |
`conflict` (six states; `OPEN_STATUSES`/`FINAL_STATUSES`,
`auction_fallback.py:61-62`).

**Encoded as**
`record["status"]` strings in `auction_fallback.json`; validated on load
(`147`) but transitions are scattered.

**Invalid combos**
- `deleted` with no later transition = item removed from buy-now but never put
  on auction. `conflict` is terminal even though the item is off the market —
  `_idle_reason`/`move_limit` (`487`) count only `OPEN_STATUSES`, so a
  conflicting record neither retries nor alerts after the initial update.
- Records with `kind` + `decrease`/`lowest_sell` extras (`685-689`) mean the
  same status values carry different payload shapes; readers can't assume
  fields.
- `deleting` records are re-settled blindly each run (`394-395`) — if the
  listing record is unreadable the status stays `deleting` and blocks
  `move_limit` (`487`) forever.

**Transitions**
Set: `678` (→deleting), `720` (→deleted), `723` (→kept_buy_now),
`726` (→sold_as_buy_now), `785` (→conflict), `790` (→configured). Read:
`147,166,394,410,487,492,532-533,633-635,706,813-816,896`.

**Risk**
A crash between delete and configure leaves the item off-market; a permanent
`conflict`/`deleting` record silently blocks all future fallback moves for the
account (`busy_*` sets at `532-533`). **Medium.**


## Trade-reversal record + restore sub-state

**States**
`pnl_pending` → `restore_pending` → `completed` | `restore_abandoned`
(`trade_reversals.py:106-107`), plus a per-record `attention` overlay and a
nested `restore` sub-machine (`required`, `waiting_reason`, `alerted_at`,
`waiting_reported_at`, `restored_at`, `outcome`).

**Encoded as**
`record["status"]` strings validated at load (`276`); sub-state inferred from
which `restore.*` timestamps are set (`1473-1509`) — null/timestamp inference —
and `record["status"]` itself derived from the `restore.required` boolean at
`1453` (flag → state derivation).

**Invalid combos**
- `attention` set on a `FINAL` record: summary (`1926`) reports it as needing
  attention while `FINAL_STATUSES` readers treat it done — both true at once.
- `restore.waiting_reason`/`alerted_at`/`waiting_reported_at` presence combos
  encode sub-phases; `1473` only stamps `waiting_reason` when it *changes*, so
  a stale reason can persist after the real reason cleared.
- `status` is rewritten from a boolean each `_remove_pnl_row` call (`1453`) —
  any out-of-band status edit is silently destroyed.

**Transitions**
Set: `1453` (→restore_pending/completed), `1480` (→restore_abandoned),
`1525` (→completed), initial `pnl_pending` at record creation. Read:
`276,1557,1561,1622,1699,1897,1926-1928`.

**Risk**
An `attention`-flagged "completed" reversal looks finished to `FINAL_STATUSES`
queries yet still needs a human; a `restore_abandoned` record whose item later
returns is never revisited — the sale stays voided and the item stays unlisted.
**Medium.**


## Listing-ledger entry status

**States**
`listed` → `sale_detected` | `sale_reversed`, interleaved with disappearance
classifications `internal_transfer` / `trade_hold_or_pending_transfer` /
`unknown_disappearance` / `left_inventory_without_csfloat_sale` — two domains
in one field.

**Encoded as**
`entry["status"]` strings in `listing_ledger.json`; the pre-reversal status is
preserved in `status_before_reversal` (`listing_ledger.py:266`).

**Invalid combos**
- Disappearance classifications are stored in the same `status` field as
  lifecycle states, so a `listed`→`internal_transfer` entry is permanently
  neither "listed" nor terminal — no transition back to `listed` exists.
- `_latest_contract_for_asset` (`283-303`) excludes only `sale_reversed`
  entries; an entry stuck in `listed` after the asset actually sold stays the
  canonical contract for future sale lookups (used at `auction_bot.py:3176`).
- `status_before_reversal` may hold a disappearance classification, so a
  "reversed" entry can claim a previous status that was never a sale.

**Transitions**
Set: `123` (→listed), `168` (→sale_detected), `236` (→classification),
`265` (→sale_reversed). Read: `260,293`, `listing_recovery.py:99-101`.

**Risk**
Stale `listed` contracts get reused as `pending_sale_contracts` candidates — a
new sale can be attributed to a months-old contract id, writing wrong P&L rows.
**Medium.**


## Listing-suspension cooldown via timestamp presence

**States**
`not suspended` / `suspended until T` / `suspension expired pending cleanup`.

**Encoded as**
Presence and recency of `listing_suspended_until` (ISO string) in bot state —
null/timestamp inference. Set at `auction_bot.py:1287` (load),
activated at `1949-1960`, expired-and-cleared inside the *reader*
`_listing_suspension_detail` (`1929-1944`).

**Invalid combos**
- A timestamp in the past is treated as "not suspended" only when the reader
  runs; every other field of state still claims the account is suspended.
- `_activate_listing_suspension` extends `until` monotonically (`1953-1957`), so
  a suspension shorter than the stored one can't shorten it — state can only
  ratchet forward.
- Dozens of scattered check sites (`2299,2484,2607,2671,2758,3634,3643`,
  `auction_fallback.py:436,420`, `smart_sell.py:464`); any new listing path that
  forgets the check writes through a suspension.

**Transitions**
Set: `1287,1956`; cleared: `1936-1938`. Read: the `_listing_suspension_detail`
call sites above.

**Risk**
During suspension the bot must not POST listings; a missed gate hammers a
suspended account and can extend the suspension. State lives in a timestamp
string, so corrupted/empty values silently mean "not suspended". **Medium-low.**


## Per-run update status vocabulary (+ `stalled` overlay)

**States**
`active`, `relisted`, `waiting`, `failed`, `snapshot`, `tracker_active`,
`tracker_delisted`, `tracker_missing`, `unlisted`, `reversed`, `sold`,
`stalled_sale`, `internal_transfer` — plus a `stalled=True` flag riding on
`waiting`.

**Encoded as**
`update["status"]` strings constructed inline at ~30 sites (producers:
`auction_bot.py:2023-2063,2224,2287,2460,2520,2586,2714-2739,2750-2754,
3040,3216-3239,3287,3312,3361,3429,3526-3571,3934,3978,4012-4027`;
`trade_reversals.py` `_update` statuses `waiting`/`failed`/`reversed`).
`stalled` is a boolean field layered on `waiting` (`3234`).

**Invalid combos**
- `status == "waiting" and stalled is True` is a distinct pseudo-state only some
  readers know (`3651` filters it, `3663-3671` don't).
- A *sold* item is reported as `status: "failed"` (`3934`) — the vocabulary
  conflates outcome classes.
- `discord_status` compresses tracker outcomes differently than the icon map
  (`2750`: everything non-relisted → `tracker_missing`).
- `icon_map` (`696-709`) is the nearest thing to a schema; any status not in it
  renders as "❔" with no error.

**Transitions**
Set: the producer sites above. Read: `688-694` (color), `713` (icon),
`3651-3653,3663-3671` (notable filtering), `3640` (`relisted` counter),
`auction_fallback.py:813-816` (`auction_result` reuse).

**Risk**
Miscategorized alerts hide real failures (e.g., a sale reported as "failed" can
train operators to ignore `failed`), and the `stalled` flag means two
`waiting` updates have opposite urgency. **Medium-low.**


## Portfolio per-account run status

**States**
`pending` → `preflight_ok` / `preflight_failed` / `configuration_failure` /
`deferred_global_dependency` / `deferred_shared_state` → `completed` /
`failed` / `failed_account_state` / `failed_global_dependency` /
`failed_shared_state` (~11 strings).

**Encoded as**
`account_status[key]` strings in `portfolio.py` (`907-1136`).

**Invalid combos**
- `pending` entries past the preflight barrier are reinterpreted as
  `deferred_*` at `1131-1136` — the same string means "not started" and
  "skipped on purpose" depending on when it's read.
- `preflight_ok` is required at `952,1028,1046,1086` for shared phases; an
  account marked `completed` upstream without the `preflight_ok` hop is
  unreachable by construction but representable.
- `disabled` (defaults `994-995`) is a read-side pseudo-state never assigned.

**Transitions**
Set: `907,914,926,934,945,960,1073,1112,1116,1118,1121,1126,1131,1135`.
Read: `945,952,986,1028,1046,1086,1131-1136,1238`.

**Risk**
A mid-loop failure that skips a transition site leaves the account `pending`,
which downstream reporting reads as a deferred (benign) skip rather than a
crash — supervision misses a dead account. **Medium-low.**


## Smart-sell journal item status

**States**
`listing` (in-flight intent) → `pending` → `listed` | `owner_listed`
(terminal; `TERMINAL_STATUSES` at `smart_sell.py:51`), gated by
`rules["mode"]` ∈ `{off, trial, live}` (`43,146,419,502`).

**Encoded as**
`entry["status"]` in the smart-sell journal; intent entries are settled by
re-inspecting the live stall (`_settle_intents`, `519-530`).

**Invalid combos**
- `listing` with no matching live listing → rewritten to `pending` (`525`) —
  intent and outcome share the field, so a crashed list call is
  indistinguishable from "scheduled, not yet attempted".
- `owner_listed` is terminal forever (`494`): if the owner later delists, the
  journal still suppresses the bot from listing that item.
- `listed` requires `listing.get("price") == entry.get("start")` (`526`); a
  bot listing whose price was edited is reclassified `owner_listed` — ownership
  is inferred from a price match, not a flag.

**Transitions**
Set: `525,527,529,677,691`. Read: `200,427,494` and
`auction_fallback.py:686` (`rotation_terms` reads `listed` entries).

**Risk**
A misclassified `owner_listed` permanently removes a due item from automation;
a stuck `listing` intent blocks nothing but also guarantees the item is never
re-evaluated for new terms. **Medium-low.**


## CSFloat transaction CSV contract classifier + import entry status

**States**
Per-contract: `sold` → `refunded` → re-`sold` (row-type driven,
`import_csfloat_transactions.py:144-160`) with terminal reasons
`not_a_sale` / `sale_refunded` / `not_verified_yet` /
`verified_with_invalid_balance` / `verified_without_positive_balance` /
`verified`. Per-entry: `sale_journaled` → `excel_reconciled` | `excel_logged`.

**Encoded as**
A `state` variable driven by chronological `Type` strings
(`contract_sold`, `contract_sale_refund`, `trade_verified`), and
`entry["status"]` strings written at `567,592,620`.

**Invalid combos**
- `trade_verified` only counts while `state == "sold"` (`158`); a CSV where the
  verify row precedes the sold row (bad ordering) silently classifies
  `not_verified_yet` — a completed sale is skipped.
- `state` is reset per row type but `verified_row` too (`154,157`), so
  interleaved refund/resale groups can produce `verified` for the *first* sale
  after a refund voided it.
- Entry status has no terminal validation: an `excel_logged` entry later
  reconciled writes `excel_reconciled`, but nothing prevents a
  `sale_journaled` entry from also being logged — double-accounted sales in
  the workbook.

**Transitions**
Set: `153,156,158` (classifier), `567,592,620` (entry). Read: `160-171`
(reason strings), `581-617` (branches on `reconcile_existing`/`journal_only`).

**Risk**
Misclassified refund-then-resale contracts can book a sale twice or drop a real
one — direct P&L corruption in the Excel ledger. **Medium-low.**


## Steam/CSFloat trade-state literals

**States**
Steam `trade_offer_state` ints `{2, 9, 11}` active / `3` accepted; CSFloat
trade `state` strings `pending` / `verified`; CSFloat trade-contract `state`
`refunded`.

**Encoded as**
Bare magic ints (`steam_trade_guard.py:22` `ACTIVE_SENT_OFFER_STATES`,
`trade_reversals.py:86` `STEAM_OFFER_ACCEPTED = 3`,
`auction_bot.py:284` `STEAM_OFFER_ACTIVE = 2`) and raw strings
(`auction_bot.py:3256` `"pending"`, `print_inventory_assets.py:565`
`"verified"`, `trade_reversals.py:986` `"refunded"`, `1007`).

**Invalid combos**
- Two different constants name overlapping Steam states in different modules
  (`{2,9,11}` vs `2` vs `3`); an offer in state `9` or `11` blocks listings
  through the guard but fails the `STEAM_OFFER_ACTIVE == 2` stalled-sale check
  at `3222`, so it is classified "waiting" rather than "stalled_sale".
- CSFloat `trade["state"]` and `contract["state"]` are compared against
  unrelated string sets; mixing them up compiles fine.

**Transitions**
Set: Steam API / CSFloat API payloads. Read: `steam_trade_guard.py:107`,
`auction_bot.py:3222,3256`, `trade_reversals.py:986,1001-1007`,
`print_inventory_assets.py:565`.

**Risk**
The stalled-sale detector misses offers in states 9/11 (confirmation-pending /
escrow) — a genuinely stalled sale looks like a routine wait and never alerts.
**Medium-low.**
