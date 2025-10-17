#!/usr/bin/env python3
"""Convert workspace_policies.json column from repr strings to JSON.

Usage:
  PYTHONPATH=src python scripts/migrate_policies_to_json.py --db data/uamm.sqlite

If --db is omitted, uses env UAMM_DB_PATH or defaults to data/uamm.sqlite.
The script is idempotent and only rewrites rows where json is a non-JSON string.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict


def _parse_blob(raw: Any) -> Dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    # Try JSON first
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else None
    except Exception:
        pass
    # Fallback to safe literal_eval
    try:
        import ast

        val = ast.literal_eval(raw)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def migrate(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS workspace_policies (workspace TEXT PRIMARY KEY, policy_name TEXT, json TEXT, updated REAL)"
            )
        except Exception:
            pass
        rows = con.execute(
            "SELECT workspace, policy_name, json, updated FROM workspace_policies"
        ).fetchall()
        rewritten = 0
        for r in rows:
            raw = r["json"]
            # If already a JSON dict, leave as-is
            try:
                val = json.loads(raw)
                if isinstance(val, dict):
                    continue
            except Exception:
                pass
            blob = _parse_blob(raw)
            if blob is None:
                continue
            new_json = json.dumps(blob)
            if new_json == raw:
                continue
            con.execute(
                "UPDATE workspace_policies SET json = ? WHERE workspace = ?",
                (new_json, r["workspace"]),
            )
            rewritten += 1
        con.commit()
        return rewritten
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db", dest="db", default=os.getenv("UAMM_DB_PATH", "data/uamm.sqlite")
    )
    args = ap.parse_args()
    path = os.path.abspath(args.db)
    if not os.path.exists(os.path.dirname(path)):
        print(
            f"error: directory does not exist: {os.path.dirname(path)}", file=sys.stderr
        )
        return 2
    count = migrate(path)
    print(f"migrated {count} row(s) in workspace_policies to JSON at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
