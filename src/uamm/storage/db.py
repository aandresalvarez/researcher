import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path: str, schema_path: str) -> None:
    conn = _connect(db_path)
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def ensure_migrations(db_path: str) -> None:
    """Apply lightweight migrations (add columns if missing)."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("PRAGMA table_info(steps)")
        cols = {row[1] for row in cur.fetchall()}  # type: ignore[index]
        if "change_summary" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN change_summary TEXT")
            conn.commit()
        if "domain" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN domain TEXT")
            conn.commit()
        if "trace_json" not in cols:
            conn.execute("ALTER TABLE steps ADD COLUMN trace_json TEXT")
            conn.commit()
    finally:
        conn.close()


def insert_step(
    db_path: str,
    *,
    question_redacted: str,
    answer_redacted: str,
    s1: float,
    s2: float,
    final_score: float,
    cp_accept: bool,
    action: str,
    reason: str,
    is_refinement: bool,
    status: str = "ok",
    latency_ms: int = 0,
    usage: Dict[str, Any] | None = None,
    pack_ids: List[str] | None = None,
    issues: List[str] | None = None,
    tools_used: List[str] | None = None,
    change_summary: str | None = None,
    eval_id: str | None = None,
    dataset_case_id: str | None = None,
    is_gold: bool | None = None,
    gold_correct: bool | None = None,
    domain: str | None = None,
    trace_json: str | None = None,
) -> str:
    conn = _connect(db_path)
    try:
        step_id = str(uuid.uuid4())
        ts = time.time()
        conn.execute(
            """
            INSERT INTO steps (
              id, ts, step, question, answer, domain, s1, s2, final_score, cp_accept,
              action, reason, is_refinement, status, latency_ms, usage, pack_ids,
              issues, tools_used, change_summary, eval_id, dataset_case_id, is_gold, gold_correct, trace_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                ts,
                0,
                question_redacted,
                answer_redacted,
                domain,
                s1,
                s2,
                final_score,
                1 if cp_accept else 0,
                action,
                reason,
                1 if is_refinement else 0,
                status,
                latency_ms,
                (usage or {}).__repr__(),
                (pack_ids or []).__repr__(),
                (issues or []).__repr__(),
                (tools_used or []).__repr__(),
                change_summary,
                eval_id,
                dataset_case_id,
                1 if is_gold else 0 if is_gold is not None else None,
                1 if gold_correct else 0 if gold_correct is not None else None,
                trace_json,
            ),
        )
        conn.commit()
        return step_id
    finally:
        conn.close()
