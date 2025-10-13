#!/usr/bin/env python3
"""Lightweight load/perf smoke harness (PRD §17.3).

Usage:
    python scripts/load_smoke.py --requests 50 --concurrency 5

The script fires synthetic `/agent/answer` requests against a running UAMM API,
records latencies, and exits non-zero if the observed p95 exceeds the configured
threshold or if failures occur.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from typing import List

import httpx


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    qs = statistics.quantiles(values, n=100, method="inclusive")
    index = max(0, min(len(qs) - 1, int(q * 100) - 1))
    return qs[index]


async def _worker(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict,
    durations: List[float],
    errors: List[str],
) -> None:
    async with sem:
        start = time.perf_counter()
        try:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            durations.append(time.perf_counter() - start)
        except Exception as exc:  # pragma: no cover - network errors reported
            errors.append(str(exc))


async def run_load(
    base_url: str,
    endpoint: str,
    payload: dict,
    requests: int,
    concurrency: int,
) -> tuple[List[float], List[str]]:
    sem = asyncio.Semaphore(max(1, concurrency))
    durations: List[float] = []
    errors: List[str] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        tasks = [
            asyncio.create_task(
                _worker(sem, client, endpoint, payload, durations, errors)
            )
            for _ in range(requests)
        ]
        await asyncio.gather(*tasks)
    return durations, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a load/perf smoke against the UAMM API."
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="API base URL."
    )
    parser.add_argument(
        "--endpoint", default="/agent/answer", help="Endpoint to exercise."
    )
    parser.add_argument(
        "--requests", type=int, default=20, help="Total number of requests to issue."
    )
    parser.add_argument(
        "--concurrency", type=int, default=5, help="Concurrent in-flight requests."
    )
    parser.add_argument(
        "--p95-threshold",
        type=float,
        default=6.0,
        help="Fail if observed p95 latency (seconds) exceeds this threshold.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=0,
        help="Fail if more than this number of requests error out.",
    )
    parser.add_argument(
        "--question",
        default="Provide a short status update.",
        help="Question payload to send to the agent.",
    )
    args = parser.parse_args()

    payload = {
        "question": args.question,
        "stream": False,
        "max_refinements": 0,
        "memory_budget": 0,
        "tool_budget_per_turn": 0,
    }

    durations, errors = asyncio.run(
        run_load(
            base_url=args.base_url,
            endpoint=args.endpoint,
            payload=payload,
            requests=args.requests,
            concurrency=args.concurrency,
        )
    )

    if errors and len(errors) > args.max_errors:
        print(
            f"[FAIL] {len(errors)} errors encountered (max {args.max_errors})",
            file=sys.stderr,
        )
        for err in errors:
            print(f" - {err}", file=sys.stderr)
        return 1

    if not durations:
        print("[WARN] No successful durations recorded.", file=sys.stderr)
        return 1

    durations_sorted = sorted(durations)
    p50 = statistics.median(durations_sorted)
    p95 = _quantile(durations_sorted, 0.95)
    p99 = _quantile(durations_sorted, 0.99)
    avg = statistics.mean(durations_sorted)

    print("Load smoke summary:")
    print(f"  Requests      : {len(durations_sorted)} ok / {len(errors)} error(s)")
    print(f"  Concurrency   : {args.concurrency}")
    print(f"  Mean latency  : {avg:.3f}s")
    print(f"  P50 latency   : {p50:.3f}s")
    print(f"  P95 latency   : {p95:.3f}s")
    print(f"  P99 latency   : {p99:.3f}s")

    if p95 > args.p95_threshold:
        print(
            f"[FAIL] p95 {p95:.3f}s exceeds threshold {args.p95_threshold:.3f}s",
            file=sys.stderr,
        )
        return 1

    print("[OK] Load smoke within thresholds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
