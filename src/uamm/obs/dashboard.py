from __future__ import annotations

from typing import Any, Dict

from uamm.policy import cp_store


def _latency_summary(hist: Dict[str, Any]) -> Dict[str, Any]:
    count = int(hist.get("count", 0) or 0)
    sum_seconds = float(hist.get("sum", 0.0) or 0.0)
    if count <= 0:
        return {"count": 0, "average": None, "p95": None}
    average = sum_seconds / count if count else None
    p95 = hist.get("p95")
    return {"count": count, "average": average, "p95": p95}


def build_dashboard_summary(
    metrics_state: Dict[str, Any], settings: Any
) -> Dict[str, Any]:
    latency_block = metrics_state.get("latency")
    if not latency_block:
        latency_block = _latency_summary(metrics_state.get("answer_latency", {}) or {})
    acceptance = {
        "answers": int(metrics_state.get("answers", 0) or 0),
        "accepted": int(metrics_state.get("accept", 0) or 0),
        "abstain": int(metrics_state.get("abstain", 0) or 0),
        "iterate": int(metrics_state.get("iterate", 0) or 0),
        "rates": metrics_state.get("rates") or {},
    }
    cp_stats = cp_store.domain_stats(settings.db_path)
    alerts = metrics_state.get("alerts", {}) or {}
    governance = {
        "events": len(metrics_state.get("gov_events", []) or []),
        "failures": int(metrics_state.get("gov_failures", 0) or 0),
    }
    return {
        "latency": latency_block,
        "acceptance": acceptance,
        "cp": {
            "stats": cp_stats,
            "target_mis": settings.cp_target_mis,
            "alerts": alerts.get("cp", {}),
        },
        "governance": governance,
        "alerts": alerts,
    }
