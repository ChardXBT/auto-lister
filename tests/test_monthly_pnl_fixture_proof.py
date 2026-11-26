import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "monthly_pnl_fixture_proof.py"


def proof():
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    return run, json.loads(run.stdout) if run.stdout else {}


class MonthlyPnlFixtureProofTests(unittest.TestCase):
    def test_generated_fixture_matrix_passes_offline(self):
        run, data = proof()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(data["network"], "none")
        self.assertTrue(data["ok"])
        self.assertTrue(all(row["status"] == "pass" for row in data["acceptance_table"]))

    def test_cutoff_overlap_and_late_reversal_are_separate(self):
        run, data = proof()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(data["scenarios"]["cutoff_overlap"]["included_at_cutoff"], 1)
        self.assertEqual(data["scenarios"]["cutoff_overlap"]["excluded_after_cutoff"], 1)
        reversal = data["scenarios"]["late_reversal"]
        self.assertEqual(reversal["signed_month_rows"], 1)
        self.assertEqual(reversal["recheck_flags"], 1)
        self.assertFalse(reversal["rewrote_signed_month"])

    def test_identity_dedup_and_fee_modes(self):
        run, data = proof()
        self.assertEqual(run.returncode, 0, run.stderr)
        names = data["scenarios"]["identical_names_distinct_assets"]
        self.assertEqual(names["rows_kept"], 2)
        dedup = data["scenarios"]["duplicate_exports"]
        self.assertEqual(dedup["input_rows"], 3)
        self.assertEqual(dedup["canonical_rows"], 1)
        self.assertEqual(dedup["conflicts"], 1)
        fees = data["scenarios"]["net_gross_fees"]
        self.assertEqual(fees["gross_cents"], 1000)
        self.assertEqual(fees["fee_cents"], 125)
        self.assertEqual(fees["net_cents"], 875)

    def test_first_day_checklist_is_unsigned_and_drains_queue(self):
        run, data = proof()
        self.assertEqual(run.returncode, 0, run.stderr)
        checklist = data["day1_checklist"]
        self.assertTrue(any("unsigned" in item for item in checklist))
        self.assertTrue(any("reversal recheck" in item for item in checklist))
        self.assertEqual(data["queue_drain"]["pending_after_drain"], 0)
        self.assertEqual(data["queue_drain"]["duplicates_suppressed"], 1)


if __name__ == "__main__":
    unittest.main()
