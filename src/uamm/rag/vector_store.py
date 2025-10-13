from __future__ import annotations

from functools import lru_cache
from typing import Dict, Iterable, Tuple

import numpy as np

from uamm.rag.lancedb_adapter import LanceDBAdapter, LanceDBUnavailable
from uamm.rag.embeddings import embed_text


@lru_cache(maxsize=8)
def _cached_lancedb_adapter(
    dim: int, uri: str, table: str, metric: str
) -> LanceDBAdapter:
    """Cache adapters per (dim, uri, table, metric) since LanceDB handles thread safety internally."""
    return LanceDBAdapter(dim=dim, uri=uri, table=table, metric=metric)


def lancedb_upsert(
    doc_id: str,
    vector: np.ndarray,
    *,
    uri: str,
    table: str,
    metric: str,
    meta: Dict | None = None,
) -> None:
    adapter = _cached_lancedb_adapter(vector.shape[0], uri, table, metric)
    adapter.upsert(doc_id, vector, meta=meta or {})


def lancedb_bulk_add(
    items: Iterable[Tuple[str, np.ndarray, Dict | None]],
    *,
    dim: int,
    uri: str,
    table: str,
    metric: str,
) -> None:
    adapter = _cached_lancedb_adapter(dim, uri, table, metric)
    adapter.bulk_add(items)


def lancedb_search(
    query: np.ndarray,
    *,
    uri: str,
    table: str,
    metric: str,
    k: int,
):
    if query.ndim != 1:
        query = query.reshape(-1)
    adapter = _cached_lancedb_adapter(query.shape[0], uri, table, metric)
    return adapter.search(query, k=k)


def upsert_document_embedding(
    settings, doc_id: str, text: str, meta: Dict | None = None
) -> bool:
    backend = str(getattr(settings, "vector_backend", "none") or "none").lower()
    if backend != "lancedb":
        return False
    uri = getattr(settings, "lancedb_uri", "data/lancedb")
    table = getattr(settings, "lancedb_table", "rag_vectors")
    metric = getattr(settings, "lancedb_metric", "cosine")
    vector = embed_text(text)
    lancedb_upsert(doc_id, vector, uri=uri, table=table, metric=metric, meta=meta or {})
    return True


__all__ = [
    "LanceDBUnavailable",
    "lancedb_bulk_add",
    "lancedb_search",
    "lancedb_upsert",
    "upsert_document_embedding",
]
