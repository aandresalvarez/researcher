from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_ui_evals_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/evals")
        assert res.status_code == 200
        assert b"Run Suites" in res.content
        assert b"Ad-hoc Items" in res.content
