#!/usr/bin/env python3
"""Build a deterministic synthetic monthly P&L reviewer export package.

The tool uses only generated fixture input. It never reads production exports,
encrypted files, account data, network resources or secrets.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import string
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures" / "monthly_pnl_export" / "synthetic_month.json"
DEFAULT_CORRECTIONS = ROOT / "fixtures" / "monthly_pnl_export" / "manual_corrections.json"
FIXTURE_SCHEMA = "synthetic-pnl-export-fixture/v1"
CORRECTIONS_SCHEMA = "synthetic-pnl-review-corrections/v1"
PACKAGE_SCHEMA = "synthetic-pnl-export-package/v1"
CENT_FIELDS = ("gross_cents", "fee_cents", "carry_cost_cents")
FIELD_SIGNS = {"gross_cents": 1, "fee_cents": -1, "carry_cost_cents": -1}
ROW_KEYS = {
    "row_id",
    "occurred_at",
    "account_alias",
    "item_name",
    "kind",
    "gross_cents",
    "fee_cents",
    "carry_cost_cents",
    "evidence_refs",
}
CORRECTION_KEYS = {
    "correction_id",
    "row_id",
    "field",
    "original_value",
    "amended_value",
    "reason",
    "reviewer",
    "evidence_refs",
}
GENERATED_TOP_LEVEL = {
    "README.txt",
    "manifest.json",
    "rows.csv",
    "original_vs_amended.csv",
    "reviewer_corrections.csv",
    "reversible_replacements.json",
    "totals.json",
    "checksums.json",
}
ALLOWED_OUTPUT_TOP_LEVEL = GENERATED_TOP_LEVEL | {"evidence"}


class ValidationError(ValueError):
    pass


def fail(message: str) -> ValidationError:
    return ValidationError(message)


def json_object_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise fail(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=json_object_without_duplicates)
    except ValidationError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise fail(f"cannot read {path.name}: {exc}") from exc
    except ValueError as exc:
        raise fail(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise fail(f"{path.name} must contain a JSON object")
    return value


def require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise fail(f"{name} must be non-empty text")
    if any(ord(char) < 32 for char in value):
        raise fail(f"{name} must not contain control characters")
    return value


def require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(f"{name} must be an integer number of cents")
    if abs(value) > 2**63 - 1:
        raise fail(f"{name} exceeds signed 64-bit cents range")
    return value


def parse_utc(value: object, name: str) -> datetime:
    text = require_text(value, name)
    if not text.endswith("Z"):
        raise fail(f"{name} must use UTC Z time")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise fail(f"{name} is not a valid ISO timestamp") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise fail(f"{name} must be UTC")
    return parsed


def validate_evidence_path(path_text: str, name: str) -> None:
    path = PurePosixPath(path_text)
    if not path_text.startswith("evidence/") or ".." in path.parts:
        raise fail(f"{name} must stay below evidence/")
    if path.as_posix() != path_text or not path.name:
        raise fail(f"{name} must be a canonical file path below evidence/")


def markdown_anchor(text: str) -> str:
    normalized = re.sub(r"[^\w\- ]", "", text.strip().lower(), flags=re.UNICODE)
    return normalized.replace(" ", "-")


def markdown_anchors(content: str) -> set[str]:
    anchors = set()
    for line in content.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            anchors.add(markdown_anchor(match.group(1)))
    return anchors


def validate_evidence_ref(ref: object, evidence_anchors: dict[str, set[str]], name: str) -> str:
    text = require_text(ref, name)
    if "\\" in text or text.startswith("/") or "://" in text:
        raise fail(f"{name} must be a portable relative POSIX path")
    path_text = text.split("#", 1)[0]
    validate_evidence_path(path_text, name)
    if path_text not in evidence_anchors:
        raise fail(f"{name} references missing evidence document {path_text}")
    if "#" in text:
        anchor = text.split("#", 1)[1]
        if not anchor or anchor not in evidence_anchors[path_text]:
            raise fail(f"{name} references missing evidence anchor {anchor!r}")
    return text


def validate_evidence_refs(value: object, evidence_anchors: dict[str, set[str]], name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise fail(f"{name} must be a non-empty list")
    refs = [validate_evidence_ref(ref, evidence_anchors, f"{name}[{index}]") for index, ref in enumerate(value)]
    if len(set(refs)) != len(refs):
        raise fail(f"{name} contains duplicate evidence references")
    return refs


def month_bounds(month: str) -> tuple[datetime, datetime]:
    if len(month) != 7 or month[4] != "-":
        raise fail("month must use YYYY-MM")
    try:
        year = int(month[:4])
        number = int(month[5:])
        start = datetime(year, number, 1, tzinfo=timezone.utc)
        if number == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, number + 1, 1, tzinfo=timezone.utc)
    except ValueError as exc:
        raise fail("month must use a valid YYYY-MM value") from exc
    return start, end


def validate_fixture(data: dict) -> dict:
    if data.get("schema") != FIXTURE_SCHEMA:
        raise fail(f"fixture schema must be {FIXTURE_SCHEMA}")
    allowed = {"schema", "month", "generated_at", "account_aliases", "evidence_documents", "rows"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise fail(f"fixture has unknown keys: {', '.join(unknown)}")
    month = require_text(data.get("month"), "month")
    start, end = month_bounds(month)
    generated_at = parse_utc(data.get("generated_at"), "generated_at")
    aliases = data.get("account_aliases")
    if not isinstance(aliases, dict) or not aliases:
        raise fail("account_aliases must be a non-empty object")
    for alias, detail in aliases.items():
        require_text(alias, "account alias")
        portable_alias_chars = string.ascii_lowercase + string.digits + "-"
        if not all(char in portable_alias_chars for char in alias):
            raise fail(f"account alias {alias!r} must use lowercase ASCII letters, digits and hyphens")
        if not isinstance(detail, dict) or "fictional" not in str(detail.get("description", "")):
            raise fail(f"account alias {alias!r} must be explicitly described as fictional")
    evidence = data.get("evidence_documents")
    if not isinstance(evidence, dict) or not evidence:
        raise fail("evidence_documents must be a non-empty object")
    evidence_paths: set[str] = set()
    evidence_anchors: dict[str, set[str]] = {}
    for path_text, content in evidence.items():
        if "\\" in path_text or path_text.startswith("/") or "://" in path_text:
            raise fail("evidence document paths must be portable relative POSIX paths")
        validate_evidence_path(path_text, "evidence document path")
        if path_text in evidence_paths:
            raise fail(f"duplicate evidence document {path_text}")
        if not isinstance(content, str) or "Synthetic" not in content or "real" not in content:
            raise fail(f"evidence document {path_text} must identify generated non-real evidence")
        evidence_paths.add(path_text)
        evidence_anchors[path_text] = markdown_anchors(content)

    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise fail("rows must be a non-empty list")
    rows = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_rows):
        name = f"rows[{index}]"
        if not isinstance(raw, dict):
            raise fail(f"{name} must be an object")
        unknown_row_keys = sorted(set(raw) - ROW_KEYS)
        missing = sorted(ROW_KEYS - set(raw))
        if unknown_row_keys or missing:
            parts = []
            if missing:
                parts.append("missing " + ",".join(missing))
            if unknown_row_keys:
                parts.append("unknown " + ",".join(unknown_row_keys))
            raise fail(f"{name} has invalid keys ({'; '.join(parts)})")
        row = {
            "row_id": require_text(raw["row_id"], f"{name}.row_id"),
            "occurred_at": require_text(raw["occurred_at"], f"{name}.occurred_at"),
            "account_alias": require_text(raw["account_alias"], f"{name}.account_alias"),
            "item_name": require_text(raw["item_name"], f"{name}.item_name"),
            "kind": require_text(raw["kind"], f"{name}.kind"),
            "evidence_refs": validate_evidence_refs(raw["evidence_refs"], evidence_anchors, f"{name}.evidence_refs"),
        }
        if row["row_id"] in seen_ids:
            raise fail(f"duplicate row_id {row['row_id']}")
        seen_ids.add(row["row_id"])
        if row["account_alias"] not in aliases:
            raise fail(f"{name}.account_alias is not declared")
        occurred = parse_utc(row["occurred_at"], f"{name}.occurred_at")
        if not start <= occurred < end:
            raise fail(f"{name}.occurred_at is outside fixture month {month}")
        for field in CENT_FIELDS:
            row[field] = require_int(raw[field], f"{name}.{field}")
        row["net_cents"] = row["gross_cents"] - row["fee_cents"] - row["carry_cost_cents"]
        rows.append((occurred, row))
    rows.sort(key=lambda item: (item[0], item[1]["row_id"]))
    rows = [row for _, row in rows]
    return {
        "schema": data["schema"],
        "month": month,
        "generated_at": data["generated_at"],
        "generated_at_dt": generated_at,
        "account_aliases": aliases,
        "evidence_documents": evidence,
        "evidence_paths": evidence_paths,
        "evidence_anchors": evidence_anchors,
        "rows": rows,
    }


def validate_corrections(data: dict, fixture: dict) -> list[dict]:
    if data.get("schema") != CORRECTIONS_SCHEMA:
        raise fail(f"corrections schema must be {CORRECTIONS_SCHEMA}")
    if sorted(set(data) - {"schema", "corrections"}):
        raise fail("corrections file has unknown top-level keys")
    raw_corrections = data.get("corrections")
    if not isinstance(raw_corrections, list):
        raise fail("corrections must be a list")
    rows_by_id = {row["row_id"]: row for row in fixture["rows"]}
    seen_ids: set[str] = set()
    seen_targets: set[tuple[str, str]] = set()
    corrections = []
    for index, raw in enumerate(raw_corrections):
        name = f"corrections[{index}]"
        if not isinstance(raw, dict):
            raise fail(f"{name} must be an object")
        if set(raw) != CORRECTION_KEYS:
            raise fail(f"{name} must contain exactly the documented correction keys")
        correction = {
            "correction_id": require_text(raw["correction_id"], f"{name}.correction_id"),
            "row_id": require_text(raw["row_id"], f"{name}.row_id"),
            "field": require_text(raw["field"], f"{name}.field"),
            "original_value": require_int(raw["original_value"], f"{name}.original_value"),
            "amended_value": require_int(raw["amended_value"], f"{name}.amended_value"),
            "reason": require_text(raw["reason"], f"{name}.reason"),
            "reviewer": require_text(raw["reviewer"], f"{name}.reviewer"),
            "evidence_refs": validate_evidence_refs(
                raw["evidence_refs"], fixture["evidence_anchors"], f"{name}.evidence_refs"
            ),
        }
        if correction["correction_id"] in seen_ids:
            raise fail(f"duplicate correction_id {correction['correction_id']}")
        seen_ids.add(correction["correction_id"])
        target = (correction["row_id"], correction["field"])
        if target in seen_targets:
            raise fail(f"duplicate correction target {correction['row_id']}:{correction['field']}")
        seen_targets.add(target)
        if correction["row_id"] not in rows_by_id:
            raise fail(f"correction references unknown row_id {correction['row_id']}")
        if correction["field"] not in FIELD_SIGNS:
            raise fail(f"correction field {correction['field']} is not a correctable cents field")
        row = rows_by_id[correction["row_id"]]
        if row[correction["field"]] != correction["original_value"]:
            raise fail(
                f"correction {correction['correction_id']} original value does not match row {correction['row_id']}"
            )
        if correction["original_value"] == correction["amended_value"]:
            raise fail(f"correction {correction['correction_id']} does not amend the value")
        sign = FIELD_SIGNS[correction["field"]]
        correction["delta_cents"] = sign * (correction["amended_value"] - correction["original_value"])
        corrections.append(correction)
    corrections.sort(key=lambda item: item["correction_id"])
    return corrections


def corrected_rows(rows: list[dict], corrections: list[dict]) -> list[dict]:
    by_target = {(item["row_id"], item["field"]): item for item in corrections}
    corrected = []
    for row in rows:
        amended = dict(row)
        correction_ids = []
        for field in CENT_FIELDS:
            correction = by_target.get((row["row_id"], field))
            if correction:
                amended[field] = correction["amended_value"]
                correction_ids.append(correction["correction_id"])
        amended["net_cents"] = amended["gross_cents"] - amended["fee_cents"] - amended["carry_cost_cents"]
        amended["correction_ids"] = correction_ids
        corrected.append(amended)
    return corrected


def sum_field(rows: list[dict], field: str) -> int:
    return sum(row[field] for row in rows)


def totals_for(rows: list[dict]) -> dict:
    return {
        "gross_cents": sum_field(rows, "gross_cents"),
        "fee_cents": sum_field(rows, "fee_cents"),
        "carry_cost_cents": sum_field(rows, "carry_cost_cents"),
        "total_cents": sum_field(rows, "net_cents"),
    }


def csv_text(fieldnames: list[str], rows: list[dict]) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def join_refs(refs: list[str]) -> str:
    return ";".join(refs)


def build_replacements(corrections: list[dict]) -> dict:
    packaged = []
    for correction in corrections:
        sign = FIELD_SIGNS[correction["field"]]
        reverse_delta = -sign * correction["original_value"]
        replacement_delta = sign * correction["amended_value"]
        packaged.append(
            {
                "correction_id": correction["correction_id"],
                "row_id": correction["row_id"],
                "field": correction["field"],
                "original_value": correction["original_value"],
                "amended_value": correction["amended_value"],
                "net_delta_cents": correction["delta_cents"],
                "explanation": correction["reason"],
                "evidence_refs": correction["evidence_refs"],
                "replacement_rows": [
                    {
                        "adjustment_id": f"{correction['correction_id']}-reverse-original",
                        "action": "reverse_original_field_contribution",
                        "signed_delta_cents": reverse_delta,
                        "reversible": True,
                        "inverse_signed_delta_cents": -reverse_delta,
                    },
                    {
                        "adjustment_id": f"{correction['correction_id']}-apply-amended",
                        "action": "apply_amended_field_contribution",
                        "signed_delta_cents": replacement_delta,
                        "reversible": True,
                        "inverse_signed_delta_cents": -replacement_delta,
                    },
                ],
            }
        )
    return {
        "schema": PACKAGE_SCHEMA,
        "method": "reverse-and-replace",
        "mutable_source_rows": False,
        "description": "Original fixture rows remain immutable. Each correction is represented by a reversing adjustment and an amended replacement adjustment; each adjustment carries its inverse signed delta.",
        "corrections": packaged,
    }


def build_package(fixture: dict, corrections: list[dict], fixture_path: Path, corrections_path: Path) -> tuple[dict[str, bytes], dict]:
    rows = fixture["rows"]
    amended_rows = corrected_rows(rows, corrections)
    original_totals = totals_for(rows)
    amended_totals = totals_for(amended_rows)
    correction_delta = amended_totals["total_cents"] - original_totals["total_cents"]
    totals = {
        "schema": PACKAGE_SCHEMA,
        "month": fixture["month"],
        "row_count": len(rows),
        "correction_count": len(corrections),
        "original_gross_cents": original_totals["gross_cents"],
        "original_fee_cents": original_totals["fee_cents"],
        "original_carry_cost_cents": original_totals["carry_cost_cents"],
        "original_total_cents": original_totals["total_cents"],
        "amended_gross_cents": amended_totals["gross_cents"],
        "amended_fee_cents": amended_totals["fee_cents"],
        "amended_carry_cost_cents": amended_totals["carry_cost_cents"],
        "amended_total_cents": amended_totals["total_cents"],
        "correction_delta_cents": correction_delta,
        "currency_minor_unit": "cents",
        "deterministic_generated_at": fixture["generated_at"],
    }

    row_csv_rows = []
    comparison_rows = []
    for row, amended in zip(rows, amended_rows):
        base = {
            "row_id": row["row_id"],
            "occurred_at": row["occurred_at"],
            "account_alias": row["account_alias"],
            "item_name": row["item_name"],
            "kind": row["kind"],
            "gross_cents": row["gross_cents"],
            "fee_cents": row["fee_cents"],
            "carry_cost_cents": row["carry_cost_cents"],
            "amount_cents": row["net_cents"],
            "evidence_refs": join_refs(row["evidence_refs"]),
        }
        row_csv_rows.append(base)
        comparison_rows.append(
            {
                "row_id": row["row_id"],
                "account_alias": row["account_alias"],
                "item_name": row["item_name"],
                "original_gross_cents": row["gross_cents"],
                "amended_gross_cents": amended["gross_cents"],
                "original_fee_cents": row["fee_cents"],
                "amended_fee_cents": amended["fee_cents"],
                "original_carry_cost_cents": row["carry_cost_cents"],
                "amended_carry_cost_cents": amended["carry_cost_cents"],
                "original_net_cents": row["net_cents"],
                "amended_net_cents": amended["net_cents"],
                "delta_cents": amended["net_cents"] - row["net_cents"],
                "correction_ids": ";".join(amended["correction_ids"]),
                "evidence_refs": join_refs(row["evidence_refs"]),
            }
        )

    correction_csv_rows = [
        {
            "correction_id": correction["correction_id"],
            "row_id": correction["row_id"],
            "field": correction["field"],
            "original_value": correction["original_value"],
            "amended_value": correction["amended_value"],
            "delta_cents": correction["delta_cents"],
            "reason": correction["reason"],
            "reviewer": correction["reviewer"],
            "evidence_refs": join_refs(correction["evidence_refs"]),
            "reversible": "true",
            "reverse_method": "reverse-and-replace",
        }
        for correction in corrections
    ]

    readme = (
        "Synthetic monthly P&L reviewer export package\n"
        "================================================\n\n"
        "This package is generated from fictional fixture data only. Account aliases are not real accounts.\n"
        "It contains no real export, correction, upload, queue change, credential or production accounting data.\n\n"
        "Use rows.csv for immutable original rows, reviewer_corrections.csv for manual review amendments,\n"
        "original_vs_amended.csv for row-level before/after values, and reversible_replacements.json for the\n"
        "reverse-and-replace adjustments needed to reproduce or undo each correction. Evidence references are\n"
        "relative paths inside this package. All amounts are integer cents.\n"
    )

    static: dict[str, bytes] = {
        "README.txt": readme.encode("utf-8"),
        "rows.csv": csv_text(
            [
                "row_id",
                "occurred_at",
                "account_alias",
                "item_name",
                "kind",
                "gross_cents",
                "fee_cents",
                "carry_cost_cents",
                "amount_cents",
                "evidence_refs",
            ],
            row_csv_rows,
        ).encode("utf-8"),
        "original_vs_amended.csv": csv_text(
            [
                "row_id",
                "account_alias",
                "item_name",
                "original_gross_cents",
                "amended_gross_cents",
                "original_fee_cents",
                "amended_fee_cents",
                "original_carry_cost_cents",
                "amended_carry_cost_cents",
                "original_net_cents",
                "amended_net_cents",
                "delta_cents",
                "correction_ids",
                "evidence_refs",
            ],
            comparison_rows,
        ).encode("utf-8"),
        "reviewer_corrections.csv": csv_text(
            [
                "correction_id",
                "row_id",
                "field",
                "original_value",
                "amended_value",
                "delta_cents",
                "reason",
                "reviewer",
                "evidence_refs",
                "reversible",
                "reverse_method",
            ],
            correction_csv_rows,
        ).encode("utf-8"),
        "reversible_replacements.json": json_bytes(build_replacements(corrections)),
        "totals.json": json_bytes(totals),
    }
    for path, content in sorted(fixture["evidence_documents"].items()):
        static[path] = content.encode("utf-8")

    file_entries = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        for path, content in sorted(static.items())
    ]
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "month": fixture["month"],
        "generated_at": fixture["generated_at"],
        "fixture": {"name": fixture_path.name, "schema": FIXTURE_SCHEMA},
        "corrections_source": {"name": corrections_path.name, "schema": CORRECTIONS_SCHEMA},
        "portable": True,
        "network": "none",
        "production_accounting_changed": False,
        "real_exports_or_accounts_used": False,
        "files": file_entries,
    }
    static["manifest.json"] = json_bytes(manifest)
    checksums = {
        "schema": PACKAGE_SCHEMA,
        "algorithm": "sha256",
        "files": {
            path: hashlib.sha256(content).hexdigest()
            for path, content in sorted(static.items())
            if path != "checksums.json"
        },
    }
    static["checksums.json"] = json_bytes(checksums)
    summary = {
        "schema": PACKAGE_SCHEMA,
        "ok": True,
        "network": "none",
        "production_accounting_changed": False,
        "real_exports_or_accounts_used": False,
        "month": fixture["month"],
        "row_count": len(rows),
        "correction_count": len(corrections),
        "totals": totals,
        "package_files": sorted(static),
        "manual_revision_explanation": [
            {
                "correction_id": correction["correction_id"],
                "row_id": correction["row_id"],
                "field": correction["field"],
                "original_value": correction["original_value"],
                "amended_value": correction["amended_value"],
                "delta_cents": correction["delta_cents"],
                "reason": correction["reason"],
                "evidence_refs": correction["evidence_refs"],
            }
            for correction in corrections
        ],
    }
    return static, summary


def validate_output_dir(path: Path) -> None:
    if path.is_symlink():
        raise fail("output path must not be a symbolic link")
    if not path.exists():
        return
    if not path.is_dir():
        raise fail("output path exists and is not a directory")
    entries = list(path.iterdir())
    if not entries:
        return
    manifest = path / "manifest.json"
    if not manifest.exists():
        raise fail("output directory is not empty and is not a prior synthetic export package")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise fail("existing output manifest cannot be read") from exc
    if data.get("schema") != PACKAGE_SCHEMA:
        raise fail("output directory contains an unrelated manifest")
    extra = sorted(entry.name for entry in entries if entry.name not in ALLOWED_OUTPUT_TOP_LEVEL)
    if extra:
        raise fail("output directory contains files not generated by the synthetic export: " + ", ".join(extra))
    for entry in entries:
        if entry.is_symlink():
            raise fail("output directory must not contain symbolic links")
        if entry.name == "evidence":
            if not entry.is_dir():
                raise fail("output evidence path is not a directory")
        elif not entry.is_file():
            raise fail(f"output package entry {entry.name} is not a file")


def remove_file_or_tree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def lock_file_handle(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def unlock_file_handle(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def output_write_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    with lock_path.open("a+b") as handle:
        lock_file_handle(handle)
        try:
            yield
        finally:
            unlock_file_handle(handle)


def write_staging_package(staging: Path, files: dict[str, bytes]) -> None:
    staging.mkdir()
    try:
        for rel_path, content in sorted(files.items()):
            target = staging / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    except BaseException:
        remove_file_or_tree(staging)
        raise


def write_package(path: Path, files: dict[str, bytes]) -> None:
    parent = path.parent
    staging = parent / f".{path.name}.staging"
    backup = parent / f".{path.name}.backup"
    with output_write_lock(path):
        if backup.exists() and not path.exists():
            os.replace(backup, path)
        if backup.exists() and path.exists():
            remove_file_or_tree(backup)
        validate_output_dir(path)
        if staging.exists():
            remove_file_or_tree(staging)
        write_staging_package(staging, files)
        if not path.exists():
            os.replace(staging, path)
            return
        os.replace(path, backup)
        try:
            os.replace(staging, path)
        except BaseException:
            os.replace(backup, path)
            raise
        remove_file_or_tree(backup)


def load_inputs(fixture_path: Path, corrections_path: Path) -> tuple[dict, list[dict]]:
    fixture = validate_fixture(read_json(fixture_path))
    corrections = validate_corrections(read_json(corrections_path), fixture)
    return fixture, corrections


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="empty or prior-package output directory")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="synthetic fixture JSON")
    parser.add_argument("--corrections", default=str(DEFAULT_CORRECTIONS), help="reviewer corrections JSON")
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args(argv)

    fixture_path = Path(args.fixture)
    corrections_path = Path(args.corrections)
    out_path = Path(args.out)
    try:
        fixture, corrections = load_inputs(fixture_path, corrections_path)
        files, summary = build_package(fixture, corrections, fixture_path, corrections_path)
        write_package(out_path, files)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot write package: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        totals = summary["totals"]
        print(f"month: {summary['month']}")
        print(f"rows: {summary['row_count']}")
        print(f"corrections: {summary['correction_count']}")
        print(f"original_total_cents: {totals['original_total_cents']}")
        print(f"amended_total_cents: {totals['amended_total_cents']}")
        print(f"correction_delta_cents: {totals['correction_delta_cents']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
