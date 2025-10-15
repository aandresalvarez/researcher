from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_ui_obs_renders():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/ui/obs")
        assert res.status_code == 200
        assert b"Metrics" in res.content
        assert b"Recent Steps" in res.content
