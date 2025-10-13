from fastapi.testclient import TestClient
from uamm.api.main import create_app


def _build_items():
    items = []
    scores = [0.95, 0.94, 0.93, 0.92, 0.91, 0.90, 0.89, 0.88, 0.87, 0.86, 0.85, 0.84]
    for s in scores[:10]:
        items.append({"S": s, "accepted": True, "correct": True})
    for s in scores[10:]:
        items.append({"S": s, "accepted": True, "correct": False})
    return items


def test_cp_artifacts_updates_threshold_cache(monkeypatch):
    monkeypatch.setenv("UAMM_DB_PATH", "tests/data/demo.sqlite")
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/cp/artifacts",
            json={
                "run_id": "calib-1",
                "domain": "analytics",
                "items": _build_items(),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        tau = body.get("tau")
        assert tau is not None
        cache = client.app.state.cp_cache
        cached = cache.get("analytics", client.app.state.settings.cp_target_mis)
        assert cached == tau
        # Threshold endpoint should reuse cache
        resp2 = client.get("/cp/threshold", params={"domain": "analytics"})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["cached"] is True
        assert data2["tau"] == tau


def test_cp_threshold_recomputes_for_custom_target(monkeypatch):
    monkeypatch.setenv("UAMM_DB_PATH", "tests/data/demo.sqlite")
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/cp/artifacts",
            json={"run_id": "calib-2", "domain": "biomed", "items": _build_items()},
        )
        resp = client.get(
            "/cp/threshold", params={"domain": "biomed", "target_mis": 0.1}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert data["tau"] is not None
