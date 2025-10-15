from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_ui_rag_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/rag")
        assert res.status_code == 200
        assert b"Upload File" in res.content
        assert b"Ingest Folder" in res.content
        assert b"Search Corpus" in res.content
