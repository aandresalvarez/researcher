#!/usr/bin/env python3
"""CLI helper to ingest a folder of documents into the RAG corpus.

Usage:
  PYTHONPATH=src \
  python scripts/ingest_folder.py --db data/uamm.sqlite --folder data/docs
"""

from __future__ import annotations

import argparse

from uamm.config.settings import load_settings
from uamm.storage.db import ensure_schema
from uamm.rag.ingest import scan_folder


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest local folder into corpus")
    parser.add_argument(
        "--db", default=None, help="Path to sqlite DB (defaults to settings)"
    )
    parser.add_argument(
        "--folder", default=None, help="Folder to scan (defaults to settings.docs_dir)"
    )
    args = parser.parse_args()

    settings = load_settings()
    db_path = args.db or settings.db_path
    folder = args.folder or settings.docs_dir

    ensure_schema(db_path, settings.schema_path)
    stats = scan_folder(db_path, folder, settings=settings)
    print({"db": db_path, "folder": folder, **stats})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
