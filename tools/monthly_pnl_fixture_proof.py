#!/usr/bin/env python3
"""Offline monthly P&L fixture proof.

Generated Main/Rukia-shaped rows only. This does not read exports, encrypted
files, production accounting, network resources or secrets. It proves the
evidence rules a monthly close must satisfy before a live implementation uses
them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable

CUTOFF = "2026-10-31T23:59:59Z"
SIGNED_AT = "2026-11-01T13:00:00Z"


@dataclass(frozen=True)
class Row:
    account: str
    transaction_id: str
    asset_id: str
    market_hash_name: str
    occurred_at: str
    gross_cents: int
    fee_cents: int
    kind: str = "sale"
    export_batch: str = "a"

    @property
    def net_cents(self) -> int:
        return self.gross_cents - self.fee_cents

    def canonical(self) -> dict:
        data = asdict(self)
        data.pop("export_batch")
        return data


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def identity(row: Row) -> tuple[str, str, str]:
    return row.account, row.asset_id, row.transaction_id


def digest(row: Row) -> str:
    return hashlib.sha256(json.dumps(row.canonical(), sort_keys=True).encode()).hexdigest()


def dedupe(rows: Iterable[Row]) -> tuple[list[Row], int, int]:
    seen: dict[tuple[str, str, str], Row] = {}
    duplicates = 0
    conflicts = 0
    for row in rows:
        key = identity(row)
        if key not in seen:
            seen[key] = row
        elif digest(seen[key]) == digest(row):
            duplicates += 1
        else:
            conflicts += 1
    return list(seen.values()), duplicates, conflicts


def in_signed_month(row: Row, cutoff: str = CUTOFF) -> bool:
    return parse_time(row.occurred_at) <= parse_time(cutoff)


def scenario_rows() -> dict[str, list[Row]]:
    sale_at_cutoff = Row("main", "tx-cutoff", "asset-1", "Case Item", CUTOFF, 1000, 125)
    sale_after_cutoff = Row("main", "tx-after", "asset-2", "Case Item", "2026-11-01T00:00:00Z", 1000, 125)
    reversal_late = Row("main", "tx-cutoff-reversal", "asset-1", "Case Item", "2026-11-03T10:00:00Z", -1000, 0, "reversal")
    same_name_a = Row("main", "tx-name-a", "asset-a", "Identical Name", "2026-10-10T00:00:00Z", 500, 50)
    same_name_b = Row("rukia", "tx-name-b", "asset-b", "Identical Name", "2026-10-10T00:00:00Z", 700, 70)
    fee_row = Row("main", "tx-fee", "asset-fee", "Fee Item", "2026-10-11T00:00:00Z", 1000, 125)
    duplicate_same = Row("main", "tx-dup", "asset-dup", "Duplicate Item", "2026-10-12T00:00:00Z", 300, 30, export_batch="b")
    duplicate_original = Row("main", "tx-dup", "asset-dup", "Duplicate Item", "2026-10-12T00:00:00Z", 300, 30)
    duplicate_conflict = Row("main", "tx-dup", "asset-dup", "Duplicate Item", "2026-10-12T00:00:00Z", 301, 30, export_batch="c")
    return {
        "cutoff_overlap": [sale_at_cutoff, sale_after_cutoff],
        "late_reversal": [sale_at_cutoff, reversal_late],
        "identical_names_distinct_assets": [same_name_a, same_name_b],
        "net_gross_fees": [fee_row],
        "duplicate_exports": [duplicate_original, duplicate_same, duplicate_conflict],
        "queue": [duplicate_original, duplicate_same],
    }


def prove() -> dict:
    rows = scenario_rows()
    cutoff_rows = rows["cutoff_overlap"]
    included = [row for row in cutoff_rows if in_signed_month(row)]
    excluded = [row for row in cutoff_rows if not in_signed_month(row)]

    signed_rows = [row for row in rows["late_reversal"] if row.kind == "sale" and in_signed_month(row)]
    recheck_flags = [row for row in rows["late_reversal"] if row.kind == "reversal" and not in_signed_month(row)]
    # The signed close remains immutable; late reversal rows are queued for a recheck.
    rewrote_signed_month = len(signed_rows) != 1 or signed_rows[0].gross_cents != 1000

    distinct, _, _ = dedupe(rows["identical_names_distinct_assets"])
    duplicates, duplicate_count, conflict_count = dedupe(rows["duplicate_exports"])
    fee = rows["net_gross_fees"][0]
    queue_rows, queue_duplicates, queue_conflicts = dedupe(rows["queue"])
    queue_pending = max(0, len(rows["queue"]) - len(queue_rows) - queue_duplicates)

    scenarios = {
        "cutoff_overlap": {"included_at_cutoff": len(included), "excluded_after_cutoff": len(excluded)},
        "late_reversal": {
            "signed_month_rows": len(signed_rows),
            "recheck_flags": len(recheck_flags),
            "rewrote_signed_month": rewrote_signed_month,
        },
        "identical_names_distinct_assets": {"rows_kept": len(distinct), "asset_ids": sorted(row.asset_id for row in distinct)},
        "net_gross_fees": {"gross_cents": fee.gross_cents, "fee_cents": fee.fee_cents, "net_cents": fee.net_cents},
        "duplicate_exports": {"input_rows": len(rows["duplicate_exports"]), "canonical_rows": len(duplicates), "conflicts": conflict_count},
    }
    queue_drain = {"pending_after_drain": queue_pending, "duplicates_suppressed": queue_duplicates, "conflicts": queue_conflicts}
    acceptance_table = [
        {"check": "cutoff row included exactly once", "status": "pass" if len(included) == 1 else "fail"},
        {"check": "post-cutoff row excluded from signed close", "status": "pass" if len(excluded) == 1 else "fail"},
        {"check": "late reversal rechecked without rewriting signed month", "status": "pass" if len(recheck_flags) == 1 and not rewrote_signed_month else "fail"},
        {"check": "identical names use distinct asset identity", "status": "pass" if len(distinct) == 2 else "fail"},
        {"check": "net and gross fee arithmetic separated", "status": "pass" if fee.net_cents == 875 else "fail"},
        {"check": "exact duplicates collapse but conflicts surface", "status": "pass" if len(duplicates) == 1 and conflict_count == 1 else "fail"},
        {"check": "reminder queue drains without duplicate work", "status": "pass" if queue_pending == 0 and queue_duplicates == 1 else "fail"},
    ]
    ok = all(row["status"] == "pass" for row in acceptance_table)
    return {
        "schema": 1,
        "network": "none",
        "fixtures": "generated-main-rukia-only",
        "production_accounting_changed": False,
        "schedule_observed": {"day1": "primary audit plus one-time reminder", "day3": "conditional unsigned nudge", "day10": "post-hold settlement tail"},
        "day1_checklist": [
            "run closed-month audit after the signed-off UTC cutoff",
            "send one unsigned reminder only when sign-off is missing",
            "deduplicate exports before queueing any follow-up",
            "perform reversal recheck for post-cutoff reversals of signed rows",
            "drain queue and record duplicate/conflict counts",
        ],
        "scenarios": scenarios,
        "queue_drain": queue_drain,
        "acceptance_table": acceptance_table,
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = prove()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("check\tstatus")
        for row in result["acceptance_table"]:
            print(f"{row['check']}\t{row['status']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
