from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from uamm.config.settings import Settings, load_settings
from uamm.evals.runner import run_evals
from uamm.policy.cp_reference import quantiles_from_scores, upsert_reference
from uamm.policy.cp_store import add_artifacts, compute_threshold, domain_stats
from uamm.storage.db import ensure_schema


_DEFAULT_QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVALS_DIR = _REPO_ROOT / "evals"


@dataclass(frozen=True)
class EvalSuite:
    id: str
    label: str
    path: str
    description: str
    category: str
    cp_enabled: bool = False
    use_cp_decision: Optional[bool] = None
    max_refinements: int = 0
    tool_budget_per_refinement: int = 0
    tool_budget_per_turn: int = 0
    record_cp_artifacts: bool = False
    tags: tuple[str, ...] = ()

    @property
    def dataset_path(self) -> Path:
        return (_EVALS_DIR / self.path).resolve()


SUITES: Dict[str, EvalSuite] = {
    "UQ-A1": EvalSuite(
        id="UQ-A1",
        label="UQ calibration smoke",
        description="Short SNNE sanity checks to ensure paraphrase sampling behaves.",
        category="UQ",
        path="uq_a1.json",
        cp_enabled=False,
        tags=("smoke", "uq"),
    ),
    "CP-B1": EvalSuite(
        id="CP-B1",
        label="CP bootstrap smoke",
        description="Validates conformal gate behaviour and updates τ baselines.",
        category="CP",
        path="cp_b1.json",
        cp_enabled=True,
        use_cp_decision=True,
        record_cp_artifacts=True,
        tags=("cp", "smoke"),
    ),
    "CP-B1-EXT": EvalSuite(
        id="CP-B1-EXT",
        label="CP extended coverage",
        description="Extended CP eval covering analytics and governance cases.",
        category="CP",
        path="cp_b1_extended.json",
        cp_enabled=True,
        use_cp_decision=True,
        record_cp_artifacts=True,
        tags=("cp", "extended"),
    ),
    "RAG-C1": EvalSuite(
        id="RAG-C1",
        label="Hybrid RAG coverage",
        description="Ensures retrieved context is used when forming answers.",
        category="RAG",
        path="rag_c1.json",
        tags=("rag",),
    ),
    "PCN-D1": EvalSuite(
        id="PCN-D1",
        label="PCN verification",
        description="Exercises numeric verification and provenance logging.",
        category="PCN",
        path="pcn_d1.json",
        tags=("pcn", "math"),
    ),
    "GoV-E1": EvalSuite(
        id="GoV-E1",
        label="Governance DAG checks",
        description="Validates graph-of-verification signalling for policy workflows.",
        category="GoV",
        path="gov_e1.json",
        tags=("gov",),
    ),
    "Refine-F1": EvalSuite(
        id="Refine-F1",
        label="Refinement improvement",
        description="Checks that borderline answers refine successfully using tools.",
        category="Refine",
        path="refine_f1.json",
        max_refinements=1,
        tool_budget_per_refinement=2,
        tool_budget_per_turn=2,
        tags=("refine",),
    ),
    "Stack-G1": EvalSuite(
        id="Stack-G1",
        label="Full stack smoke",
        description="End-to-end seatbelt run spanning citations, math, and uncertainty.",
        category="Stack",
        path="stack_g1.json",
        tags=("stack", "smoke"),
    ),
    # Benchmarks (small offline subsets)
    "HPQA-S": EvalSuite(
        id="HPQA-S",
        label="HotpotQA small",
        description="Tiny subset to sanity-check multi-hop reasoning flows.",
        category="Bench",
        path="hotpotqa_small.json",
        cp_enabled=True,
        use_cp_decision=True,
        tags=("bench",),
    ),
    "STRAT-S": EvalSuite(
        id="STRAT-S",
        label="StrategyQA small",
        description="Tiny subset for commonsense questions.",
        category="Bench",
        path="strategyqa_small.json",
        cp_enabled=True,
        use_cp_decision=True,
        tags=("bench",),
    ),
    "MMLU-S": EvalSuite(
        id="MMLU-S",
        label="MMLU small",
        description="Tiny subset of academic knowledge questions.",
        category="Bench",
        path="mmlu_small.json",
        cp_enabled=True,
        use_cp_decision=True,
        tags=("bench",),
    ),
}


def list_suites() -> List[EvalSuite]:
    return sorted(SUITES.values(), key=lambda s: s.id)


def get_suite(suite_id: str) -> EvalSuite:
    try:
        return SUITES[suite_id]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"Unknown eval suite '{suite_id}'") from exc


def load_items(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Eval dataset not found: {path}")
    data = path.read_text(encoding="utf-8")
    import json

    payload = json.loads(data)
    if isinstance(payload, dict):
        items = payload.get("items", [])
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError(f"Unexpected dataset format for {path}")
    return [dict(item) for item in items]


def summarize_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    recs = list(records)
    total = len(recs)
    if total == 0:
        return {
            "total": 0,
            "accuracy": None,
            "accept_rate": None,
            "false_accept_rate": None,
            "avg_score": None,
            "cp_accept_rate": None,
            "correct": 0,
            "accepted": 0,
            "false_accept": 0,
        }
    correct = sum(1 for r in recs if r.get("correct"))
    accepted = sum(1 for r in recs if r.get("accepted"))
    false_accept = sum(1 for r in recs if r.get("accepted") and not r.get("correct"))
    cp_accept = [r.get("cp_accept") for r in recs if r.get("cp_accept") is not None]
    avg_score = sum(float(r.get("S", 0.0) or 0.0) for r in recs) / total
    # Optional enrichments
    tools_list = [int(r.get("tools", 0) or 0) for r in recs if "tools" in r]
    avg_tools = (sum(tools_list) / len(tools_list)) if tools_list else None
    faith_list = [float(r.get("faithfulness")) for r in recs if isinstance(r.get("faithfulness"), (float, int))]
    avg_faith = (sum(faith_list) / len(faith_list)) if faith_list else None
    plan_list = [bool(r.get("planning_improved")) for r in recs if "planning_improved" in r]
    plan_improve_rate = (sum(1 for v in plan_list if v) / len(plan_list)) if plan_list else None
    toks_list = [int(r.get("tokens_estimate")) for r in recs if isinstance(r.get("tokens_estimate"), int)]
    avg_tokens = (sum(toks_list) / len(toks_list)) if toks_list else None
    cost_list = [float(r.get("cost_estimate")) for r in recs if isinstance(r.get("cost_estimate"), (float, int))]
    total_cost = sum(cost_list) if cost_list else 0.0
    return {
        "total": total,
        "accuracy": correct / total,
        "accept_rate": accepted / total,
        "false_accept_rate": (false_accept / accepted) if accepted else 0.0,
        "avg_score": avg_score,
        "cp_accept_rate": (sum(1 for v in cp_accept if v) / len(cp_accept))
        if cp_accept
        else None,
        "correct": correct,
        "accepted": accepted,
        "false_accept": false_accept,
        "avg_tools": avg_tools,
        "avg_faithfulness": avg_faith,
        "planning_improve_rate": plan_improve_rate,
        "avg_tokens": avg_tokens,
        "total_cost": total_cost,
    }


def summarize_by_domain(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        domain = str(record.get("domain", "default"))
        grouped.setdefault(domain, []).append(record)
    return {dom: summarize_records(recs) for dom, recs in grouped.items()}


def run_suite(
    suite_id: str,
    *,
    run_id: str | None = None,
    settings: Settings | None = None,
    update_cp_reference: bool = True,
) -> Dict[str, Any]:
    suite = get_suite(suite_id)
    settings = settings or load_settings()
    ensure_schema(settings.db_path, settings.schema_path)
    items = load_items(suite.dataset_path)
    records = run_evals(
        items=items,
        accept_threshold=settings.accept_threshold,
        cp_enabled=suite.cp_enabled,
        tool_budget_per_refinement=suite.tool_budget_per_refinement,
        tool_budget_per_turn=suite.tool_budget_per_turn,
        max_refinements=suite.max_refinements,
        use_cp_decision=suite.use_cp_decision,
    )
    metrics = summarize_records(records)
    by_domain = summarize_by_domain(records)

    result: Dict[str, Any] = {
        "suite_id": suite.id,
        "label": suite.label,
        "description": suite.description,
        "category": suite.category,
        "metrics": metrics,
        "by_domain": by_domain,
        "records": records,
    }

    if update_cp_reference and suite.record_cp_artifacts:
        cp_snapshot: Dict[str, Any] = {}
        total_inserted = 0
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for rec in records:
            dom = str(rec.get("domain", "default"))
            grouped.setdefault(dom, []).append(rec)
        for dom, recs in grouped.items():
            tuples = [
                (float(r["S"]), bool(r["accepted"]), bool(r["correct"])) for r in recs
            ]
            inserted = add_artifacts(
                settings.db_path,
                run_id=run_id or suite.id,
                domain=dom,
                items=tuples,
            )
            total_inserted += inserted
            tau = compute_threshold(
                settings.db_path,
                domain=dom,
                target_mis=settings.cp_target_mis,
            )
            stats = domain_stats(settings.db_path, domain=dom).get(dom, {})
            quantiles = quantiles_from_scores(
                [float(r["S"]) for r in recs], _DEFAULT_QUANTILES
            )
            upsert_reference(
                settings.db_path,
                domain=dom,
                run_id=run_id or suite.id,
                target_mis=settings.cp_target_mis,
                tau=tau,
                stats=stats,
                snne_quantiles=quantiles,
            )
            cp_snapshot[dom] = {
                "tau": tau,
                "quantiles": quantiles,
                "stats": stats,
                "inserted": inserted,
            }
        result["cp_reference"] = {"domains": cp_snapshot, "inserted": total_inserted}
    return result
