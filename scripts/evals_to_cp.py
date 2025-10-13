#!/usr/bin/env python3
"""
Convert evaluation records into CP calibration rows or import them directly.

Example usage:
    python scripts/run_demo_evals.py tests/data/demo_eval_dataset.json demo-run > demo-evals.json
    python scripts/evals_to_cp.py --input demo-evals.json --output demo-calibration.json
    python scripts/import_cp_artifacts.py --input demo-calibration.json --domain-field domain --run-id demo-calib
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from uamm.config.settings import load_settings
from uamm.policy import cp_store
from uamm.storage.db import ensure_schema

Record = Dict[str, object]
CPRow = Dict[str, object]


def _load_eval_records(path: Path) -> Iterable[Record]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "records" in data:
            return data["records"]
        if "items" in data:
            return data["items"]
    if isinstance(data, list):
        return data
    raise ValueError(
        "Unsupported eval payload; expected list or dict with 'records'/'items'."
    )


def _to_cp_rows(
    records: Iterable[Record],
    *,
    domain_field: str = "domain",
    score_field: str = "S",
    accepted_field: str = "accepted",
    correct_field: str = "correct",
) -> List[CPRow]:
    rows: List[CPRow] = []
    for rec in records:
        try:
            domain = str(rec.get(domain_field, "default"))
            score = float(rec[score_field])  # type: ignore[index]
            accepted = bool(rec.get(accepted_field, False))
            correct = bool(rec.get(correct_field, False))
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Missing required field {exc}") from exc
        rows.append(
            {"S": score, "accepted": accepted, "correct": correct, "domain": domain}
        )
    return rows


def _group_rows(rows: Iterable[CPRow]) -> Dict[str, List[Tuple[float, bool, bool]]]:
    grouped: Dict[str, List[Tuple[float, bool, bool]]] = {}
    for row in rows:
        domain = str(row["domain"])
        grouped.setdefault(domain, []).append(
            (float(row["S"]), bool(row["accepted"]), bool(row["correct"]))
        )
    return grouped


def convert_eval_to_cp(
    path: Path,
    *,
    domain_field: str = "domain",
    score_field: str = "S",
    accepted_field: str = "accepted",
    correct_field: str = "correct",
) -> List[CPRow]:
    records = _load_eval_records(path)
    return _to_cp_rows(
        records,
        domain_field=domain_field,
        score_field=score_field,
        accepted_field=accepted_field,
        correct_field=correct_field,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert evaluation outputs into CP calibration rows."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to eval output JSON (list or dict with 'records').",
    )
    parser.add_argument(
        "--output", help="Write CP rows (JSON with items[]) to this path."
    )
    parser.add_argument(
        "--domain-field",
        default="domain",
        help="Field name containing domain labels (default: domain).",
    )
    parser.add_argument(
        "--score-field",
        default="S",
        help="Field name containing final policy score (default: S).",
    )
    parser.add_argument(
        "--accepted-field",
        default="accepted",
        help="Field to read acceptance boolean from (default: accepted).",
    )
    parser.add_argument(
        "--correct-field",
        default="correct",
        help="Field to read correctness boolean from (default: correct).",
    )
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="Import rows into cp_artifacts after conversion.",
    )
    parser.add_argument(
        "--run-id", help="Run identifier for cp_artifacts (required with --import)."
    )
    parser.add_argument("--db-path", help="Override DB path when importing.")
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = convert_eval_to_cp(
        input_path,
        domain_field=args.domain_field,
        score_field=args.score_field,
        accepted_field=args.accepted_field,
        correct_field=args.correct_field,
    )

    if not rows:
        print("No rows produced from eval records.")
        return 0

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps({"items": rows}, indent=2), encoding="utf-8")
        print(f"Wrote {len(rows)} CP rows to {out_path}")

    if args.do_import:
        if not args.run_id:
            raise SystemExit("--run-id is required when using --import")
        settings = load_settings()
        db_path = args.db_path or settings.db_path
        ensure_schema(db_path, settings.schema_path)
        grouped = _group_rows(rows)
        total = 0
        for domain, tuples in grouped.items():
            total += cp_store.add_artifacts(
                db_path, run_id=args.run_id, domain=domain, items=tuples
            )
        print(
            f"Imported {total} rows into {db_path} across domains: {', '.join(grouped.keys())}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
