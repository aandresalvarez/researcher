from fastapi.testclient import TestClient

from uamm.api.main import create_app


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "tuner.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")


def test_tuner_propose_and_apply(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/tuner/propose",
            json={
                "suite_ids": ["UQ-A1"],
                "metrics": {
                    "false_accept_rate": 0.2,
                    "accept_rate": 0.4,
                    "latency_p95": 7.0,
                    "abstain_rate": 0.4,
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        proposal_id = body.get("proposal_id")
        assert proposal_id
        proposal = body.get("proposal", {})
        assert "config_patch" in proposal

        apply_resp = client.post(
            "/tuner/apply",
            json={"proposal_id": proposal_id, "approved": True},
        )
        assert apply_resp.status_code == 200
        applied = apply_resp.json()
        assert applied["status"] == "applied"

        # A second proposal to exercise rejection path
        response2 = client.post(
            "/tuner/propose",
            json={"metrics": {"false_accept_rate": 0.01, "accept_rate": 0.95}},
        )
        assert response2.status_code == 200
        proposal_id2 = response2.json().get("proposal_id")
        reject_resp = client.post(
            "/tuner/apply",
            json={"proposal_id": proposal_id2, "approved": False, "reason": "manual"},
        )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"
