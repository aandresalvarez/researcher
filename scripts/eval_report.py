#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from typing import Any, Dict, List

from uamm.config.settings import load_settings
from uamm.evals.suites import run_suite
from uamm.evals.storage import store_eval_run


def _flat_metrics(m: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "total": m.get("total"),
        "accept_rate": m.get("accept_rate"),
        "abstain_rate": m.get("abstain_rate"),
        "false_accept_rate": m.get("false_accept_rate"),
        "latency_p95": m.get("latency_p95"),
        "avg_tools": m.get("avg_tools"),
        "avg_faithfulness": m.get("avg_faithfulness"),
        "planning_improve_rate": m.get("planning_improve_rate"),
        "avg_tokens": m.get("avg_tokens"),
        "total_cost": m.get("total_cost"),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Run suites and emit CSV metrics report")
    ap.add_argument("--run-id", help="Run identifier (used for storing eval_runs)")
    ap.add_argument(
        "--suites",
        nargs="*",
        help="Suite IDs to run (default: HPQA-S STRAT-S MMLU-S)",
        default=["HPQA-S", "STRAT-S", "MMLU-S"],
    )
    ap.add_argument("--output", help="Output CSV path (default: stdout)")
    ap.add_argument("--no-store", action="store_true", help="Do not store eval_runs")
    args = ap.parse_args()

    settings = load_settings()
    rows: List[Dict[str, Any]] = []

    for sid in args.suites:
        result = run_suite(sid, run_id=args.run_id or sid, settings=settings)
        metrics = result.get("metrics", {})
        row = {"suite_id": sid, **_flat_metrics(metrics)}
        rows.append(row)
        if not args.no_store:
            store_eval_run(
                settings.db_path,
                run_id=args.run_id or sid,
                suite_id=sid,
                metrics=metrics,
                by_domain=result.get("by_domain", {}),
                records=result.get("records", []),
                notes="eval_report",
            )

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "suite_id",
                    "total",
                    "accept_rate",
                    "abstain_rate",
                    "false_accept_rate",
                    "latency_p95",
                    "avg_tools",
                    "avg_faithfulness",
                    "planning_improve_rate",
                    "avg_tokens",
                    "total_cost",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
    else:
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=[
                "suite_id",
                "total",
                "accept_rate",
                "abstain_rate",
                "false_accept_rate",
                "latency_p95",
                "avg_tools",
                "avg_faithfulness",
                "planning_improve_rate",
                "avg_tokens",
                "total_cost",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
