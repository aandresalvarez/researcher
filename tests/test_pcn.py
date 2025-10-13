from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig


def test_pcn_placeholder_in_final_text():
    agent = MainAgent(cp_enabled=False, policy=PolicyConfig(delta=1.0))
    params = {
        "question": "please provide a number and cite a source",
        "max_refinements": 1,
        "tool_budget_per_refinement": 3,
        "tool_budget_per_turn": 3,
        "memory_budget": 0,
    }
    res = agent.answer(params=params)
    assert "[PCN:" not in res["final"]


def test_pcn_placeholder_gated_in_stream(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from uamm.api.main import create_app

    monkeypatch.setenv("UAMM_DB_PATH", str(tmp_path / "pcn.sqlite"))
    monkeypatch.setenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/agent/answer",
            json={
                "question": "please provide a number and cite a source",
                "stream": False,
                "max_refinements": 1,
                "memory_budget": 0,
                "borderline_delta": 1.0,
            },
        )
        assert response.status_code == 200
        final = response.json()
        assert "[PCN:" not in final["final"]
