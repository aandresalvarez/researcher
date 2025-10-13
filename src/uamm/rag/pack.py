from typing import Any, Dict, List

from uamm.rag.retriever import retrieve, RetrieverConfig


def build_pack(
    db_path: str,
    question: str,
    *,
    memory_k: int = 8,
    corpus_k: int = 8,
    budget: int = 8,
    min_score: float = 0.1,
    w_sparse: float = 0.5,
    w_dense: float = 0.5,
    vector_backend: str | None = None,
    lancedb_uri: str | None = None,
    lancedb_table: str | None = None,
    lancedb_metric: str | None = None,
    lancedb_k: int | None = None,
) -> List[Dict[str, Any]]:
    """Merge memory and corpus hits into a pack with dedupe and thresholds."""
    backend = (vector_backend or "none").lower()
    cfg = RetrieverConfig(
        memory_k=memory_k,
        vector_backend=backend,
        corpus_k=corpus_k,
        budget=budget,
        min_score=min_score,
        w_sparse=w_sparse,
        w_dense=w_dense,
        lancedb_uri=lancedb_uri if backend == "lancedb" else None,
        lancedb_table=lancedb_table or "rag_vectors",
        lancedb_metric=lancedb_metric or "cosine",
        lancedb_k=lancedb_k,
    )
    hits = retrieve(question, db_path=db_path, config=cfg)
    items: List[Dict[str, Any]] = []
    for hit in hits:
        item: Dict[str, Any] = {
            "id": hit.get("id"),
            "snippet": hit.get("snippet", ""),
            "why": hit.get("why", ""),
            "score": hit.get("score", 0.0),
        }
        if "url" in hit:
            item["url"] = hit["url"]
        if "title" in hit:
            item["title"] = hit["title"]
        if "source" in hit:
            item["source"] = hit["source"]
        if "sparse_score" in hit:
            item["sparse_score"] = hit["sparse_score"]
        if "dense_score" in hit:
            item["dense_score"] = hit["dense_score"]
        items.append(item)
    return items
