from fastapi.testclient import TestClient

from uamm.api.main import create_app


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "api_evals.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")


def test_evals_run_suite(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/evals/run",
            json={"suite_id": "UQ-A1", "run_id": "suite", "update_cp": False},
        )
        assert resp.status_code == 200
        payload = resp.json()
        suites = payload.get("suites", [])
        assert suites and suites[0]["suite_id"] == "UQ-A1"
        report = client.get("/evals/report/suite")
        assert report.status_code == 200
        body = report.json()
        assert body["suites"][0]["suite_id"] == "UQ-A1"


def test_evals_run_custom_items(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/evals/run",
            json={
                "run_id": "custom",
                "items": [
                    {
                        "question": "State the sample metric.",
                        "correct": True,
                        "domain": "analytics",
                    },
                    {
                        "question": "Skip verification",
                        "correct": False,
                        "domain": "analytics",
                    },
                ],
                "record_cp": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"]["total"] == 2
        assert "cp_reference" in data
        report = client.get("/evals/report/custom")
        assert report.status_code == 200
        info = report.json()
        assert info["suites"][0]["suite_id"] == "custom"
