from typing import Dict, Any, List, Tuple

from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig


class _StubVerifier:
    def verify(self, question: str, answer: str) -> Tuple[float, List[str], bool]:
        # High s2 score to land in borderline+iterate, but still with a blocking issue
        return 0.8, ["missing citations"], True


def test_agent_blocks_disallowed_tool_via_allowlist(monkeypatch):
    agent = MainAgent(cp_enabled=False, policy=PolicyConfig(tau_accept=0.5, delta=0.3))
    # Replace verifier with stub to ensure iteration happens deterministically
    agent._verifier = _StubVerifier()  # type: ignore[attr-defined]

    events: List[Tuple[str, Dict[str, Any]]] = []

    def emit(evt: str, data: Dict[str, Any]) -> None:
        events.append((evt, data))

    params: Dict[str, Any] = {
        "question": "Provide citation for X",
        # No DB, no retrieval to keep execution local and deterministic
        "db_path": None,
        # Budgets allow one attempt
        "tool_budget_per_refinement": 1,
        "tool_budget_per_turn": 1,
        "max_refinements": 1,
        # Disallow WEB_SEARCH/WEB_FETCH by allowing only MATH_EVAL
        "tools_allowed": ["MATH_EVAL"],
        # Use SNNE mode to follow normal flow, but embedding will fallback safely
        "uq_mode": "snne",
        "snne_samples": 2,
        "snne_tau": 0.3,
    }
    result = agent.answer(params=params, emit=emit)
    assert isinstance(result, dict)
    # Look for a blocked tool event for WEB_SEARCH
    blocked = [
        d
        for (evt, d) in events
        if evt == "tool"
        and d.get("name") == "WEB_SEARCH"
        and d.get("status") == "blocked"
    ]
    assert blocked, "Expected WEB_SEARCH to be blocked by tools_allowed"
