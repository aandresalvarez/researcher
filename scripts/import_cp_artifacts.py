"""
Utility script to ingest CP calibration artifacts from CSV or JSON files.

Example:
    python scripts/import_cp_artifacts.py --input data/calibration.csv --domain biomed --run-id calib-20251011
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable, Tuple

from uamm.config.settings import load_settings
from uamm.policy import cp_store


def _read_json(path: Path) -> Iterable[Tuple[float, bool, bool]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "items" in payload:
        payload = payload["items"]
    for row in payload:
        yield float(row["S"]), bool(row["accepted"]), bool(row["correct"])


def _read_csv(path: Path) -> Iterable[Tuple[float, bool, bool]]:
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield (
                float(row["S"]),
                row["accepted"].lower() == "true",
                row["correct"].lower() == "true",
            )


def _load_rows(path: Path) -> Iterable[Tuple[float, bool, bool]]:
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        return _read_json(path)
    if suffix == ".csv":
        return _read_csv(path)
    raise ValueError(f"unsupported file format: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import conformal prediction calibration artifacts."
    )
    parser.add_argument(
        "--input", required=True, help="Path to CSV or JSON file with calibration rows."
    )
    parser.add_argument(
        "--domain",
        help="Domain label (e.g., biomed, analytics). Required unless --domain-field specified in the payload.",
    )
    parser.add_argument(
        "--domain-field",
        help="Name of the JSON/CSV column that carries per-row domain labels.",
    )
    parser.add_argument("--run-id", required=True, help="Calibration run identifier.")
    parser.add_argument(
        "--db-path", help="Override DB path; defaults to settings.db_path."
    )
    args = parser.parse_args()

    settings = load_settings()
    db_path = args.db_path or settings.db_path
    path = Path(args.input)
    rows = list(_load_rows(path))
    if not rows:
        raise SystemExit("no calibration rows found")

    domain_field = args.domain_field
    if domain_field and isinstance(rows[0], dict):
        grouped_rows = {}
        for record in rows:
            domain_value = record.get(domain_field) or args.domain
            if not domain_value:
                raise SystemExit(f"missing domain for record {record}")
            grouped_rows.setdefault(domain_value, []).append(record)
        inserted_total = 0
        for domain_value, domain_rows in grouped_rows.items():
            tuples = [
                (float(r["S"]), bool(r["accepted"]), bool(r["correct"]))
                for r in domain_rows
            ]
            inserted_total += cp_store.add_artifacts(
                db_path, run_id=args.run_id, domain=domain_value, items=tuples
            )
        print(
            f"Inserted {inserted_total} calibration rows across {len(grouped_rows)} domain(s) into {db_path} (run {args.run_id})."
        )
    else:
        if not args.domain:
            raise SystemExit("domain is required when domain_field is not provided.")
        tuples = (
            [(float(r[0]), bool(r[1]), bool(r[2])) for r in rows]
            if isinstance(rows[0], tuple)
            else [
                (float(r["S"]), bool(r["accepted"]), bool(r["correct"])) for r in rows
            ]
        )
        inserted = cp_store.add_artifacts(
            db_path, run_id=args.run_id, domain=args.domain, items=tuples
        )
        print(
            f"Inserted {inserted} calibration rows into {db_path} for domain '{args.domain}' (run {args.run_id})."
        )


if __name__ == "__main__":
    main()
