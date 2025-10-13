from typing import Any, Dict, List
from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig


def run_evals(
    *,
    items: List[Dict[str, Any]],
    accept_threshold: float,
    cp_enabled: bool,
    tool_budget_per_refinement: int = 0,
    tool_budget_per_turn: int = 0,
    max_refinements: int = 0,
    use_cp_decision: bool | None = None,
) -> List[Dict[str, Any]]:
    """Run a lightweight eval and return structured records per item.

    Each record contains:
    - `question`: the prompt posed to the agent
    - `domain`: optional domain label from the dataset (defaults to "default")
    - `S`: final policy score
    - `accepted`: decision used for metrics (CP if enabled, otherwise threshold)
    - `correct`: expected ground truth label
    - `cp_accept`: CP accept flag (when available)

    `use_cp_decision` can override the acceptance decision: when True the CP accept
    flag is used even if the agent fallback would be threshold-based; when False the
    static threshold is used. When None (default), CP decisions are used only if the
    gate is enabled.
    """
    results: List[Dict[str, Any]] = []
    policy = PolicyConfig(tau_accept=accept_threshold, delta=0.0)
    agent = MainAgent(cp_enabled=cp_enabled, policy=policy)
    for it in items:
        q = str(it.get("question", ""))
        correct = bool(it.get("correct", False))
        domain = str(it.get("domain", "default"))
        params = {
            "question": q,
            "max_refinements": max_refinements,
            "tool_budget_per_refinement": tool_budget_per_refinement,
            "tool_budget_per_turn": tool_budget_per_turn,
        }
        res = agent.answer(params=params)
        S = float(res["uncertainty"]["final_score"])  # type: ignore[index]
        cp_accept = res["uncertainty"].get("cp_accept")
        if use_cp_decision is True:
            accepted = bool(cp_accept)
        elif use_cp_decision is False:
            accepted = S >= accept_threshold
        elif cp_enabled and cp_accept is not None:
            accepted = bool(cp_accept)
        else:
            accepted = S >= accept_threshold
        results.append(
            {
                "question": q,
                "domain": domain,
                "S": S,
                "accepted": bool(accepted),
                "correct": correct,
                "cp_accept": bool(cp_accept) if cp_accept is not None else None,
            }
        )
    return results
