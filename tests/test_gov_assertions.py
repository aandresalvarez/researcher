from fastapi.testclient import TestClient
from uamm.api.main import create_app


def test_gov_check_assertions_pass():
    app = create_app()
    dag = {
        "nodes": [
            {"id": "a", "type": "premise"},
            {"id": "b", "type": "claim"},
        ],
        "edges": [{"from": "a", "to": "b"}],
    }
    assertions = [
        {"predicate": "no_pcn_failures"},
        {"predicate": "no_dependency_failures"},
        {"predicate": "max_depth", "value": 3},
        {"predicate": "path_exists", "source": "a", "target": "b"},
        {"predicate": "types_allowed", "types": ["premise", "claim"]},
    ]
    with TestClient(app) as client:
        r = client.post("/gov/check", json={"dag": dag, "verified_pcn": [], "assertions": assertions})
        assert r.status_code == 200
        data = r.json()
        assert data["validation_ok"] is True
        results = {a["predicate"]: a["passed"] for a in data.get("assertions", [])}
        assert all(results.get(p) for p in ["no_pcn_failures", "no_dependency_failures", "max_depth", "path_exists", "types_allowed"]) is True

