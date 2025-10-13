from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig


def test_main_agent_refinement_respects_limits(monkeypatch):
    policy = PolicyConfig(w1=0.0, w2=1.0, tau_accept=0.3, delta=0.35)
    agent = MainAgent(cp_enabled=False, policy=policy)

    call_state = {"count": 0}

    def fake_verify(question, answer):
        call_state["count"] += 1
        if call_state["count"] <= 2:
            return 0.0, ["missing numbers"], True
        return 0.9, [], False

    monkeypatch.setattr(agent._verifier, "verify", fake_verify)  # type: ignore[attr-defined]

    params = {
        "question": "How many patients are there? Please ensure the count is verified.",
        "max_refinements": 2,
        "tool_budget_per_refinement": 2,
        "tool_budget_per_turn": 4,
        "memory_budget": 0,
        "borderline_delta": 1.0,
    }
    result = agent.answer(params=params)

    trace = result["trace"]
    refinements = [step for step in trace if step["is_refinement"]]
    assert len(refinements) == 2, "expected exactly max_refinements iterations"
    assert call_state["count"] == 3, "initial + two refinement verifier evaluations"
    for step in refinements:
        assert len(step["tools_used"]) <= 2
