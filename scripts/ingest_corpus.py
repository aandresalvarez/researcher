#!/usr/bin/env python3
"""Ingest documents into the UAMM RAG corpus with optional KG metadata."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, Iterator, List

from uamm.config.settings import load_settings
from uamm.storage.db import ensure_schema
from uamm.rag.corpus import add_doc
from uamm.rag.vector_store import LanceDBUnavailable, upsert_document_embedding


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest documents into the RAG corpus."
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to SQLite database (will be created if missing).",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input file (JSONL/JSON/CSV). JSON records require fields: title, text, optional url, entities.",
    )
    parser.add_argument(
        "--schema",
        default="src/uamm/memory/schema.sql",
        help="Schema file for ensuring tables exist.",
    )
    parser.add_argument(
        "--entities-field",
        default="entities",
        help="Name of the field containing entity lists (default: entities).",
    )
    return parser.parse_args()


def load_records(path: Path) -> Iterator[Dict]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
    elif suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for item in payload:
                yield item
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("documents") or []
            for item in items:
                yield item
        else:
            raise ValueError("JSON input must be an object or array.")
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                yield dict(row)
    else:
        raise ValueError(f"Unsupported input format: {suffix}")


def normalise_entities(raw: Iterable) -> List[str]:
    out: List[str] = []
    if isinstance(raw, str):
        # Accept comma or pipe separated
        for token in raw.replace("|", ",").split(","):
            token = token.strip()
            if token:
                out.append(token)
    elif isinstance(raw, (list, tuple, set)):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
    return out


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    schema_path = Path(args.schema)
    ensure_schema(str(db_path), str(schema_path))
    settings = load_settings()
    settings.db_path = str(db_path)
    input_path = Path(args.input)
    count = 0
    for record in load_records(input_path):
        title = str(record.get("title") or "").strip()
        text = str(record.get("text") or "").strip()
        url = record.get("url")
        if not title and not text:
            continue
        meta: Dict[str, any] = {}
        entities = record.get(args.entities_field)
        if entities:
            meta["entities"] = normalise_entities(entities)
        if record.get("tags"):
            meta["tags"] = record.get("tags")
        doc_id = add_doc(
            str(db_path),
            title=title or text[:60],
            url=str(url) if url else None,
            text=text,
            meta=meta,
        )
        if getattr(settings, "vector_backend", "none").lower() == "lancedb":
            try:
                vector_meta = {"title": title or text[:60]}
                if url:
                    vector_meta["url"] = str(url)
                if meta:
                    for key, value in meta.items():
                        vector_meta[str(key)] = (
                            ",".join(value) if isinstance(value, list) else str(value)
                        )
                upsert_document_embedding(settings, doc_id, text, meta=vector_meta)
            except LanceDBUnavailable:
                print(
                    "[warn] lancedb not installed; skipping vector upsert",
                    file=sys.stderr,
                )
            except Exception as exc:  # pragma: no cover - best effort indexing
                print(
                    f"[warn] Failed to upsert vector for document: {exc}",
                    file=sys.stderr,
                )
        count += 1
    print(f"Ingested {count} documents into {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
