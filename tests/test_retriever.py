from importlib.resources import files

import pytest

from uamm.rag.retriever import retrieve, RetrieverConfig
from uamm.storage.db import ensure_schema
from uamm.storage.memory import add_memory
from uamm.rag.corpus import add_doc


def _init_db(tmp_path):
    db_path = tmp_path / "retriever.sqlite"
    schema_path = files("uamm.memory").joinpath("schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    return str(db_path)


def test_retrieve_merges_memory_and_corpus(tmp_path):
    db = _init_db(tmp_path)
    add_memory(
        db,
        key="fact:test",
        text="Alpha report counts 120 patients in cohort.",
        domain="fact",
    )
    add_doc(
        db,
        title="Alpha report Q1",
        url="https://example.com/alpha",
        text="The Q1 alpha report highlights 120 patients with follow-up metrics.",
    )
    config = RetrieverConfig(memory_k=3, corpus_k=3, budget=5)
    hits = retrieve(
        "How many patients are in the alpha report?", db_path=db, config=config
    )
    assert hits, "expected hybrid retriever to return combined hits"
    sources = {h.get("source") for h in hits}
    assert {"memory", "corpus"}.issubset(sources)
    assert all(0.0 <= h.get("score", 0.0) <= 1.0 for h in hits)
    # ensure url surfaces for corpus hit
    assert any(h.get("url") == "https://example.com/alpha" for h in hits)


def test_retrieve_deduplicates_by_snippet(tmp_path):
    db = _init_db(tmp_path)
    shared = "Shared snippet about gamma cohort outcomes."
    add_memory(db, key="fact:test", text=shared, domain="fact")
    add_doc(db, title="Gamma report", url="https://example.com/gamma", text=shared)
    hits = retrieve(
        "gamma cohort outcomes",
        db_path=db,
        config=RetrieverConfig(memory_k=5, corpus_k=5, budget=5),
    )
    assert len(hits) == 1
    hit = hits[0]
    # prefer corpus hit (has URL) when tied
    assert hit.get("url") == "https://example.com/gamma"
    assert hit.get("source") == "corpus"


def test_retrieve_faiss_parity(tmp_path):
    db = _init_db(tmp_path)
    add_memory(
        db, key="fact:test", text="Omega cohort registered 42 patients.", domain="fact"
    )
    add_doc(
        db,
        title="Omega report summary",
        url="https://example.com/omega",
        text="Detailed metrics for the Omega cohort show 42 patients with follow-up.",
    )
    query = "How many patients are in the Omega cohort report?"
    base_hits = retrieve(
        query,
        db_path=db,
        config=RetrieverConfig(memory_k=4, corpus_k=4, budget=4, use_faiss=False),
    )
    faiss_hits = retrieve(
        query,
        db_path=db,
        config=RetrieverConfig(memory_k=4, corpus_k=4, budget=4, use_faiss=True),
    )
    assert faiss_hits, "expected FAISS-backed retrieval to return hits"
    assert [h["id"] for h in base_hits] == [h["id"] for h in faiss_hits]
    for left, right in zip(base_hits, faiss_hits):
        assert right["score"] == pytest.approx(left["score"])
        assert right["dense_score"] == pytest.approx(left["dense_score"])


def test_retriever_entity_bonus(tmp_path, monkeypatch):
    monkeypatch.setenv("UAMM_EMBEDDING_BACKEND", "hash")
    db = _init_db(tmp_path)
    add_doc(
        db,
        title="Modular memory improves analytics collaboration",
        url="https://example.com/modular",
        text="Modular memory lets analytics teams reuse components quickly.",
        meta={"entities": ["analytics", "memory"]},
    )
    add_doc(
        db,
        title="Generic productivity article",
        url="https://example.com/productivity",
        text="Teams work better with clear processes.",
    )
    hits = retrieve(
        "How does modular memory help analytics teams collaborate?",
        db_path=db,
        config=RetrieverConfig(memory_k=0, corpus_k=5, budget=5, kg_boost=0.3),
    )
    assert hits, "expected at least one corpus hit"
    top = hits[0]
    entities = [ent.lower() for ent in top.get("meta", {}).get("entities", [])]
    assert "analytics" in entities
    assert top.get("kg_bonus", 0) > 0.0
