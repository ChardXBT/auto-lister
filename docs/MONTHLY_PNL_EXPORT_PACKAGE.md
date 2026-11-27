# Synthetic monthly P&L export package

This is a portable reviewer-handoff format built only from generated fixtures. It does not read or produce real exports, corrections, uploads, account operations, bot queue changes, production thresholds, encrypted vendor files, network traffic or secrets.

The fixture month is February 2026. The aliases `amber-otter` and `blue-fox` are fictional and intentionally unrelated to real account names.

## Normal entry point

```bash
python3 tools/monthly_pnl_export_package.py --out /tmp/synthetic-pnl-package --json
```

Default inputs are plaintext generated fixtures:

- `fixtures/monthly_pnl_export/synthetic_month.json`
- `fixtures/monthly_pnl_export/manual_corrections.json`

A reviewer can copy `manual_corrections.json`, amend the generated correction, and pass the copy with `--corrections PATH`. Validation is fail-closed: unknown rows, mismatched original values, duplicate correction targets, duplicate JSON keys, non-integer cents, missing evidence documents or anchors, non-portable aliases or evidence paths, non-UTC timestamps and out-of-month rows are rejected before output is written.

The reviewed example package is committed at `fixtures/monthly_pnl_export/package/`. It is generated output, not real export data. Regenerate it after any intentional fixture or format change with the normal entry point above, using that directory as `--out`.

## Package contents

- `manifest.json` — schema, deterministic generation time, input fixture names and payload-file hashes.
- `rows.csv` — immutable original rows, sorted by UTC time and row id.
- `reviewer_corrections.csv` — reviewer correction, original value, amended value, reason and evidence references.
- `original_vs_amended.csv` — row-level before/after gross, fee, carry-cost and net cents.
- `reversible_replacements.json` — reverse-and-replace adjustment entries. Source rows are never rewritten. Every adjustment carries its inverse signed delta.
- `totals.json` — original total, amended total and correction delta in integer cents.
- `checksums.json` — SHA-256 checksums for the portable files.
- `evidence/` — generated evidence documents referenced by relative portable links.

Evidence references use relative POSIX paths such as `evidence/synthetic-month.md#carry-cost-revision`; they never contain local absolute paths.

## Manual carry-cost revision

The generated correction `corr-carry-001` changes fictional row `sale-003` carry cost from 4200 to 4500 cents. Because carry cost subtracts from net P&L, the amended total is 300 cents lower than the original total. `reversible_replacements.json` explains this as two reversible adjustments:

1. reverse the original carry-cost contribution (`+4200` cents to net);
2. apply the amended carry-cost contribution (`-4500` cents to net).

The net correction delta is `-300` cents. Reversing those signed deltas restores the original total. The original row remains visible in both `rows.csv` and `original_vs_amended.csv`, so the correction is explainable instead of overwritten.

## Determinism

The input fixture carries a fixed `generated_at`; the tool does not use the wall clock. Rows sort by parsed UTC timestamp rather than timestamp text, corrections are sorted, JSON keys are sorted, CSV line endings are fixed, floating point is never used, and output content has no absolute paths. Rerunning the normal entry point into two empty directories therefore produces identical file hashes and identical totals.

Writes are serialized with a sibling lock file and built in a sibling staging directory. The prior package is swapped aside only after the replacement is complete, so an interrupted run never leaves a mixed package; if the interruption happens during the swap, a later run restores or removes the backup before rebuilding. A non-empty output directory is accepted only when it contains exactly the generated files of a prior synthetic package and their recorded SHA-256 checksums still match, so unrelated, edited or corrupt reviewer files are never overwritten.

## Boundary and relation to prior work

This proof is independent of the encrypted monthly reminder, exact-cost ingestion, monthly checker and vendor implementation. It does not decrypt or inspect those files. The overlapping prior draft PR #39 covered fixture acceptance for reminders and queue draining; this package instead covers reviewer corrections, evidence references, original-versus-amended export values and reversible replacements. No live execution path is changed.
