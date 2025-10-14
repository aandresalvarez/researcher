from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Tuple, Optional

from uamm.uq.snne import snne as snne_score, normalize as snne_normalize
from uamm.uq.sampling import generate_answer_variants
from uamm.policy.policy import final_score, PolicyConfig


@dataclass(frozen=True)
class PlanningConfig:
    mode: str = "tot"  # "tot"|"beam"|"mcts" (beam is heuristic)
    budget: int = 3
    threshold_improve: float = 0.0  # adopt only if S improves strictly by > this
    beam_width: int = 2  # for beam mode
    mcts_simulations: int = 5  # rollouts for mcts
    mcts_branch: int = 2  # children per expansion


DEFAULT_DOMAIN = "default"


def _score_candidate(
    *,
    candidate: str,
    question: str,
    evidence_snippets: Sequence[str],
    sample_count: int,
    embed: Callable[[str], Any],
    snne_calibrator: Any | None,
    verifier: Any,
    policy_cfg: PolicyConfig,
    domain: str = DEFAULT_DOMAIN,
) -> Tuple[float, float | None, List[str], float, List[str], bool]:
    """Return (s1_norm, raw_snne, snne_samples, s2, issues, needs_fix)."""
    try:
        samples = generate_answer_variants(
            candidate,
            question=question,
            evidence_snippets=list(evidence_snippets)[:3],
            count=max(2, sample_count),
        )
        raw = snne_score(samples, 0.3, embed=embed)
        if snne_calibrator is not None:
            # Respect caller-provided domain for calibration; fall back handled by calibrator
            dom = (domain or DEFAULT_DOMAIN).lower()
            s1 = snne_calibrator.normalize(domain=dom, raw=raw)
        else:
            s1 = snne_normalize(raw)
    except Exception:
        raw = None
        s1 = 1.0
        samples = [candidate] * max(2, sample_count)
    s2, issues, needs_fix = verifier.verify(question, candidate)
    # small tilt towards candidates addressing issues
    if needs_fix and isinstance(s1, (int, float)):
        s1 = min(1.0, float(s1) + 0.1 * len(issues))
    return (
        float(s1),
        raw if isinstance(raw, (int, float)) else None,
        samples,
        float(s2),
        list(issues),
        bool(needs_fix),
    )


def plan_best_answer(
    *,
    question: str,
    evidence_pack: Sequence[Dict[str, Any]],
    base_answer: str,
    embed: Callable[[str], Any],
    snne_calibrator: Any | None,
    verifier: Any,
    policy_cfg: PolicyConfig,
    sample_count: int = 3,
    config: PlanningConfig | None = None,
    domain: Optional[str] = None,
) -> Dict[str, Any]:
    """Heuristic planning: propose answer variants and pick best by policy score.

    Returns dict with keys: `best_answer`, `best`, `base`, where each entry has
    {answer, s1, s2, S, raw_snne, snne_samples, issues, needs_fix}.
    """
    cfg = config or PlanningConfig()
    # Evidence snippets for conditioning
    ev_snips: List[str] = [str(p.get("snippet", "") or "") for p in evidence_pack[:3]]
    # Base candidate is always considered
    candidates: List[str] = [base_answer]
    mode = (cfg.mode or "tot").lower()
    if mode == "beam":
        # Heuristic beam over paraphrases
        width = max(1, int(cfg.beam_width or 1))
        steps = max(0, int(cfg.budget or 0) - 1)
        beam: List[str] = [base_answer]
        all_seen: List[str] = [base_answer]
        for _ in range(steps):
            proposals: List[str] = []
            for seed in beam:
                try:
                    vs = generate_answer_variants(
                        seed,
                        question=question,
                        evidence_snippets=ev_snips,
                        count=width,
                    )
                except Exception:
                    vs = []
                for v in vs:
                    if isinstance(v, str) and v.strip() and v not in all_seen:
                        proposals.append(v.strip())
                        all_seen.append(v.strip())
            # score beam + proposals; keep top width
            scored_local: List[Tuple[float, str]] = []
            for cand in beam + proposals:
                s1, raw, samples, s2, issues, needs_fix = _score_candidate(
                    candidate=cand,
                    question=question,
                    evidence_snippets=ev_snips,
                    sample_count=max(2, sample_count),
                    embed=embed,
                    snne_calibrator=snne_calibrator,
                    verifier=verifier,
                    policy_cfg=policy_cfg,
                    domain=(domain or DEFAULT_DOMAIN),
                )
                S = final_score(snne_norm=float(s1), s2=float(s2), cfg=policy_cfg)
                scored_local.append((float(S), cand))
            scored_local.sort(key=lambda t: t[0], reverse=True)
            beam = [cand for _, cand in scored_local[:width]]
        candidates = list({c for c in beam + candidates})
    elif mode == "mcts":
        # Lightweight MCTS-like exploration: iteratively expand the best candidate
        # and keep the best among its children, within a fixed simulation budget.
        current_best = base_answer
        all_seen: List[str] = [base_answer]
        sims = max(1, int(cfg.mcts_simulations or 1))
        branch = max(1, int(cfg.mcts_branch or 1))
        for _ in range(sims):
            try:
                variants = generate_answer_variants(
                    current_best,
                    question=question,
                    evidence_snippets=ev_snips,
                    count=branch,
                )
            except Exception:
                variants = []
            # filter seen and empties
            children = [
                v.strip()
                for v in variants
                if isinstance(v, str) and v.strip() and v not in all_seen
            ]
            if not children:
                continue
            # score children, pick best child; if better than current, move to it
            best_child = None
            best_child_S = None
            for cand in children:
                s1, raw, samples, s2, issues, needs_fix = _score_candidate(
                    candidate=cand,
                    question=question,
                    evidence_snippets=ev_snips,
                    sample_count=max(2, sample_count),
                    embed=embed,
                    snne_calibrator=snne_calibrator,
                    verifier=verifier,
                    policy_cfg=policy_cfg,
                    domain=(domain or DEFAULT_DOMAIN),
                )
                S = final_score(snne_norm=float(s1), s2=float(s2), cfg=policy_cfg)
                if best_child is None or float(S) > float(best_child_S or 0.0):
                    best_child = cand
                    best_child_S = float(S)
            if best_child is not None:
                candidates.append(best_child)
                all_seen.append(best_child)
                # quick local comparison: if improves, move current_best to child
                # evaluate current_best S
                s1_b, _, _, s2_b, _, _ = _score_candidate(
                    candidate=current_best,
                    question=question,
                    evidence_snippets=ev_snips,
                    sample_count=max(2, sample_count),
                    embed=embed,
                    snne_calibrator=snne_calibrator,
                    verifier=verifier,
                    policy_cfg=policy_cfg,
                    domain=(domain or DEFAULT_DOMAIN),
                )
                S_b = final_score(snne_norm=float(s1_b), s2=float(s2_b), cfg=policy_cfg)
                if float(best_child_S or 0.0) > float(S_b):
                    current_best = best_child
    else:
        # ToT-like: one shot pool
        extra = max(0, int(cfg.budget or 0) - 1)
        if extra > 0:
            try:
                variants = generate_answer_variants(
                    base_answer,
                    question=question,
                    evidence_snippets=ev_snips,
                    count=extra,
                )
            except Exception:
                variants = []
            for v in variants:
                if isinstance(v, str) and v.strip():
                    candidates.append(v.strip())

    scored: List[Dict[str, Any]] = []
    for cand in candidates:
        s1, raw, samples, s2, issues, needs_fix = _score_candidate(
            candidate=cand,
            question=question,
            evidence_snippets=ev_snips,
            sample_count=max(2, sample_count),
            embed=embed,
            snne_calibrator=snne_calibrator,
            verifier=verifier,
            policy_cfg=policy_cfg,
            domain=(domain or DEFAULT_DOMAIN),
        )
        S = final_score(snne_norm=float(s1), s2=float(s2), cfg=policy_cfg)
        scored.append(
            {
                "answer": cand,
                "s1": float(s1),
                "raw_snne": raw,
                "snne_samples": samples,
                "s2": float(s2),
                "S": float(S),
                "issues": issues,
                "needs_fix": needs_fix,
            }
        )
    if not scored:
        return {
            "best_answer": base_answer,
            "best": {},
            "base": {},
            "candidates": [],
        }
    # Rank by policy score S
    scored.sort(key=lambda d: float(d.get("S", 0.0)), reverse=True)
    best = scored[0]
    base = scored[0] if candidates[0] == best["answer"] else None
    if base is None:
        # Find metrics for the original answer (first candidate)
        base = next((d for d in scored if d.get("answer") == candidates[0]), scored[-1])
    improved = float(best.get("S", 0.0)) > float(base.get("S", 0.0)) + float(
        cfg.threshold_improve or 0.0
    )
    return {
        "best_answer": str(best.get("answer", base_answer)),
        "best": best,
        "base": base,
        "candidates": scored,
        "improved": improved,
    }


__all__ = ["PlanningConfig", "plan_best_answer"]
