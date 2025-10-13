from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, Optional


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def store_eval_run(
    db_path: str,
    *,
    run_id: str,
    suite_id: str,
    metrics: Dict[str, Any],
    by_domain: Dict[str, Any],
    records: Iterable[Dict[str, Any]],
    notes: Dict[str, Any] | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        payload_metrics = json.dumps(metrics, separators=(",", ":"))
        payload_by_domain = json.dumps(by_domain, separators=(",", ":"))
        payload_records = json.dumps(list(records), separators=(",", ":"))
        payload_notes = json.dumps(notes or {}, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO eval_runs (run_id, suite_id, ts, metrics_json, by_domain_json, records_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, suite_id) DO UPDATE SET
              ts=excluded.ts,
              metrics_json=excluded.metrics_json,
              by_domain_json=excluded.by_domain_json,
              records_json=excluded.records_json,
              notes=excluded.notes
            """,
            (
                run_id,
                suite_id,
                time.time(),
                payload_metrics,
                payload_by_domain,
                payload_records,
                payload_notes,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_eval_run(
    db_path: str, run_id: str, suite_id: Optional[str] = None
) -> list[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if suite_id is None:
            rows = conn.execute(
                "SELECT run_id, suite_id, ts, metrics_json, by_domain_json, records_json, notes FROM eval_runs WHERE run_id=? ORDER BY suite_id",
                (run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT run_id, suite_id, ts, metrics_json, by_domain_json, records_json, notes FROM eval_runs WHERE run_id=? AND suite_id=?",
                (run_id, suite_id),
            ).fetchall()
        results: list[Dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "run_id": row["run_id"],
                    "suite_id": row["suite_id"],
                    "ts": row["ts"],
                    "metrics": json.loads(row["metrics_json"])
                    if row["metrics_json"]
                    else {},
                    "by_domain": json.loads(row["by_domain_json"])
                    if row["by_domain_json"]
                    else {},
                    "records": json.loads(row["records_json"])
                    if row["records_json"]
                    else [],
                    "notes": json.loads(row["notes"]) if row["notes"] else {},
                }
            )
        return results
    finally:
        conn.close()
