from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_evals_env_endpoint():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/evals/env")
        assert res.status_code == 200
        data = res.json()
        assert "llm_available" in data
        assert "embedding_backend" in data
