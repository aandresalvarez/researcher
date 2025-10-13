from fastapi.testclient import TestClient
from uamm.api.main import create_app


def test_tools_approve_flow_stub():
    app = create_app()
    with TestClient(app) as client:
        # create a pending approval in store
        store = app.state.approvals
        store.create("appr-1", {"tool": "WEB_FETCH"})
        r = client.post(
            "/tools/approve",
            json={"approval_id": "appr-1", "approved": True, "reason": "ok"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["approval_id"] == "appr-1"
        assert data["status"] == "approved"
        # unknown approval
        r2 = client.post(
            "/tools/approve", json={"approval_id": "missing", "approved": True}
        )
        assert r2.status_code == 404


def test_agent_answer_waits_for_tool_approval(monkeypatch):
    os_env = monkeypatch.setenv
    os_env("UAMM_WEB_FETCH_FIXTURE_DIR", "tests/data")
    os_env("UAMM_WEB_SEARCH_FIXTURE", "tests/data/web_search_results.json")
    os_env("UAMM_DB_PATH", "tests/data/demo.sqlite")
    os_env("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        client.app.state.settings.tools_requiring_approval = ["WEB_SEARCH"]
        resp = client.post(
            "/agent/answer",
            json={
                "question": "Provide the latest metrics with a citation",
                "stream": False,
                "max_refinements": 1,
                "borderline_delta": 1.0,
                "memory_budget": 0,
            },
        )
        assert resp.status_code == 202
        payload = resp.json()
        approval_id = payload.get("approval_id")
        assert approval_id
        store = app.state.approvals
        context = store.get(approval_id)
        assert context["context"]["tool"] == "WEB_SEARCH"
        client.post(
            "/tools/approve", json={"approval_id": approval_id, "approved": True}
        )
        resp2 = client.post(
            "/agent/answer",
            headers={"X-Approval-ID": approval_id},
            json={
                "question": "Provide the latest metrics with a citation",
                "stream": False,
                "max_refinements": 1,
                "borderline_delta": 1.0,
                "memory_budget": 0,
            },
        )
        assert resp2.status_code == 200
        final = resp2.json()
        assert final["stop_reason"] != "approval_pending"
