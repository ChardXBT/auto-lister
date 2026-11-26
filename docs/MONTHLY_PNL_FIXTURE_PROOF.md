# Monthly PnL fixture proof

Offline generated Main/Rukia-shaped evidence only. It reads no real exports, encrypted files, production accounting, network resources or secrets.

Run: `python3 tools/monthly_pnl_fixture_proof.py --json`. Focused tests: `python3 -m unittest tests.test_monthly_pnl_fixture_proof`.

## Day-1 unsigned follow-up and reversal recheck checklist

- run closed-month audit after the signed-off UTC cutoff
- send one unsigned reminder only when sign-off is missing
- deduplicate exports before queueing any follow-up
- perform reversal recheck for post-cutoff reversals of signed rows
- drain queue and record duplicate/conflict counts

## Deduplication and queue-drain acceptance table

| Check | Status |
|---|---|
| cutoff row included exactly once | pass |
| post-cutoff row excluded from signed close | pass |
| late reversal rechecked without rewriting signed month | pass |
| identical names use distinct asset identity | pass |
| net and gross fee arithmetic separated | pass |
| exact duplicates collapse but conflicts surface | pass |
| reminder queue drains without duplicate work | pass |

## Fixture outcomes

| Scenario | Result |
|---|---|
| cutoff_overlap | `{"excluded_after_cutoff": 1, "included_at_cutoff": 1}` |
| duplicate_exports | `{"canonical_rows": 1, "conflicts": 1, "input_rows": 3}` |
| identical_names_distinct_assets | `{"asset_ids": ["asset-a", "asset-b"], "rows_kept": 2}` |
| late_reversal | `{"recheck_flags": 1, "rewrote_signed_month": false, "signed_month_rows": 1}` |
| net_gross_fees | `{"fee_cents": 125, "gross_cents": 1000, "net_cents": 875}` |

## Boundary

The existing implementation and historical evidence files are git-crypt encrypted in an unlocked-checkout-required repository. This proof deliberately does not decrypt or alter them. Eric's single manual follow-up is to run the real monthly checker once in his unlocked checkout against cleared/generated fixture copies and compare its verdicts to this table before enabling any reminder.
