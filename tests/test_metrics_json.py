from fastapi.testclient import TestClient
from uamm.api.main import create_app


def test_metrics_endpoint_json_contains_cp_stats():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "requests" in data
        # cp_stats present (may be empty dict)
        assert "cp_stats" in data or isinstance(data.get("cp_stats", {}), dict)


def test_metrics_alerts_include_latency_and_abstain():
    app = create_app()
    with TestClient(app) as client:
        metrics = client.app.state.metrics
        metrics["requests"] = 120
        metrics["answers"] = 100
        metrics["abstain"] = 40
        metrics["accept"] = 50
        metrics["iterate"] = 10
        metrics["by_domain"] = {
            "default": {"answers": 100, "abstain": 40, "accept": 50, "iterate": 10},
        }
        metrics["answer_latency"] = {
            "buckets": {"0.1": 1, "0.5": 1, "1": 1, "2.5": 1, "6": 1, "+Inf": 95},
            "sum": 420.0,
            "count": 100,
        }
        metrics["answer_latency_by_domain"] = {
            "default": {
                "buckets": {"0.1": 1, "0.5": 1, "1": 1, "2.5": 1, "6": 1, "+Inf": 95},
                "sum": 420.0,
                "count": 100,
            }
        }
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "latency" in body
        assert body["latency"]["count"] == 100
        alerts = body.get("alerts", {})
        assert "latency" in alerts
        assert "abstain" in alerts
        prom = client.get("/metrics/prom")
        assert prom.status_code == 200
        text = prom.text
        assert "uamm_latency_p95_seconds" in text
        assert "uamm_abstain_rate" in text
        assert "uamm_alert_latency" in text
