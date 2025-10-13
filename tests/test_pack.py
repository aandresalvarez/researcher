from importlib.resources import files

from uamm.rag.pack import build_pack
from uamm.storage.db import ensure_schema
from uamm.rag.corpus import add_doc


def _init_db(tmp_path):
    db_path = tmp_path / "pack.sqlite"
    schema_path = files("uamm.memory").joinpath("schema.sql")
    ensure_schema(str(db_path), str(schema_path))
    return str(db_path)


def test_build_pack_returns_items(tmp_path):
    # Minimal smoke: with no data, returns empty list
    db = str(tmp_path / "db.sqlite")
    items = build_pack(db, "question", memory_k=0, corpus_k=0, budget=5)
    assert isinstance(items, list)
    assert len(items) == 0


def test_build_pack_includes_urls_from_corpus(tmp_path):
    db = _init_db(tmp_path)
    add_doc(
        db,
        title="Delta metrics",
        url="https://example.com/delta",
        text="Delta metrics summary indicates improved retention across cohorts.",
    )
    items = build_pack(db, "delta metrics", memory_k=0, corpus_k=5, budget=5)
    assert items
    first = items[0]
    assert first.get("url") == "https://example.com/delta"
    assert 0.0 <= first.get("score", 0.0) <= 1.0
