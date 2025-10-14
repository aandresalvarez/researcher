from fastapi.testclient import TestClient
from uamm.api.main import create_app


def test_metrics_contains_faithfulness(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "metrics.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        _ = client.post(
            "/agent/answer",
            json={
                "question": "Explain modular memory briefly",
                "stream": False,
                "use_memory": False,
                "max_refinements": 0,
            },
        )
        r = client.get("/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "faithfulness" in data or "faithfulness_summary" in data

