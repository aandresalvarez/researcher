from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig


def test_planning_mcts_event():
    agent = MainAgent(cp_enabled=False, policy=PolicyConfig(tau_accept=0.6, delta=0.2))
    events: list[tuple[str, dict]] = []

    def emit(evt: str, data: dict) -> None:
        events.append((evt, data))

    params = {
        "question": "What is modular memory?",
        "max_refinements": 0,
        "snne_samples": 3,
        # force planning regardless of borderline
        "planning_enabled": True,
        "planning_mode": "mcts",
        "planning_budget": 2,
        "planning_when": "always",
    }
    _ = agent.answer(params=params, emit=emit)
    kinds = [k for (k, _) in events]
    assert "planning" in kinds
