import numpy as np
import pytest

from uamm.rag.corpus import add_doc
from uamm.rag.embeddings import embed_text
from uamm.rag.lancedb_adapter import LanceDBAdapter
from uamm.rag.pack import build_pack
from uamm.rag.retriever import RetrieverConfig
from uamm.rag.vector_store import lancedb_upsert
from uamm.storage.db import ensure_schema

try:
    import lancedb  # type: ignore  # noqa: F401

    HAS_LANCEDB = True
except Exception:  # pragma: no cover - optional dependency
    HAS_LANCEDB = False


@pytest.mark.skipif(not HAS_LANCEDB, reason="lancedb optional dependency not installed")
def test_lancedb_adapter_roundtrip(tmp_path):
    uri = str(tmp_path / "ldb")
    adapter = LanceDBAdapter(dim=3, uri=uri, table="vectors")
    adapter.bulk_add(
        [
            ("a", np.array([1.0, 0.0, 0.0], dtype=np.float32), {"label": "alpha"}),
            ("b", np.array([0.0, 1.0, 0.0], dtype=np.float32), {"label": "beta"}),
        ]
    )
    hits = adapter.search(np.array([1.0, 0.0, 0.0], dtype=np.float32), k=2)
    assert hits, "Expected hits from LanceDB search"
    assert hits[0].doc_id == "a"
    assert hits[0].score >= hits[1].score


@pytest.mark.skipif(not HAS_LANCEDB, reason="lancedb optional dependency not installed")
def test_retriever_uses_lancedb_hits(tmp_path, monkeypatch):
    db_path = tmp_path / "uamm.sqlite"
    ensure_schema(str(db_path), "src/uamm/memory/schema.sql")
    question = "What is the revenue?"
    doc_text = "Revenue for Q1 was 12 million dollars."
    doc_id = add_doc(
        str(db_path),
        title="Quarterly report",
        url="https://example.com/report",
        text=doc_text,
        meta={"entities": ["revenue", "q1"]},
    )
    vector = embed_text(doc_text)
    uri = str(tmp_path / "lancedb")
    lancedb_upsert(
        doc_id,
        vector,
        uri=uri,
        table="rag_vectors",
        metric="cosine",
        meta={"title": "Quarterly report"},
    )

    cfg = RetrieverConfig(
        memory_k=0,
        vector_backend="lancedb",
        corpus_k=2,
        budget=4,
        min_score=0.0,
        w_sparse=0.2,
        w_dense=0.8,
        lancedb_uri=uri,
        lancedb_table="rag_vectors",
        lancedb_metric="cosine",
        lancedb_k=4,
    )
    hits = build_pack(
        str(db_path),
        question,
        memory_k=cfg.memory_k,
        corpus_k=cfg.corpus_k,
        budget=cfg.budget,
        min_score=cfg.min_score,
        w_sparse=cfg.w_sparse,
        w_dense=cfg.w_dense,
        vector_backend=cfg.vector_backend,
        lancedb_uri=cfg.lancedb_uri,
        lancedb_table=cfg.lancedb_table,
        lancedb_metric=cfg.lancedb_metric,
        lancedb_k=cfg.lancedb_k,
    )
    assert any(hit["id"] == doc_id for hit in hits)
    assert any(hit.get("why") == "lancedb match" for hit in hits)
