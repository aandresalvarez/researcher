#!/usr/bin/env python3
"""Create a snapshot of the SQLite database with optional vacuum/prune."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

RETENTION_SECONDS = 60 * 60 * 24 * 90  # 90 days


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backup SQLite database with retention pruning."
    )
    parser.add_argument(
        "--db", default="data/uamm.sqlite", help="Path to SQLite database."
    )
    parser.add_argument(
        "--backup-dir", default="backup", help="Directory for backup snapshots."
    )
    parser.add_argument(
        "--vacuum", action="store_true", help="Run VACUUM before copying."
    )
    return parser.parse_args()


def vacuum_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def prune_artifacts(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        cutoff = time.time() - RETENTION_SECONDS
        conn.execute("DELETE FROM cp_artifacts WHERE ts < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


def copy_db(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"uamm-{timestamp}.sqlite"
    shutil.copy2(src, target)
    return target


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    backup_dir = Path(args.backup_dir)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    if args.vacuum:
        vacuum_db(db_path)
    prune_artifacts(db_path)
    target = copy_db(db_path, backup_dir)
    print(f"Backup written to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
