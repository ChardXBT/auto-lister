import concurrent.futures
import copy
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "monthly_pnl_export_package.py"
FIXTURE = ROOT / "fixtures" / "monthly_pnl_export" / "synthetic_month.json"
CORRECTIONS = ROOT / "fixtures" / "monthly_pnl_export" / "manual_corrections.json"


def run_export(out_dir, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out_dir), *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )


def file_hashes(path):
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_corrections():
    return json.loads(CORRECTIONS.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make_inputs(tmp_path, fixture_mutator=None, corrections_mutator=None):
    fixture = copy.deepcopy(load_fixture())
    corrections = copy.deepcopy(load_corrections())
    if fixture_mutator:
        fixture_mutator(fixture)
    if corrections_mutator:
        corrections_mutator(corrections)
    fixture_path = tmp_path / "fixture.json"
    corrections_path = tmp_path / "corrections.json"
    write_json(fixture_path, fixture)
    write_json(corrections_path, corrections)
    return fixture_path, corrections_path


def run_with_inputs(tmp_path, out_path, fixture_mutator=None, corrections_mutator=None):
    fixture_path, corrections_path = make_inputs(tmp_path, fixture_mutator, corrections_mutator)
    return run_export(
        out_path,
        "--fixture",
        str(fixture_path),
        "--corrections",
        str(corrections_path),
        "--json",
    )


class MonthlyPnlExportPackageTests(unittest.TestCase):
    def test_normal_entry_point_writes_deterministic_portable_package(self):
        self.assertTrue(SCRIPT.exists(), "synthetic export tool is missing")
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = Path(first_tmp) / "package"
            second = Path(second_tmp) / "package"
            run_one = run_export(first, "--json")
            run_two = run_export(second, "--json")
            self.assertEqual(run_one.returncode, 0, run_one.stderr)
            self.assertEqual(run_two.returncode, 0, run_two.stderr)
            summary_one = json.loads(run_one.stdout)
            summary_two = json.loads(run_two.stdout)
            self.assertEqual(summary_one["totals"], summary_two["totals"])
            self.assertEqual(file_hashes(first), file_hashes(second))
            self.assertEqual(summary_one["network"], "none")
            self.assertFalse(summary_one["production_accounting_changed"])
            self.assertFalse(summary_one["real_exports_or_accounts_used"])

            rows = read_csv(first / "rows.csv")
            corrections = read_csv(first / "reviewer_corrections.csv")
            comparison = read_csv(first / "original_vs_amended.csv")
            replacements = json.loads((first / "reversible_replacements.json").read_text(encoding="utf-8"))
            totals = json.loads((first / "totals.json").read_text(encoding="utf-8"))

            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0]["row_id"], "sale-001")
            self.assertLess(rows[0]["occurred_at"], rows[-1]["occurred_at"])
            self.assertTrue(any(int(row["amount_cents"]) < 0 for row in rows))
            self.assertTrue(any(row["amount_cents"] == "0" for row in rows))
            self.assertTrue(any("★" in row["item_name"] for row in rows))
            self.assertTrue(any(row["gross_cents"] == str(2**40) for row in rows))
            self.assertEqual(len(corrections), 1)
            correction = corrections[0]
            self.assertEqual(correction["field"], "carry_cost_cents")
            self.assertNotEqual(correction["original_value"], correction["amended_value"])
            self.assertTrue(correction["evidence_refs"].startswith("evidence/"))
            corrected = next(row for row in comparison if row["row_id"] == correction["row_id"])
            self.assertEqual(corrected["original_carry_cost_cents"], correction["original_value"])
            self.assertEqual(corrected["amended_carry_cost_cents"], correction["amended_value"])
            self.assertIn("reverse-and-replace", replacements["method"])
            self.assertEqual(len(replacements["corrections"]), 1)
            reversal, replacement = replacements["corrections"][0]["replacement_rows"]
            self.assertTrue(reversal["reversible"])
            self.assertTrue(replacement["reversible"])
            self.assertEqual(totals["original_total_cents"] + totals["correction_delta_cents"], totals["amended_total_cents"])

            golden = ROOT / "fixtures" / "monthly_pnl_export" / "package"
            self.assertEqual(file_hashes(first), file_hashes(golden))
            recorded = json.loads((first / "checksums.json").read_text(encoding="utf-8"))["files"]
            actual = {
                item.relative_to(first).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                for item in sorted(first.rglob("*"))
                if item.is_file() and item.name != "checksums.json"
            }
            self.assertEqual(recorded, actual)

    def test_reversible_adjustments_restore_original_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "package"
            run = run_export(out, "--json")
            self.assertEqual(run.returncode, 0, run.stderr)
            totals = json.loads((out / "totals.json").read_text(encoding="utf-8"))
            replacements = json.loads((out / "reversible_replacements.json").read_text(encoding="utf-8"))
            adjustments = replacements["corrections"][0]["replacement_rows"]
            forward_delta = sum(item["signed_delta_cents"] for item in adjustments)
            inverse_delta = sum(item["inverse_signed_delta_cents"] for item in adjustments)
            self.assertEqual(forward_delta, totals["correction_delta_cents"])
            self.assertEqual(forward_delta + inverse_delta, 0)
            self.assertEqual(totals["original_total_cents"] + forward_delta, totals["amended_total_cents"])
            self.assertEqual(totals["original_total_cents"] + forward_delta + inverse_delta, totals["original_total_cents"])

    def test_package_has_no_absolute_paths_and_evidence_refs_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "package"
            run = run_export(out, "--json")
            self.assertEqual(run.returncode, 0, run.stderr)
            for file in out.rglob("*"):
                if file.is_file():
                    content = file.read_text(encoding="utf-8")
                    self.assertNotIn(str(out), content)
                    self.assertNotIn(str(ROOT), content)
                    self.assertNotIn("\x00", content)
            rows = read_csv(out / "rows.csv")
            for row in rows:
                for ref in row["evidence_refs"].split(";"):
                    evidence_path = ref.split("#", 1)[0]
                    self.assertTrue((out / evidence_path).is_file(), ref)

    def test_unknown_correction_row_fails_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "package"

            def bad_correction(data):
                data["corrections"][0]["row_id"] = "missing-row"

            run = run_with_inputs(tmp_path, out, corrections_mutator=bad_correction)
            self.assertEqual(run.returncode, 2)
            self.assertIn("unknown row_id", run.stderr)
            self.assertFalse(out.exists())

    def test_mismatched_original_is_atomic_and_retry_after_fix_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "package"
            fixture_path, corrections_path = make_inputs(
                tmp_path,
                corrections_mutator=lambda data: data["corrections"][0].update(original_value=4199),
            )
            bad = run_export(out, "--fixture", str(fixture_path), "--corrections", str(corrections_path), "--json")
            self.assertEqual(bad.returncode, 2)
            self.assertIn("original value does not match", bad.stderr)
            self.assertFalse(out.exists())

            corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
            corrections["corrections"][0]["original_value"] = 4200
            write_json(corrections_path, corrections)
            good = run_export(out, "--fixture", str(fixture_path), "--corrections", str(corrections_path), "--json")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertTrue((out / "totals.json").is_file())

    def test_duplicate_rows_and_duplicate_correction_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def duplicate_row(data):
                data["rows"].append(copy.deepcopy(data["rows"][0]))

            duplicate_row_run = run_with_inputs(tmp_path, tmp_path / "dup-row", fixture_mutator=duplicate_row)
            self.assertEqual(duplicate_row_run.returncode, 2)
            self.assertIn("duplicate row_id", duplicate_row_run.stderr)
            self.assertFalse((tmp_path / "dup-row").exists())

            def duplicate_target(data):
                second = copy.deepcopy(data["corrections"][0])
                second["correction_id"] = "corr-carry-002"
                second["amended_value"] = 4600
                data["corrections"].append(second)

            duplicate_target_run = run_with_inputs(tmp_path, tmp_path / "dup-target", corrections_mutator=duplicate_target)
            self.assertEqual(duplicate_target_run.returncode, 2)
            self.assertIn("duplicate correction target", duplicate_target_run.stderr)
            self.assertFalse((tmp_path / "dup-target").exists())

    def test_empty_or_missing_row_list_is_rejected(self):
        for mutate in (lambda data: data.update(rows=[]), lambda data: data.pop("rows")):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=mutate)
                self.assertEqual(run.returncode, 2)
                self.assertIn("rows", run.stderr)
                self.assertFalse((tmp_path / "package").exists())

    def test_timezone_offsets_and_out_of_month_rows_are_rejected(self):
        cases = [
            ("2026-02-03T12:00:00+00:00", "UTC Z time"),
            ("2026-03-01T00:00:00Z", "outside fixture month"),
        ]
        for occurred_at, message in cases:
            with self.subTest(occurred_at=occurred_at), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def mutate(data):
                    data["rows"][1]["occurred_at"] = occurred_at

                run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=mutate)
                self.assertEqual(run.returncode, 2)
                self.assertIn(message, run.stderr)
                self.assertFalse((tmp_path / "package").exists())

    def test_non_integer_boolean_and_out_of_range_cents_are_rejected(self):
        cases = [(None, "integer"), (1.5, "integer"), (False, "integer"), (2**63, "signed 64-bit")]
        for value, message in cases:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def mutate(data):
                    data["rows"][1]["gross_cents"] = value

                run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=mutate)
                self.assertEqual(run.returncode, 2)
                self.assertIn(message, run.stderr)
                self.assertFalse((tmp_path / "package").exists())

    def test_missing_duplicate_or_nonportable_evidence_is_rejected(self):
        cases = [
            ("evidence/missing.md", "missing evidence"),
            ("../outside.md", "below evidence"),
            ("C:\\evidence\\synthetic-month.md", "portable relative"),
        ]
        for ref, message in cases:
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                def mutate(data):
                    data["rows"][1]["evidence_refs"] = [ref]

                run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=mutate)
                self.assertEqual(run.returncode, 2)
                self.assertIn(message, run.stderr)
                self.assertFalse((tmp_path / "package").exists())

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def duplicate_refs(data):
                data["rows"][1]["evidence_refs"] = [
                    "evidence/synthetic-month.md#sale-002",
                    "evidence/synthetic-month.md#sale-002",
                ]

            run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=duplicate_refs)
            self.assertEqual(run.returncode, 2)
            self.assertIn("duplicate evidence", run.stderr)



    def test_json_integer_beyond_parser_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_path = tmp_path / "fixture.json"
            corrections_path = tmp_path / "corrections.json"
            fixture_text = FIXTURE.read_text(encoding="utf-8").replace("125000", "9" * 5000, 1)
            fixture_path.write_text(fixture_text, encoding="utf-8")
            corrections_path.write_text(CORRECTIONS.read_text(encoding="utf-8"), encoding="utf-8")
            run = run_export(
                tmp_path / "package",
                "--fixture",
                str(fixture_path),
                "--corrections",
                str(corrections_path),
                "--json",
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("cannot read fixture.json", run.stderr)
            self.assertNotIn("Traceback", run.stderr)
            self.assertFalse((tmp_path / "package").exists())

    def test_missing_evidence_anchor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def missing_anchor(data):
                data["rows"][0]["evidence_refs"] = ["evidence/synthetic-month.md#does-not-exist"]

            run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=missing_anchor)
            self.assertEqual(run.returncode, 2)
            self.assertIn("missing evidence anchor", run.stderr)
            self.assertFalse((tmp_path / "package").exists())

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_path = tmp_path / "fixture.json"
            corrections_path = tmp_path / "corrections.json"
            fixture_text = FIXTURE.read_text(encoding="utf-8")
            duplicate_schema = fixture_text.replace(
                '"schema": "synthetic-pnl-export-fixture/v1",',
                '"schema": "synthetic-pnl-export-fixture/v1", "schema": "synthetic-pnl-export-fixture/v1",',
                1,
            )
            fixture_path.write_text(duplicate_schema, encoding="utf-8")
            corrections_path.write_text(CORRECTIONS.read_text(encoding="utf-8"), encoding="utf-8")
            run = run_export(
                tmp_path / "package",
                "--fixture",
                str(fixture_path),
                "--corrections",
                str(corrections_path),
                "--json",
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("duplicate JSON key", run.stderr)
            self.assertFalse((tmp_path / "package").exists())

    def test_concurrent_independent_runs_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outs = [base / "one", base / "two", base / "three"]
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                runs = list(executor.map(lambda out: run_export(out, "--json"), outs))
            self.assertTrue(all(run.returncode == 0 for run in runs), [run.stderr for run in runs])
            summaries = [json.loads(run.stdout) for run in runs]
            self.assertEqual([item["totals"] for item in summaries], [summaries[0]["totals"]] * 3)
            self.assertEqual([file_hashes(out) for out in outs], [file_hashes(outs[0])] * 3)

    def test_rerun_in_prior_package_directory_is_stable_and_unrelated_dir_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "package"
            first = run_export(out, "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_hashes = file_hashes(out)
            second = run_export(out, "--json")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(file_hashes(out), first_hashes)

            unrelated = Path(tmp) / "unrelated"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("do not overwrite", encoding="utf-8")
            refused = run_export(unrelated, "--json")
            self.assertEqual(refused.returncode, 2)
            self.assertIn("not empty", refused.stderr)
            self.assertEqual((unrelated / "keep.txt").read_text(encoding="utf-8"), "do not overwrite")


    def test_rows_sort_by_timestamp_value_not_timestamp_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def fractional_second(data):
                data["rows"][2]["occurred_at"] = "2026-02-03T12:00:00.500Z"

            run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=fractional_second)
            self.assertEqual(run.returncode, 0, run.stderr)
            rows = read_csv(tmp_path / "package" / "rows.csv")
            row_ids = [row["row_id"] for row in rows]
            self.assertLess(row_ids.index("sale-001"), row_ids.index("sale-002"))

    def test_non_ascii_account_alias_is_rejected_as_non_portable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def unicode_alias(data):
                detail = data["account_aliases"]["amber-otter"]
                data["account_aliases"]["amber-öttër"] = detail
                data["rows"][0]["account_alias"] = "amber-öttër"

            run = run_with_inputs(tmp_path, tmp_path / "package", fixture_mutator=unicode_alias)
            self.assertEqual(run.returncode, 2)
            self.assertIn("lowercase ASCII letters", run.stderr)
            self.assertFalse((tmp_path / "package").exists())

    def test_evidence_document_paths_must_be_canonical_files_below_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def directory_evidence(data):
                content = data["evidence_documents"].pop("evidence/synthetic-month.md")
                data["evidence_documents"]["evidence/"] = content
                for row in data["rows"]:
                    row["evidence_refs"] = ["evidence/#row"]

            def correction_refs(data):
                data["corrections"][0]["evidence_refs"] = ["evidence/#correction"]

            run = run_with_inputs(
                tmp_path,
                tmp_path / "package",
                fixture_mutator=directory_evidence,
                corrections_mutator=correction_refs,
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("canonical file path", run.stderr)
            self.assertFalse((tmp_path / "package").exists())

    def test_prior_package_with_extra_file_is_refused_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "package"
            first = run_export(out, "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            extra = out / "reviewer-notes.txt"
            extra.write_text("not generated", encoding="utf-8")
            second = run_export(out, "--json")
            self.assertEqual(second.returncode, 2)
            self.assertIn("not generated", second.stderr)
            self.assertEqual(extra.read_text(encoding="utf-8"), "not generated")


    def test_symlink_output_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "target"
            target.mkdir()
            out = tmp_path / "package"
            try:
                os.symlink(target, out, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            run = run_export(out, "--json")
            self.assertEqual(run.returncode, 2)
            self.assertIn("symbolic link", run.stderr)
            self.assertTrue(out.is_symlink())
            self.assertEqual(list(target.iterdir()), [])

    def test_interrupted_write_leaves_prior_package_unchanged(self):
        spec = importlib.util.spec_from_file_location("monthly_pnl_export_package", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "package"
            out.mkdir()
            manifest = json.dumps({"schema": "synthetic-pnl-export-package/v1"}).encode("utf-8")
            (out / "manifest.json").write_bytes(manifest)
            real_write_bytes = Path.write_bytes

            def crashing_write_bytes(path, data):
                if path.name == "README.txt":
                    raise OSError("simulated crash during write")
                return real_write_bytes(path, data)

            files = {
                "README.txt": b"new readme\n",
                "manifest.json": json.dumps({"schema": "synthetic-pnl-export-package/v1", "new": True}).encode("utf-8"),
            }
            with mock.patch.object(Path, "write_bytes", crashing_write_bytes):
                with self.assertRaises(OSError):
                    module.write_package(out, files)
            self.assertTrue((out / "manifest.json").is_file())
            self.assertEqual((out / "manifest.json").read_bytes(), manifest)
            self.assertFalse((out / "README.txt").exists())

    def test_concurrent_runs_to_same_output_are_serialized_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "package"
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                runs = list(executor.map(lambda _: run_export(out, "--json"), range(4)))
            self.assertTrue(all(run.returncode == 0 for run in runs), [run.stderr for run in runs])
            golden = ROOT / "fixtures" / "monthly_pnl_export" / "package"
            self.assertEqual(file_hashes(out), file_hashes(golden))

    def test_empty_correction_list_is_valid_but_schema_must_be_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture_path, corrections_path = make_inputs(
                tmp_path, corrections_mutator=lambda data: data.update(corrections=[])
            )
            empty = run_export(out := tmp_path / "empty", "--fixture", str(fixture_path), "--corrections", str(corrections_path), "--json")
            self.assertEqual(empty.returncode, 0, empty.stderr)
            totals = json.loads((out / "totals.json").read_text(encoding="utf-8"))
            self.assertEqual(totals["correction_count"], 0)
            self.assertEqual(totals["correction_delta_cents"], 0)

            corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
            corrections["unexpected"] = True
            write_json(corrections_path, corrections)
            invalid = run_export(tmp_path / "invalid", "--fixture", str(fixture_path), "--corrections", str(corrections_path), "--json")
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("unknown top-level keys", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
