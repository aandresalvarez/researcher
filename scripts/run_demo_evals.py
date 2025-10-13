#!/usr/bin/env python3
"""Eval helper CLI for quick calibration or suite runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List

from uamm.config.settings import load_settings
from uamm.evals.runner import run_evals
from uamm.evals.suites import (
    list_suites,
    summarize_by_domain,
    summarize_records,
)
from uamm.evals.orchestrator import default_suite_ids, run_suites
from uamm.evals.storage import store_eval_run
from uamm.policy.cp_reference import quantiles_from_scores
from uamm.policy.cp_store import add_artifacts, compute_threshold, domain_stats
from uamm.storage.db import ensure_schema


def _group_by_domain(
    records: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        dom = str(rec.get("domain", "default"))
        grouped.setdefault(dom, []).append(rec)
    return grouped


def _print_suite_list() -> None:
    for suite in list_suites():
        tags = ", ".join(suite.tags) if suite.tags else ""
        line = (
            f"{suite.id:<8} {suite.label:<24} {suite.category:<6} {suite.description}"
        )
        if tags:
            line += f" [{tags}]"
        print(line)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run demo evals or predefined suites.")
    parser.add_argument(
        "dataset", nargs="?", help="Path to a dataset JSON file (items[])."
    )
    parser.add_argument("run_id", nargs="?", help="Run identifier (default: auto).")
    parser.add_argument(
        "--suite",
        action="append",
        help="Run one or more predefined suites (e.g. --suite UQ-A1 --suite CP-B1).",
    )
    parser.add_argument(
        "--all-suites", action="store_true", help="Run all predefined suites."
    )
    parser.add_argument(
        "--list-suites", action="store_true", help="List available suites and exit."
    )
    parser.add_argument(
        "--no-cp-update",
        action="store_true",
        help="Skip updating CP artifacts/reference.",
    )
    return parser.parse_args()


def run_dataset(path: Path, run_id: str, *, update_cp: bool) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = (
        data.get("items", []) if isinstance(data, dict) else data
    )
    settings = load_settings()
    ensure_schema(settings.db_path, settings.schema_path)

    records = run_evals(
        items=items,
        accept_threshold=settings.accept_threshold,
        cp_enabled=False,
        tool_budget_per_refinement=0,
        tool_budget_per_turn=0,
        max_refinements=0,
    )
    metrics = summarize_records(records)
    by_domain = summarize_by_domain(records)

    output: Dict[str, Any] = {
        "run_id": run_id,
        "dataset": str(path),
        "metrics": metrics,
        "by_domain": by_domain,
        "records": records,
    }

    if update_cp:
        grouped = _group_by_domain(records)
        inserted_total = 0
        references: Dict[str, Dict[str, Any]] = {}
        for dom, recs in grouped.items():
            tuples = [
                (float(r["S"]), bool(r["accepted"]), bool(r["correct"])) for r in recs
            ]
            inserted_total += add_artifacts(
                settings.db_path,
                run_id=run_id,
                domain=dom,
                items=tuples,
            )
            tau = compute_threshold(
                settings.db_path, domain=dom, target_mis=settings.cp_target_mis
            )
            stats_dom = domain_stats(settings.db_path, domain=dom).get(dom, {})
            quantiles = quantiles_from_scores(
                [float(r["S"]) for r in recs], (0.1, 0.25, 0.5, 0.75, 0.9)
            )
            references[dom] = {"tau": tau, "stats": stats_dom, "quantiles": quantiles}
        output["cp_reference"] = {"domains": references, "inserted": inserted_total}
        output["cp_stats"] = domain_stats(settings.db_path)

    store_eval_run(
        settings.db_path,
        run_id=run_id,
        suite_id="custom",
        metrics=metrics,
        by_domain=by_domain,
        records=records,
        notes={"dataset": str(path)},
    )
    return output


def main() -> int:
    args = parse_args()
    if args.list_suites:
        _print_suite_list()
        return 0

    suite_ids = args.suite or []
    if args.all_suites:
        suite_ids = default_suite_ids()

    run_id = args.run_id or (suite_ids[0] if suite_ids else "demo-run")

    if suite_ids:
        suites_result = run_suites(
            run_id,
            suite_ids=suite_ids,
            update_cp_reference=not args.no_cp_update,
        )
        print(json.dumps(suites_result, indent=2))
        return 0

    if not args.dataset:
        print("Usage: run_demo_evals.py <dataset.json> [run_id]", file=sys.stderr)
        print("       run_demo_evals.py --suite UQ-A1", file=sys.stderr)
        print("       run_demo_evals.py --all-suites", file=sys.stderr)
        return 2

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    output = run_dataset(dataset_path, run_id, update_cp=not args.no_cp_update)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
