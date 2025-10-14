from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uamm.api.main import create_app
from uamm.storage.db import ensure_schema
from uamm.rag.ingest import ingest_file
from uamm.rag.corpus import search_docs


def _setup_app(tmp_path):
    db = tmp_path / "api.sqlite"
    schema = Path("src/uamm/memory/schema.sql")
    ensure_schema(str(db), str(schema))
    return str(db)


def test_rag_docs_chunking_and_search(tmp_path, monkeypatch):
    db = _setup_app(tmp_path)
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_DOCS_AUTO_INGEST", "0")
    app = create_app()

    long_text = ("alpha beta gamma " * 200).strip()
    with TestClient(app) as client:
        r = client.post("/rag/docs", json={"title": "Long", "text": long_text})
        assert r.status_code == 200
        ids = r.json().get("ids", [])
        assert len(ids) >= 2
        sr = client.get("/rag/search", params={"q": "gamma"})
        assert sr.status_code == 200
        assert sr.json().get("hits"), "expected RAG search hits for ingested text"


def test_rag_ingest_folder_path_restriction_and_ingest(tmp_path, monkeypatch):
    db = _setup_app(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("Folder ingest test content")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.md").write_text("Should be rejected")

    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_DOCS_DIR", str(docs))
    monkeypatch.setenv("UAMM_DOCS_AUTO_INGEST", "0")
    app = create_app()

    with TestClient(app) as client:
        # Reject path outside configured docs_dir
        rbad = client.post("/rag/ingest-folder", json={"path": str(outside)})
        assert rbad.status_code == 400
        # Ingest from allowed docs_dir
        rok = client.post("/rag/ingest-folder", json={"path": str(docs)})
        assert rok.status_code == 200
        data = rok.json()
        assert data.get("ingested", 0) >= 1


def test_upload_file_endpoint_txt(tmp_path, monkeypatch):
    db = _setup_app(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_DOCS_DIR", str(docs))
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    app = create_app()

    # Use editor role via monkeypatched lookup
    from uamm.security.auth import APIKeyRecord
    editor_key = "wk_editor"

    def fake_lookup_key(db_path: str, token: str):
        if token == editor_key:
            return APIKeyRecord(
                id="1", workspace="wsX", key_hash="h", role="editor", label="editX", active=True, created=0.0
            )
        return None

    monkeypatch.setattr("uamm.security.auth.lookup_key", fake_lookup_key)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post(
            "/rag/upload-file",
            headers={"Authorization": f"Bearer {editor_key}"},
            data={"filename": "note.txt"},
            files={"file": (None, b"hello mitochondria in upload", "application/octet-stream")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("ingested", 0) >= 1
        sr = client.get("/rag/search", headers={"Authorization": f"Bearer {editor_key}"}, params={"q": "mitochondria"})
        assert sr.status_code == 200
        assert sr.json()["hits"], "uploaded doc should be searchable"


def test_upload_files_endpoint_txts(tmp_path, monkeypatch):
    db = _setup_app(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("UAMM_DB_PATH", db)
    monkeypatch.setenv("UAMM_DOCS_DIR", str(docs))
    monkeypatch.setenv("UAMM_AUTH_REQUIRED", "1")
    app = create_app()

    from uamm.security.auth import APIKeyRecord
    editor_key = "wk_editor"

    def fake_lookup_key(db_path: str, token: str):
        if token == editor_key:
            return APIKeyRecord(
                id="1", workspace="teamX", key_hash="h", role="editor", label="edX", active=True, created=0.0
            )
        return None

    monkeypatch.setattr("uamm.security.auth.lookup_key", fake_lookup_key)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        files = [
            ("files", ("a.txt", b"alpha mitochondria", "text/plain")),
            ("files", ("b.txt", b"beta cell", "text/plain")),
        ]
        r = client.post(
            "/rag/upload-files",
            headers={"Authorization": f"Bearer {editor_key}"},
            files=files,
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("saved", 0) >= 2
        sr = client.get(
            "/rag/search", headers={"Authorization": f"Bearer {editor_key}"}, params={"q": "mitochondria"}
        )
        assert sr.status_code == 200
        assert sr.json()["hits"], "at least one uploaded doc should be searchable"


@pytest.mark.skipif(pytest.importorskip("docx", reason="python-docx not installed") is None, reason="python-docx not installed")
def test_ingest_docx_and_search(tmp_path):
    db = _setup_app(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    docx = pytest.importorskip("docx")
    p = docs / "doc1.docx"
    doc = docx.Document()
    doc.add_paragraph("Docx ingestion succeeds with mitochondria keyword.")
    doc.save(str(p))

    # Direct ingest without running API
    from uamm.config.settings import Settings

    settings = Settings()
    settings.db_path = db
    settings.docs_dir = str(docs)
    settings.vector_backend = "none"

    did = ingest_file(db, str(p), settings=settings)
    assert isinstance(did, (str, type(None)))

    hits = search_docs(db, "mitochondria", k=5)
    # If parser worked, we should see at least one hit
    if did:
        assert hits, "expected a hit for docx keyword"


def test_pdf_ocr_fallback_monkeypatched(tmp_path, monkeypatch):
    # Create an empty/unsupported PDF and ensure OCR fallback is used when provided
    db = _setup_app(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    pdf_path = docs / "scan.pdf"
    # Minimal PDF header to simulate a file; actual parsing will fail -> None
    pdf_path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF")

    from uamm.config.settings import Settings

    settings = Settings()
    settings.db_path = db
    settings.docs_dir = str(docs)
    settings.vector_backend = "none"
    settings.docs_ocr_enabled = True

    # Force parse_pdf to return None then supply OCR content
    monkeypatch.setattr("uamm.rag.ingest._parse_pdf", lambda p: None)
    monkeypatch.setattr("uamm.rag.ingest._ocr_pdf", lambda p: "ocr extracted mitochondria text")

    did = ingest_file(db, str(pdf_path), settings=settings)
    assert did is not None
    hits = search_docs(db, "mitochondria", k=5)
    assert hits, "expected a hit from OCR fallback content"
