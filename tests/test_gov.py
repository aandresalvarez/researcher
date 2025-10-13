from unittest.mock import patch

from uamm.agents.main_agent import MainAgent
from uamm.gov.validator import validate_dag
from uamm.policy.policy import PolicyConfig


def test_governance_event_flags_missing():
    agent = MainAgent(cp_enabled=False, policy=PolicyConfig(delta=1.0))
    events = []

    def _emit(evt: str, data: dict) -> None:
        events.append((evt, data))

    with patch("uamm.agents.main_agent.math_eval", side_effect=ValueError("fail")):
        agent.answer(
            {
                "question": "provide the metric count and cite the source",
                "max_refinements": 1,
                "tool_budget_per_refinement": 1,
                "tool_budget_per_turn": 1,
                "memory_budget": 0,
            },
            emit=_emit,
        )

    gov = [payload for evt, payload in events if evt == "gov"]
    assert gov, "expected governance events"
    assert any(not payload.get("dag_delta", {}).get("ok", True) for payload in gov)


def test_validate_dag_detects_cycle_and_unsupported_claim():
    dag = {
        "nodes": [
            {"id": "p1", "type": "premise", "text": "premise"},
            {"id": "c1", "type": "claim", "text": "claim"},
        ],
        "edges": [
            {"from": "p1", "to": "c1"},
            {"from": "c1", "to": "p1"},
        ],
    }
    ok, failing = validate_dag(dag)
    assert not ok
    assert any(code.startswith("cycle:") for code in failing)


def test_validate_dag_rejects_invalid_type_and_missing_support():
    dag = {
        "nodes": [
            {"id": "x1", "type": "unsupported", "text": "bad"},
            {"id": "c1", "type": "claim", "text": "claim"},
        ],
        "edges": [],
    }
    ok, failing = validate_dag(dag)
    assert not ok
    assert "invalid_type:x1" in failing
    assert "unsupported_claim:c1" in failing
