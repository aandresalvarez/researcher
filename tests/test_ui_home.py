from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_ui_home_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/home")
        assert res.status_code == 200
        assert b"Workspaces" in res.content
        assert b"Quick Start" in res.content
        assert b"Configuration" in res.content
