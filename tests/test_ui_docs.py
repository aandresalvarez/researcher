from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_ui_docs_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/docs")
        assert res.status_code == 200
        assert b"UAMM UI Guide" in res.content
        assert b"Core Concepts" in res.content
        assert b"Playground" in res.content

