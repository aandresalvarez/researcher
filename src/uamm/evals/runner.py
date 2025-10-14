from typing import Any, Dict, List, Tuple
from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig
from uamm.verification.faithfulness import compute_faithfulness
from uamm.config.settings import load_settings


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
    settings = load_settings()
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
        # Capture planning events for improvement signal
        events: List[Tuple[str, Dict[str, Any]]] = []

        def _emit(evt: str, data: Dict[str, Any]) -> None:
            events.append((evt, data))

        res = agent.answer(params=params, emit=_emit)
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
        # Tool counts from final trace
        try:
            trace = list(res.get("trace", []) or [])
            tools = int((trace[-1] or {}).get("tools_used") and len(trace[-1]["tools_used"]) or 0)
        except Exception:
            tools = 0
        # Planning improvement flag
        planning_events = [d for (e, d) in events if e == "planning" and isinstance(d, dict)]
        planning_improved = any(bool(d.get("improved")) for d in planning_events)
        # Tokens/cost estimates
        usage = res.get("usage", {}) or {}
        tok_est = None
        toks = usage.get("llm_tokens")
        if isinstance(toks, (list, tuple)):
            tok_est = len(toks)
        if tok_est is None and usage.get("llm_tokens_estimate") is not None:
            try:
                tok_est = int(usage.get("llm_tokens_estimate"))
            except Exception:
                tok_est = None
        cost_est = None
        try:
            if tok_est is not None:
                cpk = float(getattr(settings, "token_cost_per_1k", 0.0) or 0.0)
                cost_est = (tok_est / 1000.0) * cpk
        except Exception:
            cost_est = None
        # Faithfulness score for the final answer (optional)
        try:
            f = compute_faithfulness(res.get("final", ""), res.get("pack_used", []) or [])
            faith = f.get("score")
        except Exception:
            faith = None
        results.append(
            {
                "question": q,
                "domain": domain,
                "S": S,
                "accepted": bool(accepted),
                "correct": correct,
                "cp_accept": bool(cp_accept) if cp_accept is not None else None,
                "tools": tools,
                "faithfulness": faith,
                "planning_improved": planning_improved,
                "tokens_estimate": tok_est,
                "cost_estimate": cost_est,
            }
        )
    return results
