from fastapi.testclient import TestClient

from uamm.api.main import create_app


def test_dashboard_summary_aggregates_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "dash.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        metrics = client.app.state.metrics
        metrics["answers"] = 10
        metrics["accept"] = 6
        metrics["abstain"] = 2
        metrics["iterate"] = 2
        metrics["latency"] = {"count": 10, "average": 0.8, "p95": 1.5}
        metrics["gov_failures"] = 3
        metrics["gov_events"] = [{"dag_delta": {"ok": False}}]
        resp = client.get("/dashboards/summary")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["latency"]["p95"] == 1.5
        assert payload["acceptance"]["accepted"] == 6
        assert "cp" in payload and "stats" in payload["cp"]
        assert payload["governance"]["failures"] == 3
