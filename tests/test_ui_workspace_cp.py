from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_ui_workspaces_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/workspaces")
        assert res.status_code == 200
        assert b"Create Workspace" in res.content
        assert b"Workspaces" in res.content


def test_ui_cp_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/cp")
        assert res.status_code == 200
        assert b"CP Threshold" in res.content
