from pathlib import Path

from uamm.config.settings import Settings
from uamm.storage.db import ensure_schema
from uamm.rag.ingest import scan_folder, ingest_file, chunk_text, token_chunk_text, make_chunks
from uamm.rag.corpus import search_docs


def test_ingest_file_and_search(tmp_path):
    db = tmp_path / "db.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))

    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "note.md"
    f.write_text("# Title\nThis is a local test document about mitochondria.")

    # minimal settings stub
    settings = Settings()
    settings.db_path = str(db)
    settings.docs_dir = str(docs)
    settings.vector_backend = "none"

    did = ingest_file(str(db), str(f), settings=settings)
    assert did is not None

    hits = search_docs(str(db), "mitochondria", k=3)
    assert any("mitochondria" in h["snippet"].lower() for h in hits)


def test_scan_folder_skips_unmodified(tmp_path):
    db = tmp_path / "db.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha beta gamma")
    (docs / "b.md").write_text("delta epsilon zeta")

    settings = Settings()
    settings.db_path = str(db)
    settings.docs_dir = str(docs)
    settings.vector_backend = "none"

    stats1 = scan_folder(str(db), str(docs), settings=settings)
    assert stats1["ingested"] >= 2

    # Run again without changes: should largely skip
    stats2 = scan_folder(str(db), str(docs), settings=settings)
    assert stats2["ingested"] == 0
    assert stats2["skipped"] >= 2


def test_chunk_text_splits_and_overlaps():
    text = " ".join([f"word{i}" for i in range(0, 300)])
    chunks = chunk_text(text, chunk_chars=200, overlap_chars=50)
    assert len(chunks) >= 2
    # Ensure some overlap: the last 50 chars of chunk0 should appear in chunk1
    tail = chunks[0][-50:]
    assert tail.strip() in chunks[1]


def test_ingest_file_chunks_long_text(tmp_path):
    db = tmp_path / "db.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))

    docs = tmp_path / "docs2"
    docs.mkdir()
    long_text = ("Alpha beta gamma " * 200).strip()
    f = docs / "long.md"
    f.write_text(long_text)

    settings = Settings()
    settings.db_path = str(db)
    settings.docs_dir = str(docs)
    settings.vector_backend = "none"
    settings.docs_chunk_chars = 200
    settings.docs_overlap_chars = 50

    did = ingest_file(str(db), str(f), settings=settings)
    assert did is not None
    hits = search_docs(str(db), "gamma", k=50)
    # Expect multiple chunks indexed
    assert len(hits) >= 2


def test_token_chunk_text_uses_tokens_or_fallback(monkeypatch):
    text = "hello world " * 200
    # Try token path if tiktoken exists; otherwise verify fallback still chunks
    chunks = token_chunk_text(text, chunk_tokens=50, overlap_tokens=10)
    assert len(chunks) >= 2


def test_make_chunks_mode_tokens_or_chars(monkeypatch):
    class S:
        docs_chunk_mode = "tokens"
        docs_chunk_tokens = 50
        docs_overlap_tokens = 10

    chunks = make_chunks("a word " * 200, settings=S())
    assert len(chunks) >= 2

    S.docs_chunk_mode = "chars"
    S.docs_chunk_chars = 200
    S.docs_overlap_chars = 50
    chunks2 = make_chunks("a word " * 200, settings=S())
    assert len(chunks2) >= 2
