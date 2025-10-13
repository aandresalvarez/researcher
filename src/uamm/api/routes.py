from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, Iterable, List
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from pydantic import BaseModel, Field
from uamm.models.schemas import AgentResultModel, StepTraceModel
from uamm.policy.policy import PolicyConfig
from uamm.policy import cp_store
from uamm.policy.cp_reference import (
    get_reference,
    quantiles_from_scores,
    upsert_reference,
)
from uamm.policy.drift import (
    compute_quantile_drift,
    needs_attention,
    recent_scores,
    rolling_false_accept_rate,
)
from uamm.evals.runner import run_evals
from uamm.evals.suites import (
    run_suite as run_eval_suite,
    summarize_by_domain as suite_summarize_by_domain,
    summarize_records as suite_summarize_records,
)
from uamm.evals.orchestrator import run_suites
from uamm.evals.storage import store_eval_run, fetch_eval_run
from uamm.agents.main_agent import MainAgent
from uamm.security.redaction import redact
from uamm.storage.db import insert_step

# from uamm.rag.retriever import retrieve
from uamm.rag.corpus import add_doc as rag_add_doc, search_docs as rag_search_docs
from uamm.api.state import IdempotencyStore
from uamm.storage.memory import (
    add_memory as db_add_memory,
    search_memory as db_search_memory,
)
from uamm.models.schemas import MemoryPackItem
from uamm.obs.logger import log_step
from uamm.obs.dashboard import build_dashboard_summary
from uamm.security.sql_guard import is_read_only_select, tables_allowed
from uamm.tools.table_query import table_query as db_table_query
from uamm.tuner import TunerAgent, TunerTargets
from uamm.rag.vector_store import LanceDBUnavailable, upsert_document_embedding
from uamm.gov.executor import evaluate_dag
from uamm.gov.validator import validate_dag


router = APIRouter()


DRIFT_QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
_LAT_BUCKET_KEYS = ["0.1", "0.5", "1", "2.5", "6", "+Inf"]
_LAT_BUCKET_VALUES = [0.1, 0.5, 1.0, 2.5, 6.0, float("inf")]


def _latency_total(buckets: Dict[str, int]) -> int:
    return sum(int(buckets.get(k, 0) or 0) for k in _LAT_BUCKET_KEYS)


def _estimate_latency_quantile(hist: Dict[str, Any], quantile: float) -> float | None:
    buckets: Dict[str, int] = {
        str(k): int(v) for k, v in (hist.get("buckets", {}) or {}).items()
    }
    total = _latency_total(buckets)
    if total <= 0 or not 0.0 < quantile <= 1.0:
        return None
    rank = quantile * total
    cumulative = 0
    for key, upper in zip(_LAT_BUCKET_KEYS, _LAT_BUCKET_VALUES):
        cumulative += buckets.get(key, 0)
        if cumulative >= rank:
            return upper
    return float("inf")


def _latency_summary(hist: Dict[str, Any]) -> Dict[str, Any]:
    count = int(hist.get("count", 0) or 0)
    sum_seconds = float(hist.get("sum", 0.0) or 0.0)
    if count <= 0:
        return {"count": 0, "average": None, "p95": None}
    p95 = _estimate_latency_quantile(hist, 0.95)
    average = sum_seconds / count if count else None
    return {"count": count, "average": average, "p95": p95}


def _group_records_by_domain(
    records: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        dom = str(record.get("domain", "default"))
        grouped.setdefault(dom, []).append(record)
    return grouped


def _ensure_uq_stats(container: Dict[str, Any]) -> Dict[str, Any]:
    container.setdefault("events", 0)
    container.setdefault("raw_sum", 0.0)
    container.setdefault("raw_count", 0)
    container.setdefault("normalized_sum", 0.0)
    container.setdefault("normalized_count", 0)
    container.setdefault("samples_total", 0)
    container.setdefault("last", None)
    return container


def _update_uq_metrics(
    metrics: Dict[str, Any], uq_events: list[Dict[str, Any]], domain: str
) -> None:
    if not uq_events:
        return
    global_stats = _ensure_uq_stats(metrics.setdefault("uq", {}))
    domain_map = metrics.setdefault("uq_by_domain", {})
    domain_stats_local = _ensure_uq_stats(domain_map.setdefault(domain, {}))
    targets = (global_stats, domain_stats_local)
    last_event = uq_events[-1]
    for stats in targets:
        stats["events"] += len(uq_events)
        stats["last"] = last_event
    for event in uq_events:
        raw = event.get("raw")
        normalized = event.get("normalized")
        samples = event.get("samples")
        if isinstance(raw, (int, float)):
            for stats in targets:
                stats["raw_sum"] += float(raw)
                stats["raw_count"] += 1
        if isinstance(normalized, (int, float)):
            for stats in targets:
                stats["normalized_sum"] += float(normalized)
                stats["normalized_count"] += 1
        if isinstance(samples, list):
            add = len(samples)
            for stats in targets:
                stats["samples_total"] += add


def _bucket_event_lists(
    events: list[tuple[str, Dict[str, Any]]],
    existing_pcn: Dict[str, Dict[str, Any]] | None = None,
) -> tuple[
    Dict[str, Dict[str, Any]],
    list[Dict[str, Any]],
    list[Dict[str, Any]],
    list[Dict[str, Any]],
    list[Dict[str, Any]],
]:
    pcn_map: Dict[str, Dict[str, Any]] = dict(existing_pcn or {})
    tool_events: list[Dict[str, Any]] = []
    score_events: list[Dict[str, Any]] = []
    uq_events: list[Dict[str, Any]] = []
    gov_events: list[Dict[str, Any]] = []
    for evt, data in events:
        if evt == "pcn":
            pid = str(data.get("id", ""))
            if not pid:
                continue
            typ = data.get("type")
            if typ == "pcn_pending":
                pcn_map[pid] = {
                    "status": "pending",
                    "policy": data.get("policy"),
                    "prov": data.get("provenance"),
                }
            elif typ == "pcn_verified":
                pcn_map[pid] = {
                    "status": "verified",
                    "value": data.get("value"),
                    "policy": data.get("policy"),
                    "prov": data.get("provenance"),
                }
            elif typ == "pcn_failed":
                pcn_map[pid] = {
                    "status": "failed",
                    "reason": data.get("reason"),
                    "policy": data.get("policy"),
                    "prov": data.get("provenance"),
                }
        elif evt == "tool":
            tool_events.append(data)
        elif evt == "score":
            score_events.append(data)
        elif evt == "uq":
            uq_events.append(data)
        elif evt == "gov":
            gov_events.append(data)
    return pcn_map, tool_events, score_events, uq_events, gov_events


def _prepare_trace_blob(
    final: AgentResultModel,
    events: list[tuple[str, Dict[str, Any]]],
    existing_pcn: Dict[str, Dict[str, Any]] | None = None,
) -> tuple[str, Dict[str, Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    pcn_map, tool_events, score_events, uq_events, gov_events = _bucket_event_lists(
        events, existing_pcn
    )
    full_trace = [t.model_dump(mode="json") for t in final.trace]
    pack_used = [p.model_dump(mode="json") for p in final.pack_used]
    trace_blob = json.dumps(
        {
            "trace": full_trace,
            "events": {
                "tool": tool_events,
                "score": score_events,
                "uq": uq_events,
                "pcn": pcn_map,
                "gov": gov_events,
            },
            "pack_used": pack_used,
        }
    )
    return trace_blob, pcn_map, gov_events, uq_events


def _bucketize_latency(ms: int) -> str:
    s = ms / 1000.0
    if s <= 0.1:
        return "0.1"
    if s <= 0.5:
        return "0.5"
    if s <= 1.0:
        return "1"
    if s <= 2.5:
        return "2.5"
    if s <= 6.0:
        return "6"
    return "+Inf"


def _persist_trace_and_metrics(
    request: Request,
    req: AnswerRequest,
    final: AgentResultModel,
    last: StepTraceModel,
    trace_blob: str,
    q_red: str,
    gov_events: list[Dict[str, Any]],
    uq_events: list[Dict[str, Any]],
    first_token_ms: int | None = None,
) -> None:
    settings = request.app.state.settings
    a_red, _ = redact(final.final)
    metrics_state = request.app.state.metrics
    _update_uq_metrics(metrics_state, uq_events, req.domain)
    if gov_events:
        metrics_state.setdefault("gov_events", []).extend(gov_events)
        failure_count = sum(
            1 for evt in gov_events if not evt.get("dag_delta", {}).get("ok", True)
        )
        if failure_count:
            metrics_state["gov_failures"] = (
                metrics_state.get("gov_failures", 0) + failure_count
            )
    first_token = first_token_ms if first_token_ms is not None else last.latency_ms
    insert_step(
        settings.db_path,
        question_redacted=q_red,
        answer_redacted=a_red,
        s1=last.s1_or_snne,
        s2=last.s2,
        final_score=last.final_score,
        cp_accept=last.cp_accept,
        action=last.action,
        reason=last.reason,
        is_refinement=last.is_refinement,
        status="ok",
        latency_ms=last.latency_ms,
        usage=last.usage,
        pack_ids=[p.id for p in final.pack_used],
        issues=last.issues,
        tools_used=last.tools_used,
        change_summary=last.change_summary,
        domain=req.domain,
        trace_json=trace_blob,
    )
    log_step(
        {
            "rid": getattr(request.state, "request_id", ""),
            "domain": req.domain,
            "action": last.action,
            "s1": last.s1_or_snne,
            "s2": last.s2,
            "S": last.final_score,
            "cp": last.cp_accept,
            "ms": last.latency_ms,
            "ft_ms": first_token,
            "tools": len(last.tools_used),
            "snne_raw": final.uncertainty.snne_raw,
            "snne_samples": final.uncertainty.snne_sample_count,
        }
    )
    metrics = metrics_state
    metrics["answers"] += 1
    if last.action == "abstain":
        metrics["abstain"] += 1
    elif last.action == "accept":
        metrics["accept"] += 1
    elif last.action == "iterate":
        metrics["iterate"] += 1
    by_dom = metrics.setdefault("by_domain", {})
    dom = req.domain
    if dom not in by_dom:
        by_dom[dom] = {"answers": 0, "abstain": 0, "accept": 0, "iterate": 0}
    by_dom[dom]["answers"] += 1
    if last.action == "abstain":
        by_dom[dom]["abstain"] += 1
    elif last.action == "accept":
        by_dom[dom]["accept"] += 1
    elif last.action == "iterate":
        by_dom[dom]["iterate"] += 1
    b = _bucketize_latency(last.latency_ms)
    ans_lat = metrics.setdefault(
        "answer_latency", {"buckets": {}, "sum": 0.0, "count": 0}
    )
    ans_lat["buckets"][b] = ans_lat["buckets"].get(b, 0) + 1
    ans_lat["sum"] = float(ans_lat.get("sum", 0.0)) + (last.latency_ms / 1000.0)
    ans_lat["count"] = ans_lat.get("count", 0) + 1
    by_dom_lat = metrics.setdefault("answer_latency_by_domain", {})
    dom_lat = by_dom_lat.setdefault(dom, {"buckets": {}, "sum": 0.0, "count": 0})
    dom_lat["buckets"][b] = dom_lat["buckets"].get(b, 0) + 1
    dom_lat["sum"] = float(dom_lat.get("sum", 0.0)) + (last.latency_ms / 1000.0)
    dom_lat["count"] = dom_lat.get("count", 0) + 1
    if first_token_ms is None:
        first_token_ms = last.latency_ms
    ft_bucket = _bucketize_latency(first_token_ms)
    ft_lat = metrics.setdefault(
        "first_token_latency", {"buckets": {}, "sum": 0.0, "count": 0}
    )
    ft_lat["buckets"][ft_bucket] = ft_lat["buckets"].get(ft_bucket, 0) + 1
    ft_lat["sum"] = float(ft_lat.get("sum", 0.0)) + (first_token_ms / 1000.0)
    ft_lat["count"] = ft_lat.get("count", 0) + 1
    ft_dom_map = metrics.setdefault("first_token_latency_by_domain", {})
    ft_dom = ft_dom_map.setdefault(dom, {"buckets": {}, "sum": 0.0, "count": 0})
    ft_dom["buckets"][ft_bucket] = ft_dom["buckets"].get(ft_bucket, 0) + 1
    ft_dom["sum"] = float(ft_dom.get("sum", 0.0)) + (first_token_ms / 1000.0)
    ft_dom["count"] = ft_dom.get("count", 0) + 1


def _ensure_last_trace(final: AgentResultModel, latency_ms: int) -> StepTraceModel:
    if final.trace:
        last = final.trace[-1]
        last.latency_ms = latency_ms
        return last
    return StepTraceModel(
        step_index=0,
        is_refinement=False,
        s1_or_snne=final.uncertainty.snne or 1.0,
        s2=final.uncertainty.s2,
        final_score=final.uncertainty.final_score,
        cp_accept=final.uncertainty.cp_accept,
        issues=[],
        tools_used=[],
        action="abstain",
        reason=final.stop_reason,
        latency_ms=latency_ms,
        usage=final.usage or {},
    )


class AnswerRequest(BaseModel):
    question: str = Field(
        ...,
        description="Natural-language question or instruction to answer.",
        examples=["Summarise the value of modular memory for analytics teams."],
    )
    use_memory: bool = Field(
        True,
        description="Whether to retrieve personalized memory items alongside corpus hits.",
    )
    memory_budget: int = Field(
        8,
        description="Maximum combined memory + corpus snippets to include in the evidence pack.",
    )
    stream: bool = Field(
        True,
        description="Set false to force a single JSON response even when hitting the streaming endpoint.",
    )
    max_refinements: int = Field(
        2,
        description="Maximum number of tool-guided refinement loops when the verifier flags issues.",
    )
    borderline_delta: float = Field(
        0.05,
        description="Delta added to the accept threshold to decide when to refine vs accept.",
    )
    domain: str = Field(
        "default",
        description="Domain used for calibration (controls CP τ lookup and SNNE normalisation).",
    )
    uq_mode: str = Field(
        "snne",
        description="Uncertainty mode: `snne`, `se`, or `logprob` (SNNE recommended).",
    )
    snne_samples: int = Field(
        5,
        description="Number of paraphrased variants to sample for SNNE scoring.",
    )
    cp_target_mis: float = Field(
        0.05,
        description="Target miscoverage rate for conformal policy (lower means more conservative).",
    )
    approved_tools: list[str] = Field(
        default_factory=list,
        description="Whitelist of tools already approved for this request (bypasses pause).",
    )
    llm_model: str | None = Field(
        None,
        description="Optional override for the primary LLM identifier (defaults to GPT-5 per settings).",
    )
    llm_instructions: str | None = Field(
        None,
        description="Optional operator guidance appended to the main agent system prompt.",
    )


class TunerProposeRequest(BaseModel):
    suite_ids: list[str] | None = None
    targets: Dict[str, float] | None = None
    metrics: Dict[str, Any] | None = None
    update_cp_reference: bool = False


class TunerApplyRequest(BaseModel):
    proposal_id: str
    approved: bool
    reason: str | None = None


@router.post(
    "/agent/answer",
    response_model=AgentResultModel,
    summary="Generate grounded answer",
    tags=["Agent"],
)
def answer(
    req: AnswerRequest, request: Request, response: Response
) -> AgentResultModel:
    """Return a grounded answer with calibrated uncertainty and trace metadata.

    Use this endpoint when you need the full JSON payload in one response. Include
    `X-Idempotency-Key` to safely retry requests, and optionally `X-Approval-ID`
    when resuming a tool execution that required human approval.
    """
    # Redact incoming question before any persistence
    q_red, _ = redact(req.question)
    t0 = time.time()
    settings = request.app.state.settings
    if "borderline_delta" not in req.model_fields_set:
        req.borderline_delta = getattr(
            settings, "borderline_delta", req.borderline_delta
        )
    if "snne_samples" not in req.model_fields_set:
        req.snne_samples = getattr(settings, "snne_samples", req.snne_samples)
    if "max_refinements" not in req.model_fields_set:
        req.max_refinements = getattr(
            settings, "max_refinement_steps", req.max_refinements
        )
    if "cp_target_mis" not in req.model_fields_set:
        req.cp_target_mis = getattr(settings, "cp_target_mis", req.cp_target_mis)
    policy = PolicyConfig(
        tau_accept=settings.accept_threshold,
        delta=req.borderline_delta,
    )
    # CP threshold supplier from app state
    tau_supplier = getattr(request.app.state, "cp_tau_supplier", lambda: None)
    # Domain-aware CP enablement: auto-enable if τ available
    cp_enabled_for_call = settings.cp_enabled
    tau_supplier = getattr(
        request.app.state, "cp_tau_supplier", lambda *args, **kwargs: None
    )
    if not cp_enabled_for_call and settings.cp_auto_enable:
        try:
            if tau_supplier(req.domain) is not None:
                cp_enabled_for_call = True
        except Exception:
            pass
    agent = MainAgent(cp_enabled=cp_enabled_for_call, policy=policy)
    # Idempotency support (non-streaming): replay final if available
    idem_key = request.headers.get("X-Idempotency-Key")
    response.headers["X-Request-ID"] = getattr(request.state, "request_id", "")
    if idem_key:
        response.headers["X-Idempotency-Key"] = idem_key
        store: IdempotencyStore = request.app.state.idem_store
        cached = store.get(idem_key)
        if cached:
            return AgentResultModel(**cached)
    # metrics
    request.app.state.metrics["requests"] += 1
    params = req.model_dump()
    # ensure budgets from settings are available to agent
    params.setdefault(
        "tool_budget_per_refinement", getattr(settings, "tool_budget_per_refinement", 2)
    )
    params.setdefault(
        "tool_budget_per_turn", getattr(settings, "tool_budget_per_turn", 4)
    )
    params.setdefault("max_refinements", req.max_refinements)
    params.setdefault("snne_samples", req.snne_samples)
    params.setdefault("snne_tau", getattr(settings, "snne_tau", 0.3))
    params.setdefault("cp_target_mis", req.cp_target_mis)
    params.setdefault("db_path", settings.db_path)
    params.setdefault("rag_weight_sparse", getattr(settings, "rag_weight_sparse", 0.5))
    params.setdefault("rag_weight_dense", getattr(settings, "rag_weight_dense", 0.5))
    params.setdefault("vector_backend", getattr(settings, "vector_backend", "none"))
    params.setdefault("lancedb_uri", getattr(settings, "lancedb_uri", ""))
    params.setdefault(
        "lancedb_table", getattr(settings, "lancedb_table", "rag_vectors")
    )
    params.setdefault("lancedb_metric", getattr(settings, "lancedb_metric", "cosine"))
    params.setdefault("lancedb_k", getattr(settings, "lancedb_k", None))
    # Egress policy params
    params.setdefault(
        "egress_block_private_ip", getattr(settings, "egress_block_private_ip", True)
    )
    params.setdefault(
        "egress_enforce_tls", getattr(settings, "egress_enforce_tls", True)
    )
    params.setdefault(
        "egress_allow_redirects", getattr(settings, "egress_allow_redirects", 3)
    )
    params.setdefault(
        "egress_max_payload_bytes",
        getattr(settings, "egress_max_payload_bytes", 5 * 1024 * 1024),
    )
    params.setdefault(
        "egress_allowlist_hosts", getattr(settings, "egress_allowlist_hosts", [])
    )
    params.setdefault(
        "egress_denylist_hosts", getattr(settings, "egress_denylist_hosts", [])
    )
    # Tool approvals config
    params["tools_requiring_approval"] = params.get(
        "tools_requiring_approval"
    ) or getattr(settings, "tools_requiring_approval", [])
    params.setdefault("approvals", getattr(request.app.state, "approvals", None))
    approval_token = request.headers.get("X-Approval-ID")
    approvals_store = getattr(request.app.state, "approvals", None)
    if approval_token:
        if approvals_store is None:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "approvals_unavailable",
                    "message": "approvals store not initialized",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        info = approvals_store.get(approval_token)
        if not info:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "approval_not_found",
                    "message": "approval id not recognized or expired",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        status = info.get("status")
        context = info.get("context") or {}
        tool_name = context.get("tool")
        if status == "pending":
            return JSONResponse(
                status_code=202,
                content={
                    "status": "waiting_approval",
                    "approval_id": approval_token,
                    "message": "Tool approval still pending",
                },
            )
        if status == "denied":
            approvals_store.consume(approval_token)
            return JSONResponse(
                status_code=403,
                content={
                    "status": "approval_denied",
                    "approval_id": approval_token,
                    "message": info.get("reason") or "Tool approval denied",
                },
            )
        if status == "approved":
            approved_tools = set(params.get("approved_tools", []) or [])
            if tool_name:
                approved_tools.add(tool_name)
            params["approved_tools"] = list(approved_tools)
            approvals_store.consume(approval_token)
    # Inject domain-aware threshold supplier into CP if enabled
    agent._cp._get_tau = lambda: tau_supplier(req.domain)  # type: ignore[attr-defined]
    # Collect PCN/events for persistence (non-streaming)
    events: list[tuple[str, dict]] = []

    def _emit(evt: str, data: dict) -> None:
        events.append((evt, data))

    result = agent.answer(params=params, emit=_emit)
    # Approval pending shortcut for non-streaming: return 202 with approval id
    if isinstance(result, dict) and result.get("stop_reason") == "approval_pending":
        pending = result.get("pending_approvals") or []
        appr_id = pending[0] if pending else None
        return JSONResponse(
            status_code=202,
            content={
                "status": "waiting_approval",
                "approval_id": appr_id,
                "message": "Tool approval required to continue",
            },
        )
    # Build final and step from agent result
    final = AgentResultModel(**result)
    latency_ms = int((time.time() - t0) * 1000)
    last = _ensure_last_trace(final, latency_ms)
    trace_blob, _, gov_events, uq_events = _prepare_trace_blob(final, events)
    _persist_trace_and_metrics(
        request,
        req,
        final,
        last,
        trace_blob,
        q_red,
        gov_events,
        uq_events,
        first_token_ms=latency_ms,
    )
    if idem_key:
        store = request.app.state.idem_store
        store.set(idem_key, final.model_dump())
    return final


def _sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\n" + "data: " + json.dumps(data) + "\n\n"


@router.post(
    "/agent/answer/stream",
    summary="Stream grounded answer (SSE)",
    tags=["Agent"],
    responses={
        200: {
            "description": "Server-Sent Events stream with ready/token/score/tool/final updates.",
            "content": {
                "text/event-stream": {
                    "example": (
                        "event: ready\\n"
                        'data: {"request_id":"123"}\\n\\n'
                        "event: token\\n"
                        'data: {"text":"Partial"}\\n\\n'
                        "event: final\\n"
                        'data: {"final":"answer","stop_reason":"accept"}\\n\\n'
                    )
                }
            },
        }
    },
)
def answer_stream(req: AnswerRequest, request: Request) -> Response:
    """Stream an answer over SSE after SNNE/CP approval.

    Events emitted:
    - `ready`: stream initialized (always first event).
    - `token`: text fragments of the approved answer (after policy gate).
    - `score`: SNNE/S₂ scores and CP τ used for the decision.
    - `tool`: tool execution lifecycle, including approval handshakes.
    - `trace`: refinement step summaries for observability.
    - `pcn`: verification status for numeric placeholders.
    - `heartbeat`: sent every ~15s to keep the connection alive.
    - `final`: complete `AgentResultModel` payload.
    """
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = "unknown"
    q_red, _ = redact(req.question)
    request.app.state.metrics["requests"] += 1
    t0 = time.time()
    settings = request.app.state.settings
    if "borderline_delta" not in req.model_fields_set:
        req.borderline_delta = getattr(
            settings, "borderline_delta", req.borderline_delta
        )
    if "snne_samples" not in req.model_fields_set:
        req.snne_samples = getattr(settings, "snne_samples", req.snne_samples)
    if "max_refinements" not in req.model_fields_set:
        req.max_refinements = getattr(
            settings, "max_refinement_steps", req.max_refinements
        )
    if "cp_target_mis" not in req.model_fields_set:
        req.cp_target_mis = getattr(settings, "cp_target_mis", req.cp_target_mis)
    policy = PolicyConfig(
        tau_accept=settings.accept_threshold,
        delta=req.borderline_delta,
    )
    tau_supplier = getattr(
        request.app.state, "cp_tau_supplier", lambda *args, **kwargs: None
    )
    cp_enabled_for_call = settings.cp_enabled
    if not cp_enabled_for_call and settings.cp_auto_enable:
        try:
            if tau_supplier(req.domain) is not None:
                cp_enabled_for_call = True
        except Exception:
            pass
    agent = MainAgent(cp_enabled=cp_enabled_for_call, policy=policy)
    idem_key = request.headers.get("X-Idempotency-Key")
    idem_store: IdempotencyStore = request.app.state.idem_store

    async def agen():
        # Idempotent replay path: return ready + final only
        if idem_key:
            cached = idem_store.get(idem_key)
            if cached:

                def se(evt: str, data: Dict[str, Any]) -> str:
                    data.setdefault("request_id", rid)
                    return _sse_event(evt, data)

                yield se("ready", {"request_id": rid})
                yield se("final", cached)
                return
        try:

            def se(evt: str, data: Dict[str, Any]) -> str:
                data.setdefault("request_id", rid)
                return _sse_event(evt, data)

            yield se("ready", {"request_id": rid})
            # Stream agent events in real time while keeping state for persistence.
            pcn_map: dict[str, dict] = {}

            def finalize_result(
                result_obj: Dict[str, Any], event_list: list[tuple[str, dict]]
            ) -> tuple[
                AgentResultModel,
                StepTraceModel,
                str,
                list[Dict[str, Any]],
                list[Dict[str, Any]],
                int,
            ]:
                nonlocal pcn_map
                final_model = AgentResultModel(**result_obj)
                latency_ms = int((time.time() - t0) * 1000)
                last = _ensure_last_trace(final_model, latency_ms)
                trace_blob, updated_pcn, gov_events, uq_events = _prepare_trace_blob(
                    final_model, event_list, pcn_map
                )
                pcn_map = updated_pcn
                metrics_state = request.app.state.metrics
                if gov_events:
                    metrics_state.setdefault("gov_events", []).extend(gov_events)
                    failure_count = sum(
                        1
                        for evt in gov_events
                        if not evt.get("dag_delta", {}).get("ok", True)
                    )
                    if failure_count:
                        metrics_state["gov_failures"] = (
                            metrics_state.get("gov_failures", 0) + failure_count
                        )
                return final_model, last, trace_blob, gov_events, uq_events, latency_ms

            import asyncio

            SENTINEL = "__agent_complete__"
            params = req.model_dump()
            params.setdefault(
                "tool_budget_per_refinement",
                getattr(settings, "tool_budget_per_refinement", 2),
            )
            params.setdefault(
                "tool_budget_per_turn", getattr(settings, "tool_budget_per_turn", 4)
            )
            params.setdefault("max_refinements", req.max_refinements)
            params.setdefault("snne_samples", req.snne_samples)
            params.setdefault("snne_tau", getattr(settings, "snne_tau", 0.3))
            params.setdefault("cp_target_mis", req.cp_target_mis)
            params.setdefault("db_path", settings.db_path)
            params.setdefault(
                "rag_weight_sparse", getattr(settings, "rag_weight_sparse", 0.5)
            )
            params.setdefault(
                "rag_weight_dense", getattr(settings, "rag_weight_dense", 0.5)
            )
            params.setdefault(
                "vector_backend", getattr(settings, "vector_backend", "none")
            )
            params.setdefault("lancedb_uri", getattr(settings, "lancedb_uri", ""))
            params.setdefault(
                "lancedb_table", getattr(settings, "lancedb_table", "rag_vectors")
            )
            params.setdefault(
                "lancedb_metric", getattr(settings, "lancedb_metric", "cosine")
            )
            params.setdefault("lancedb_k", getattr(settings, "lancedb_k", None))
            params.setdefault(
                "egress_block_private_ip",
                getattr(settings, "egress_block_private_ip", True),
            )
            params.setdefault(
                "egress_enforce_tls", getattr(settings, "egress_enforce_tls", True)
            )
            params.setdefault(
                "egress_allow_redirects", getattr(settings, "egress_allow_redirects", 3)
            )
            params.setdefault(
                "egress_max_payload_bytes",
                getattr(settings, "egress_max_payload_bytes", 5 * 1024 * 1024),
            )
            params.setdefault(
                "egress_allowlist_hosts",
                getattr(settings, "egress_allowlist_hosts", []),
            )
            params.setdefault(
                "egress_denylist_hosts", getattr(settings, "egress_denylist_hosts", [])
            )
            params["tools_requiring_approval"] = params.get(
                "tools_requiring_approval"
            ) or getattr(settings, "tools_requiring_approval", [])
            params.setdefault(
                "approvals", getattr(request.app.state, "approvals", None)
            )
            agent._cp._get_tau = lambda: tau_supplier(req.domain)  # type: ignore[attr-defined]
            loop = asyncio.get_running_loop()
            params_current = dict(params)
            events_last: list[tuple[str, dict]] = []
            result: Dict[str, Any] | None = None

            while True:
                cp_tau = tau_supplier(req.domain)
                pending_approval_id: str | None = None
                event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
                events_iter: list[tuple[str, dict]] = []

                def _emit(evt: str, data: dict) -> None:
                    try:
                        loop.call_soon_threadsafe(event_queue.put_nowait, (evt, data))
                    except RuntimeError:
                        pass

                def _run_agent() -> Dict[str, Any]:
                    try:
                        return agent.answer(params=params_current, emit=_emit)
                    finally:
                        loop.call_soon_threadsafe(
                            event_queue.put_nowait, (SENTINEL, {})
                        )

                agent_future = asyncio.create_task(asyncio.to_thread(_run_agent))
                try:
                    while True:
                        evt, data = await event_queue.get()
                        if evt == SENTINEL:
                            break
                        events_iter.append((evt, data))
                        if evt == "tool" and data.get("status") == "waiting_approval":
                            pending_approval_id = data.get("id")
                        elif evt == "pcn":
                            pid = str(data.get("id", ""))
                            typ = data.get("type")
                            if pid:
                                if typ == "pcn_pending":
                                    pcn_map[pid] = {
                                        "status": "pending",
                                        "policy": data.get("policy"),
                                        "provenance": data.get("provenance"),
                                    }
                                elif typ == "pcn_verified":
                                    pcn_map[pid] = {
                                        "status": "verified",
                                        "value": data.get("value"),
                                        "policy": data.get("policy"),
                                        "provenance": data.get("provenance"),
                                    }
                                elif typ == "pcn_failed":
                                    pcn_map[pid] = {
                                        "status": "failed",
                                        "reason": data.get("reason"),
                                        "policy": data.get("policy"),
                                        "provenance": data.get("provenance"),
                                    }
                        out_data = data
                        if evt == "score" and cp_tau is not None:
                            out_data = dict(data)
                            out_data["cp_tau"] = cp_tau
                        yield se(evt, out_data)
                except asyncio.CancelledError:
                    agent_future.cancel()
                    raise

                result = await agent_future
                events_last = events_iter

                if (
                    isinstance(result, dict)
                    and result.get("stop_reason") == "approval_pending"
                    and pending_approval_id
                ):
                    approvals_store = getattr(request.app.state, "approvals", None)
                    waited = 0
                    while True:
                        item = (
                            approvals_store.get(pending_approval_id)
                            if approvals_store
                            else None
                        )
                        if not item:
                            yield se(
                                "tool",
                                {
                                    "id": pending_approval_id,
                                    "name": "",
                                    "status": "expired",
                                },
                            )
                            yield se(
                                "final",
                                {
                                    "final": "approval expired",
                                    "stop_reason": "approval_expired",
                                    "uncertainty": {
                                        "mode": "snne",
                                        "snne": 1.0,
                                        "s2": 0.0,
                                        "final_score": 0.0,
                                        "cp_accept": False,
                                        "prediction_set_size": None,
                                    },
                                    "trace": [],
                                    "pack_used": [],
                                    "usage": {},
                                },
                            )
                            return
                        status = item.get("status")
                        if status in ("approved", "denied"):
                            tool_name = item.get("context", {}).get("tool")
                            yield se(
                                "tool",
                                {
                                    "id": pending_approval_id,
                                    "name": tool_name or "",
                                    "status": status,
                                },
                            )
                            if status == "denied":
                                if approvals_store:
                                    approvals_store.consume(pending_approval_id)
                                yield se(
                                    "final",
                                    {
                                        "final": "request denied by policy",
                                        "stop_reason": "approval_denied",
                                        "uncertainty": {
                                            "mode": "snne",
                                            "snne": 1.0,
                                            "s2": 0.0,
                                            "final_score": 0.0,
                                            "cp_accept": False,
                                            "prediction_set_size": None,
                                        },
                                        "trace": [],
                                        "pack_used": [],
                                        "usage": {},
                                    },
                                )
                                return
                            if approvals_store:
                                approvals_store.consume(pending_approval_id)
                            params_current = dict(params_current)
                            approved_tools = set(
                                params_current.get("approved_tools", []) or []
                            )
                            if tool_name:
                                approved_tools.add(tool_name)
                            params_current["approved_tools"] = list(approved_tools)
                            break
                        await asyncio.sleep(1)
                        waited += 1
                        if waited % 5 == 0:
                            yield se("heartbeat", {"t": int(time.time())})
                    continue
                break
            if not isinstance(result, dict):
                raise RuntimeError("agent returned unexpected payload")
            # Normal path: stream tokens then final
            final_model, last_step, trace_blob, gov_events, uq_events, latency_ms = (
                finalize_result(result, events_last)
            )
            final_payload = final_model.model_dump(mode="json")
            # Stream tokens for the final text with PCN gating
            heartbeat_sec = 15
            last_hb = int(time.time())
            usage_tokens = (
                final_model.usage.get("llm_tokens")
                if isinstance(final_model.usage, dict)
                else None
            )
            if isinstance(usage_tokens, list) and usage_tokens:
                raw_tokens = [str(tok) for tok in usage_tokens]
            else:
                raw_tokens = final_model.final.split()
            gated_tokens: list[str] = []
            for tok in raw_tokens:
                if tok.startswith("[PCN:") and tok.endswith("]"):
                    pid = tok[5:-1]
                    st = pcn_map.get(pid) if pid else None
                    if st and st.get("status") == "verified":
                        gated_tokens.append(str(st.get("value")))
                    else:
                        gated_tokens.append("[unverified]")
                else:
                    gated_tokens.append(tok)

            try:
                from anyio import CancelledError  # type: ignore
            except Exception:
                CancelledError = Exception  # type: ignore

            first_token_ms = None
            for tok in gated_tokens:
                try:
                    if first_token_ms is None:
                        first_token_ms = int((time.time() - t0) * 1000)
                    yield se("token", {"text": tok})
                    await asyncio.sleep(0)
                    now = int(time.time())
                    if now - last_hb >= heartbeat_sec:
                        yield se("heartbeat", {"t": now})
                        last_hb = now
                except CancelledError:  # pragma: no cover
                    yield se(
                        "error", {"code": "cancelled", "message": "client disconnected"}
                    )
                    return
            yield se("heartbeat", {"t": int(time.time())})
            if first_token_ms is None:
                first_token_ms = latency_ms
            _persist_trace_and_metrics(
                request,
                req,
                final_model,
                last_step,
                trace_blob,
                q_red,
                gov_events,
                uq_events,
                first_token_ms=first_token_ms,
            )
            # cache final for idempotency
            if idem_key:
                idem_store.set(idem_key, final_payload)
            yield se("final", final_payload)
        except Exception as e:  # pragma: no cover
            if os.getenv("UAMM_RERAISE_STREAM_ERRORS") == "1":
                raise
            yield se("error", {"code": "server_error", "message": str(e)})
            return

    resp = StreamingResponse(agen(), media_type="text/event-stream")
    if idem_key:
        resp.headers["X-Idempotency-Key"] = idem_key
    resp.headers["X-Request-ID"] = rid
    return resp


class MemoryAddRequest(BaseModel):
    text: str
    key: str = "fact:manual"
    domain: str = "fact"  # fact|trace|summary|tool


@router.post("/memory")
def memory_add(req: MemoryAddRequest, request: Request):
    """Persist a manual memory item for the active workspace/domain."""
    settings = request.app.state.settings
    red_text, _ = redact(req.text)
    mid = db_add_memory(
        settings.db_path,
        key=req.key,
        text=red_text,
        domain=req.domain,
    )
    return {"id": mid}


class RagDocRequest(BaseModel):
    title: str
    url: str | None = None
    text: str


@router.post("/rag/docs")
def rag_add(req: RagDocRequest, request: Request):
    """Ingest a document into the hybrid RAG corpus."""
    settings = request.app.state.settings
    red_text, _ = redact(req.text)
    did = rag_add_doc(settings.db_path, title=req.title, url=req.url, text=red_text)
    if getattr(settings, "vector_backend", "none").lower() == "lancedb":
        meta_payload = {"title": req.title or ""}
        if req.url:
            meta_payload["url"] = req.url
        try:
            upsert_document_embedding(settings, did, red_text, meta=meta_payload)
        except LanceDBUnavailable:
            logging.getLogger("uamm.rag.vector").warning(
                "lancedb_dependency_missing",
                extra={"doc_id": did, "uri": getattr(settings, "lancedb_uri", "")},
            )
        except Exception as exc:  # pragma: no cover - best effort cache
            logging.getLogger("uamm.rag.vector").warning(
                "lancedb_upsert_failed",
                extra={"doc_id": did, "error": str(exc)},
            )
    return {"id": did}


@router.get("/rag/search")
def rag_search(request: Request, q: str, k: int = 5):
    """Search the RAG corpus for relevant snippets."""
    settings = request.app.state.settings
    hits = rag_search_docs(settings.db_path, q, k=k)
    return {"hits": hits}


@router.get("/memory/search")
def memory_search(request: Request, q: str, k: int = 5):
    """Search previously stored memory items."""
    settings = request.app.state.settings
    hits = db_search_memory(settings.db_path, q, k=k)
    return {"hits": hits}


class MemoryPackRequest(BaseModel):
    question: str
    memory_budget: int = 8


@router.post("/memory/pack")
def memory_pack(req: MemoryPackRequest, request: Request):
    """Build a memory pack constrained by the supplied budget."""
    settings = request.app.state.settings
    hits = db_search_memory(settings.db_path, req.question, k=req.memory_budget)
    pack = [MemoryPackItem(**h) for h in hits]
    return {"pack": [p.model_dump() for p in pack]}


class PackMergeRequest(BaseModel):
    question: str
    memory_k: int = 8
    corpus_k: int = 8
    budget: int = 8
    min_score: float = 0.1


@router.post("/pack/merge")
def pack_merge(req: PackMergeRequest, request: Request):
    """Merge memory and corpus hits into a single prioritized pack."""
    settings = request.app.state.settings
    m_hits = db_search_memory(settings.db_path, req.question, k=req.memory_k)
    c_hits = rag_search_docs(settings.db_path, req.question, k=req.corpus_k)
    # Normalize corpus hits to MemoryPackItem shape
    c_norm = [
        {
            "id": h["id"],
            "snippet": h["snippet"],
            "why": h.get("why", "rag"),
            "score": h["score"],
        }
        for h in c_hits
    ]
    merged: Dict[str, Dict[str, Any]] = {}
    for h in m_hits + c_norm:
        if h["score"] < req.min_score:
            continue
        prev = merged.get(h["id"])
        if not prev or h["score"] > prev["score"]:
            merged[h["id"]] = h
    items = sorted(merged.values(), key=lambda x: x["score"], reverse=True)[
        : req.budget
    ]
    pack = [MemoryPackItem(**i) for i in items]
    return {"pack": [p.model_dump() for p in pack]}


class ToolsApproveRequest(BaseModel):
    approval_id: str
    approved: bool
    reason: str | None = None


@router.post("/tools/approve")
def tools_approve(req: ToolsApproveRequest, request: Request):
    """Approve or deny a pending tool call (stub store).

    This endpoint updates the in-memory approvals store. Full pause/resume
    orchestration will be integrated in a subsequent step.
    """
    store = getattr(request.app.state, "approvals", None)
    if not store:
        return JSONResponse(
            status_code=503,
            content={
                "code": "unavailable",
                "message": "approvals store not initialized",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )
    result = store.approve(req.approval_id, req.approved, req.reason)
    if not result:
        return JSONResponse(
            status_code=404,
            content={
                "code": "not_found",
                "message": "approval not found or expired",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )
    return result


@router.post("/evals/run")
def evals_run(request: Request, body: Dict[str, Any]):
    """Run eval suites or ad-hoc items and persist calibration artifacts."""
    settings = request.app.state.settings
    run_id = str(body.get("run_id") or f"run-{int(time.time())}")
    suite_field = body.get("suite_ids") or body.get("suite_id")
    update_cp = bool(body.get("update_cp", True))

    if suite_field:
        suite_ids = (
            list(suite_field) if isinstance(suite_field, list) else [suite_field]
        )
        result = run_suites(
            run_id,
            suite_ids=suite_ids,
            settings=settings,
            update_cp_reference=update_cp,
        )
        return result

    items = body.get("items") or []
    if not items:
        return {
            "run_id": run_id,
            "message": "no items provided",
            "suites": [],
        }

    default_domain = str(body.get("domain", "default"))
    tb_ref = int(body.get("tool_budget_per_refinement", 0) or 0)
    tb_turn = int(body.get("tool_budget_per_turn", 0) or 0)
    max_refinements = int(body.get("max_refinements", 0) or 0)
    cp_enabled = bool(body.get("cp_enabled", False))
    use_cp_decision = body.get("use_cp_decision")

    normalized_items = []
    for it in items:
        entry = dict(it)
        entry.setdefault("domain", default_domain)
        normalized_items.append(entry)

    records = run_evals(
        items=normalized_items,
        accept_threshold=settings.accept_threshold,
        cp_enabled=cp_enabled,
        tool_budget_per_refinement=tb_ref,
        tool_budget_per_turn=tb_turn,
        max_refinements=max_refinements,
        use_cp_decision=use_cp_decision,
    )

    metrics = suite_summarize_records(records)
    by_domain = suite_summarize_by_domain(records)
    response: Dict[str, Any] = {
        "run_id": run_id,
        "metrics": metrics,
        "by_domain": by_domain,
        "records": records,
    }

    if body.get("record_cp"):
        grouped = _group_records_by_domain(records)
        total_inserted = 0
        taus: Dict[str, float | None] = {}
        references: Dict[str, Dict[str, Any]] = {}
        for dom, recs in grouped.items():
            tuples = [
                (float(r["S"]), bool(r["accepted"]), bool(r["correct"])) for r in recs
            ]
            total_inserted += cp_store.add_artifacts(
                settings.db_path, run_id=run_id, domain=dom, items=tuples
            )
            tau = cp_store.compute_threshold(
                settings.db_path, domain=dom, target_mis=settings.cp_target_mis
            )
            taus[dom] = tau
            stats_dom = cp_store.domain_stats(settings.db_path, domain=dom).get(dom, {})
            quantiles = quantiles_from_scores(
                [float(r["S"]) for r in recs], DRIFT_QUANTILES
            )
            upsert_reference(
                settings.db_path,
                domain=dom,
                run_id=run_id,
                target_mis=settings.cp_target_mis,
                tau=tau,
                stats=stats_dom,
                snne_quantiles=quantiles,
            )
            references[dom] = {"tau": tau, "stats": stats_dom, "quantiles": quantiles}
        response["cp_reference"] = {"domains": references, "inserted": total_inserted}
        response["taus"] = taus
        response["cp_stats"] = cp_store.domain_stats(settings.db_path)

    store_eval_run(
        settings.db_path,
        run_id=run_id,
        suite_id=body.get("suite_name", "custom"),
        metrics=metrics,
        by_domain=by_domain,
        records=records,
        notes={"type": "custom", "item_count": len(records)},
    )
    return response


@router.get("/evals/report/{run_id}")
def evals_report(run_id: str, request: Request):
    """Return stored metrics and records for a specific evaluation run."""
    settings = request.app.state.settings
    runs = fetch_eval_run(settings.db_path, run_id)
    if not runs:
        return JSONResponse(
            status_code=404,
            content={
                "code": "not_found",
                "message": "eval run not found",
                "run_id": run_id,
            },
        )
    return {"run_id": run_id, "suites": runs}


@router.post("/tuner/propose")
def tuner_propose(req: TunerProposeRequest, request: Request):
    tuner_store = getattr(request.app.state, "tuner_store", None)
    if tuner_store is None:
        return JSONResponse(
            status_code=503,
            content={
                "code": "tuner_unavailable",
                "message": "tuner store not initialized",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    settings = request.app.state.settings
    suite_ids = req.suite_ids or ["CP-B1", "Stack-G1"]
    suite_results: List[Dict[str, Any]] = []
    for suite_id in suite_ids:
        try:
            suite_output = run_eval_suite(
                suite_id,
                run_id=f"tuner-{suite_id}-{int(time.time())}",
                settings=settings,
                update_cp_reference=bool(req.update_cp_reference),
            )
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "unknown_suite",
                    "message": f"suite '{suite_id}' not found",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        trimmed = {k: v for k, v in suite_output.items() if k != "records"}
        suite_results.append(trimmed)

    targets = TunerTargets.from_payload(req.targets)
    tuner_agent = TunerAgent(settings)
    proposal = tuner_agent.propose(
        suite_results=suite_results,
        targets=targets,
        metrics=req.metrics,
    )

    canary_summary: List[Dict[str, Any]] = []
    for result in suite_results:
        metrics = dict(result.get("metrics", {}) or {})
        status = "pass"
        reasons: List[str] = []
        fa = metrics.get("false_accept_rate")
        if fa is not None and fa > targets.false_accept_max:
            status = "warn"
            reasons.append(
                f"false_accept_rate {fa:.3f} > target {targets.false_accept_max:.3f}"
            )
        acc = metrics.get("accept_rate")
        if acc is not None and acc < targets.accept_min:
            status = "warn"
            reasons.append(f"accept_rate {acc:.3f} < target {targets.accept_min:.3f}")
        abstain = metrics.get("abstain_rate")
        if abstain is not None and abstain > targets.abstain_max:
            status = "warn"
            reasons.append(
                f"abstain_rate {abstain:.3f} > target {targets.abstain_max:.3f}"
            )
        latency = metrics.get("latency_p95")
        if latency is not None and latency > targets.latency_p95_max:
            status = "warn"
            reasons.append(
                f"latency_p95 {latency:.3f}s > target {targets.latency_p95_max:.3f}s"
            )
        canary_summary.append(
            {
                "suite_id": result.get("suite_id"),
                "status": status,
                "metrics": metrics,
                "reasons": reasons,
            }
        )

    proposal_dict = proposal.to_dict()
    payload = {
        "proposal": proposal_dict,
        "suite_results": suite_results,
        "targets": asdict(targets),
        "metrics": req.metrics or {},
        "canary": canary_summary,
    }
    proposal_id = str(uuid.uuid4())
    tuner_store.create(proposal_id, payload)
    return {
        "proposal_id": proposal_id,
        "requires_approval": proposal.requires_approval,
        "proposal": proposal_dict,
        "canary": canary_summary,
        "suite_results": suite_results,
    }


@router.post("/tuner/apply")
def tuner_apply(req: TunerApplyRequest, request: Request):
    tuner_store = getattr(request.app.state, "tuner_store", None)
    if tuner_store is None:
        return JSONResponse(
            status_code=503,
            content={
                "code": "tuner_unavailable",
                "message": "tuner store not initialized",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    item = tuner_store.get(req.proposal_id)
    if not item:
        return JSONResponse(
            status_code=404,
            content={
                "code": "proposal_not_found",
                "message": "proposal not found or expired",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    if not req.approved:
        tuner_store.set_status(req.proposal_id, "rejected", reason=req.reason)
        return {"proposal_id": req.proposal_id, "status": "rejected"}

    payload = item.get("payload", {})
    proposal = dict(payload.get("proposal", {}))
    config_patch = dict(proposal.get("config_patch", {}))

    settings = request.app.state.settings
    applied_changes: Dict[str, Any] = {}

    def _apply(key: str, value: Any) -> None:
        applied_changes[key] = value
        setattr(settings, key, value)

    for key, value in config_patch.items():
        if key == "accept_threshold":
            settings.accept_threshold = float(value)
            applied_changes[key] = settings.accept_threshold
        elif key == "borderline_delta":
            settings.borderline_delta = float(value)
            applied_changes[key] = settings.borderline_delta
        elif key == "snne_samples":
            settings.snne_samples = int(value)
            applied_changes[key] = settings.snne_samples
        elif key == "snne_tau":
            settings.snne_tau = float(value)
            applied_changes[key] = settings.snne_tau
        elif key == "max_refinement_steps":
            settings.max_refinement_steps = int(value)
            applied_changes[key] = settings.max_refinement_steps
        elif key == "cp_target_mis":
            settings.cp_target_mis = float(value)
            applied_changes[key] = settings.cp_target_mis
        else:
            try:
                numeric = float(value)
                if numeric.is_integer():
                    numeric = int(numeric)
                _apply(key, numeric)
            except (TypeError, ValueError):
                _apply(key, value)

    tuner_store.set_status(req.proposal_id, "applied", reason=req.reason)

    snapshot = {
        "accept_threshold": settings.accept_threshold,
        "borderline_delta": getattr(settings, "borderline_delta", None),
        "snne_samples": getattr(settings, "snne_samples", None),
        "snne_tau": getattr(settings, "snne_tau", None),
        "max_refinement_steps": getattr(settings, "max_refinement_steps", None),
        "cp_target_mis": getattr(settings, "cp_target_mis", None),
    }

    return {
        "proposal_id": req.proposal_id,
        "status": "applied",
        "config_patch": config_patch,
        "applied_changes": applied_changes,
        "settings": snapshot,
    }


@router.get("/health")
def health(request: Request):
    # Simple DB check: ensure schema applied and steps table is readable
    try:
        # Lazy import to avoid circular dep
        import sqlite3

        db_path = request.app.state.settings.db_path
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='steps'"
        )
        exists = cur.fetchone() is not None
        con.close()
        return {"status": "ok", "db": {"steps": exists}}
    except Exception as e:  # pragma: no cover
        return {"status": "degraded", "error": str(e)}


@router.get("/metrics")
def metrics(request: Request):
    # Return in-memory counters with CP stats
    settings = request.app.state.settings
    m = getattr(request.app.state, "metrics", None)
    if not m:
        m = {"requests": 0, "answers": 0, "abstain": 0, "accept": 0, "iterate": 0}
        request.app.state.metrics = m
    # attach CP stats (false-accept among accepted) by domain
    try:
        m_out = dict(m)
        # global rates
        ans_total = float(m_out.get("answers", 0) or 0)
        if ans_total > 0:
            m_out["rates"] = {
                "accept": (m_out.get("accept", 0) or 0) / ans_total,
                "iterate": (m_out.get("iterate", 0) or 0) / ans_total,
                "abstain": (m_out.get("abstain", 0) or 0) / ans_total,
            }
        # per-domain rates
        by_dom = m_out.get("by_domain", {}) or {}
        brates = {}
        for dom, dm in by_dom.items():
            d_total = float(dm.get("answers", 0) or 0)
            if d_total > 0:
                brates[dom] = {
                    "accept": (dm.get("accept", 0) or 0) / d_total,
                    "iterate": (dm.get("iterate", 0) or 0) / d_total,
                    "abstain": (dm.get("abstain", 0) or 0) / d_total,
                }
        if brates:
            m_out["rates_by_domain"] = brates
        lat_summary_raw = _latency_summary(m.get("answer_latency", {}) or {})
        if lat_summary_raw.get("count", 0) > 0:
            lat_summary_public = dict(lat_summary_raw)
            if math.isinf(lat_summary_public.get("p95") or 0.0):
                lat_summary_public["p95"] = None
            m_out["latency"] = lat_summary_public
            request.app.state.metrics["latency"] = lat_summary_public
        ft_summary_raw = _latency_summary(m.get("first_token_latency", {}) or {})
        if ft_summary_raw.get("count", 0) > 0:
            ft_public = dict(ft_summary_raw)
            if math.isinf(ft_public.get("p95") or 0.0):
                ft_public["p95"] = None
            m_out["first_token_latency"] = ft_public
            request.app.state.metrics["first_token_latency_summary"] = ft_public
        latency_by_dom_out: Dict[str, Any] = {}
        latency_by_dom_summary: Dict[str, Any] = {}
        lat_by_dom = m.get("answer_latency_by_domain", {}) or {}
        for dom, hist in lat_by_dom.items():
            summary_raw = _latency_summary(hist or {})
            if summary_raw.get("count", 0) > 0:
                summary_public = dict(summary_raw)
                if math.isinf(summary_public.get("p95") or 0.0):
                    summary_public["p95"] = None
                latency_by_dom_out[dom] = summary_public
                latency_by_dom_summary[dom] = summary_raw
        if latency_by_dom_out:
            m_out["latency_by_domain"] = latency_by_dom_out
            request.app.state.metrics["latency_by_domain"] = latency_by_dom_out
        ft_by_dom = m.get("first_token_latency_by_domain", {}) or {}
        ft_by_dom_out: Dict[str, Any] = {}
        for dom, hist in ft_by_dom.items():
            summary_raw = _latency_summary(hist or {})
            if summary_raw.get("count", 0) > 0:
                summary_public = dict(summary_raw)
                if math.isinf(summary_public.get("p95") or 0.0):
                    summary_public["p95"] = None
                ft_by_dom_out[dom] = summary_public
        if ft_by_dom_out:
            m_out["first_token_latency_by_domain"] = ft_by_dom_out
            request.app.state.metrics["first_token_latency_by_domain"] = ft_by_dom_out

        def _format_uq(stats: Dict[str, Any]) -> Dict[str, Any]:
            out = dict(stats)
            raw_count = int(out.get("raw_count", 0) or 0)
            norm_count = int(out.get("normalized_count", 0) or 0)
            out["avg_raw"] = (out["raw_sum"] / raw_count) if raw_count else None
            out["avg_normalized"] = (
                (out["normalized_sum"] / norm_count) if norm_count else None
            )
            return out

        uq_stats = m.get("uq")
        if uq_stats:
            m_out["uq"] = _format_uq(uq_stats)
        uq_by_dom = m.get("uq_by_domain")
        if uq_by_dom:
            m_out["uq_by_domain"] = {
                dom: _format_uq(stats) for dom, stats in uq_by_dom.items()
            }
        cp_stats = cp_store.domain_stats(settings.db_path)
        m_out["cp_stats"] = cp_stats
        request.app.state.metrics["cp_stats"] = cp_stats
        alerts = dict(m_out.get("alerts") or {})
        target = float(getattr(settings, "cp_target_mis", 0.05) or 0.0)
        tolerance = float(getattr(settings, "cp_alert_tolerance", 0.02) or 0.0)
        cp_alerts: Dict[str, Dict[str, Any]] = rolling_false_accept_rate(
            cp_stats, target, tolerance
        )
        latency_alerts: Dict[str, Any] = {}
        lat_threshold = float(
            getattr(settings, "latency_p95_alert_seconds", 6.0) or 0.0
        )
        min_requests = max(
            1, int(getattr(settings, "latency_alert_min_requests", 20) or 0)
        )
        global_p95 = lat_summary_raw.get("p95")
        if (
            lat_summary_raw.get("count", 0) >= min_requests
            and global_p95 is not None
            and (math.isinf(global_p95) or global_p95 > lat_threshold)
        ):
            latency_alerts["global"] = {"p95": global_p95, "threshold": lat_threshold}
        for dom, summary_raw in latency_by_dom_summary.items():
            dom_p95 = summary_raw.get("p95")
            if (
                summary_raw.get("count", 0) >= min_requests
                and dom_p95 is not None
                and (math.isinf(dom_p95) or dom_p95 > lat_threshold)
            ):
                latency_alerts[dom] = {"p95": dom_p95, "threshold": lat_threshold}
        if latency_alerts:
            sanitized_latency_alerts: Dict[str, Any] = {}
            for scope, payload in latency_alerts.items():
                data = dict(payload)
                p95_val = data.get("p95")
                if p95_val is not None and math.isinf(p95_val):
                    data["p95"] = None
                sanitized_latency_alerts[scope] = data
            alerts["latency"] = sanitized_latency_alerts
        abstain_alerts: Dict[str, Any] = {}
        abstain_threshold = float(getattr(settings, "abstain_alert_rate", 0.3) or 0.0)
        abstain_min = max(
            1, int(getattr(settings, "abstain_alert_min_answers", 20) or 0)
        )
        if ans_total >= abstain_min:
            global_abstain_rate = (m_out.get("abstain", 0) or 0) / ans_total
            if global_abstain_rate > abstain_threshold:
                abstain_alerts["global"] = {
                    "rate": global_abstain_rate,
                    "threshold": abstain_threshold,
                    "answers": ans_total,
                }
        for dom, dm in by_dom.items():
            dom_answers = float(dm.get("answers", 0) or 0)
            if dom_answers < abstain_min:
                continue
            rate_dom = (dm.get("abstain", 0) or 0) / dom_answers if dom_answers else 0.0
            if rate_dom > abstain_threshold:
                abstain_alerts[dom] = {
                    "rate": rate_dom,
                    "threshold": abstain_threshold,
                    "answers": dom_answers,
                }
        if abstain_alerts:
            alerts["abstain"] = abstain_alerts
        snne_tol = float(
            getattr(settings, "snne_drift_quantile_tolerance", 0.08) or 0.0
        )
        snne_min = int(getattr(settings, "snne_drift_min_samples", 50) or 0)
        snne_window = int(getattr(settings, "snne_drift_window", 200) or 0)
        if snne_window <= 0:
            snne_window = 200
        cp_refs: Dict[str, Dict[str, Any]] = {}
        cp_recent_quantiles: Dict[str, Dict[str, Any]] = {}
        cp_quantile_drift: Dict[str, Dict[str, Any]] = {}
        for dom in cp_stats.keys():
            ref = get_reference(settings.db_path, dom)
            baseline_quantiles: Dict[str, float] | None = None
            if ref:
                cp_refs[dom] = {
                    "run_id": ref.get("run_id"),
                    "target_mis": ref.get("target_mis"),
                    "tau": ref.get("tau"),
                    "updated": ref.get("updated"),
                }
                baseline_quantiles = {
                    k: float(v) for k, v in (ref.get("snne_quantiles") or {}).items()
                }
            scores = recent_scores(settings.db_path, dom, limit=snne_window)
            if scores:
                recent_q = quantiles_from_scores(scores, DRIFT_QUANTILES)
                cp_recent_quantiles[dom] = {
                    "quantiles": recent_q,
                    "samples": len(scores),
                }
                if baseline_quantiles:
                    drift = compute_quantile_drift(
                        baseline_quantiles, recent_q, sample_size=len(scores)
                    )
                    cp_quantile_drift[dom] = {
                        "deltas": drift.deltas,
                        "max_abs_delta": drift.max_abs_delta,
                        "samples": drift.sample_size,
                    }
                    if needs_attention(
                        drift, tolerance=snne_tol, min_sample_size=snne_min
                    ):
                        cp_alerts.setdefault(dom, {}).update(
                            {"snne_quantile_delta": drift.max_abs_delta}
                        )
        if cp_refs:
            m_out["cp_reference"] = cp_refs
            request.app.state.metrics["cp_reference"] = cp_refs
        if cp_recent_quantiles:
            m_out["cp_recent_quantiles"] = cp_recent_quantiles
            request.app.state.metrics["cp_recent_quantiles"] = cp_recent_quantiles
        if cp_quantile_drift:
            m_out["cp_quantile_drift"] = cp_quantile_drift
            request.app.state.metrics["cp_quantile_drift"] = cp_quantile_drift
        if cp_alerts:
            alerts["cp"] = cp_alerts
        approvals_store = getattr(request.app.state, "approvals", None)
        if approvals_store:
            approvals_snapshot = approvals_store.snapshot()
            m_out["approvals"] = approvals_snapshot
            request.app.state.metrics["approvals"] = approvals_snapshot
            pending_threshold = int(
                getattr(settings, "approvals_pending_alert_threshold", 5) or 0
            )
            age_threshold = int(
                getattr(settings, "approvals_pending_age_threshold_seconds", 300) or 0
            )
            approvals_alerts: Dict[str, Any] = {}
            if approvals_snapshot["pending"] > pending_threshold:
                approvals_alerts["pending"] = {
                    "current": approvals_snapshot["pending"],
                    "threshold": pending_threshold,
                }
            if approvals_snapshot["max_pending_age"] > age_threshold:
                approvals_alerts["pending_age"] = {
                    "current": approvals_snapshot["max_pending_age"],
                    "threshold": age_threshold,
                }
            if approvals_alerts:
                alerts["approvals"] = approvals_alerts
        if alerts:
            m_out["alerts"] = alerts
            request.app.state.metrics["alerts"] = alerts
        return m_out
    except Exception:
        return m


@router.get("/metrics/prom")
def metrics_prom(request: Request):
    settings = request.app.state.settings
    m = getattr(
        request.app.state,
        "metrics",
        {"requests": 0, "answers": 0, "abstain": 0, "by_domain": {}},
    )
    lines: list[str] = []

    def _prom_number(value: Any) -> str:
        if value is None:
            return "nan"
        try:
            fval = float(value)
        except (TypeError, ValueError):
            return "nan"
        if math.isinf(fval):
            return "+Inf"
        if math.isnan(fval):
            return "nan"
        return f"{fval}"

    lines.append("# HELP uamm_requests_total Total requests received")
    lines.append("# TYPE uamm_requests_total counter")
    lines.append(f"uamm_requests_total {m.get('requests', 0)}")
    lines.append("# HELP uamm_answers_total Total answers produced")
    lines.append("# TYPE uamm_answers_total counter")
    lines.append(f"uamm_answers_total {m.get('answers', 0)}")
    lines.append("# HELP uamm_abstain_total Total abstentions")
    lines.append("# TYPE uamm_abstain_total counter")
    lines.append(f"uamm_abstain_total {m.get('abstain', 0)}")
    by_dom = m.get("by_domain", {}) or {}
    lines.append("# HELP uamm_answers_by_domain_total Answers by domain")
    lines.append("# TYPE uamm_answers_by_domain_total counter")
    for dom, dm in by_dom.items():
        lines.append(
            f'uamm_answers_by_domain_total{{domain="{dom}"}} {dm.get("answers", 0)}'
        )
    lines.append("# HELP uamm_abstain_by_domain_total Abstentions by domain")
    lines.append("# TYPE uamm_abstain_by_domain_total counter")
    for dom, dm in by_dom.items():
        lines.append(
            f'uamm_abstain_by_domain_total{{domain="{dom}"}} {dm.get("abstain", 0)}'
        )
    # Histogram for answer latency (seconds)
    lines.append("# HELP uamm_answer_latency_seconds Answer latency in seconds")
    lines.append("# TYPE uamm_answer_latency_seconds histogram")
    h = m.get("answer_latency", {}) or {}
    buckets = h.get("buckets", {})
    cumulative = 0
    for le in ["0.1", "0.5", "1", "2.5", "6", "+Inf"]:
        cumulative += int(buckets.get(le, 0))
        lines.append(f'uamm_answer_latency_seconds_bucket{{le="{le}"}} {cumulative}')
    lines.append(f"uamm_answer_latency_seconds_sum {float(h.get('sum', 0.0))}")
    lines.append(f"uamm_answer_latency_seconds_count {int(h.get('count', 0))}")
    # Per-domain histogram
    lines.append(
        "# HELP uamm_answer_latency_seconds_by_domain Answer latency in seconds by domain"
    )
    lines.append("# TYPE uamm_answer_latency_seconds_by_domain histogram")
    hbd = m.get("answer_latency_by_domain", {}) or {}
    for dom, hd in hbd.items():
        cumulative = 0
        buckets = hd.get("buckets", {})
        for le in ["0.1", "0.5", "1", "2.5", "6", "+Inf"]:
            cumulative += int(buckets.get(le, 0))
            lines.append(
                f'uamm_answer_latency_seconds_by_domain_bucket{{domain="{dom}",le="{le}"}} {cumulative}'
            )
        lines.append(
            f'uamm_answer_latency_seconds_by_domain_sum{{domain="{dom}"}} {float(hd.get("sum", 0.0))}'
        )
        lines.append(
            f'uamm_answer_latency_seconds_by_domain_count{{domain="{dom}"}} {int(hd.get("count", 0))}'
        )
    latency_summary = _latency_summary(h or {})
    lines.append(
        "# HELP uamm_latency_p95_seconds Approximate 95th percentile latency in seconds"
    )
    lines.append("# TYPE uamm_latency_p95_seconds gauge")
    lines.append(f"uamm_latency_p95_seconds {_prom_number(latency_summary.get('p95'))}")
    lines.append("# HELP uamm_latency_avg_seconds Average latency in seconds")
    lines.append("# TYPE uamm_latency_avg_seconds gauge")
    lines.append(
        f"uamm_latency_avg_seconds {_prom_number(latency_summary.get('average'))}"
    )
    latency_by_dom = m.get("latency_by_domain", {}) or {}
    if latency_by_dom:
        lines.append(
            "# HELP uamm_latency_p95_seconds_by_domain Approximate 95th percentile latency by domain"
        )
        lines.append("# TYPE uamm_latency_p95_seconds_by_domain gauge")
        for dom, summary in latency_by_dom.items():
            lines.append(
                f'uamm_latency_p95_seconds_by_domain{{domain="{dom}"}} {_prom_number(summary.get("p95"))}'
            )
    # UQ aggregates
    uq_stats = m.get("uq") or {}
    if uq_stats:
        events = int(uq_stats.get("events", 0) or 0)
        avg_raw = uq_stats.get("avg_raw")
        avg_norm = uq_stats.get("avg_normalized")
        lines.append("# HELP uamm_uq_events_total Total SNNE/UQ events observed")
        lines.append("# TYPE uamm_uq_events_total counter")
        lines.append(f"uamm_uq_events_total {events}")
        lines.append("# HELP uamm_uq_avg_raw Average raw SNNE score (log-space)")
        lines.append("# TYPE uamm_uq_avg_raw gauge")
        lines.append(f"uamm_uq_avg_raw {avg_raw if avg_raw is not None else 'nan'}")
        lines.append("# HELP uamm_uq_avg_normalized Average normalized SNNE score")
        lines.append("# TYPE uamm_uq_avg_normalized gauge")
        lines.append(
            f"uamm_uq_avg_normalized {avg_norm if avg_norm is not None else 'nan'}"
        )
        lines.append("# HELP uamm_uq_samples_total Total SNNE samples evaluated")
        lines.append("# TYPE uamm_uq_samples_total counter")
        lines.append(
            f"uamm_uq_samples_total {int(uq_stats.get('samples_total', 0) or 0)}"
        )
    uq_by_dom = m.get("uq_by_domain", {}) or {}
    if uq_by_dom:
        lines.append("# HELP uamm_uq_events_by_domain_total SNNE/UQ events by domain")
        lines.append("# TYPE uamm_uq_events_by_domain_total counter")
        for dom, stats in uq_by_dom.items():
            events = int(stats.get("events", 0) or 0)
            lines.append(f'uamm_uq_events_by_domain_total{{domain="{dom}"}} {events}')
        lines.append(
            "# HELP uamm_uq_avg_raw_by_domain Average raw SNNE score by domain"
        )
        lines.append("# TYPE uamm_uq_avg_raw_by_domain gauge")
        for dom, stats in uq_by_dom.items():
            avg_raw = stats.get("avg_raw")
            lines.append(
                f'uamm_uq_avg_raw_by_domain{{domain="{dom}"}} {avg_raw if avg_raw is not None else "nan"}'
            )
        lines.append(
            "# HELP uamm_uq_avg_normalized_by_domain Average normalized SNNE score by domain"
        )
        lines.append("# TYPE uamm_uq_avg_normalized_by_domain gauge")
        for dom, stats in uq_by_dom.items():
            avg_norm = stats.get("avg_normalized")
            lines.append(
                f'uamm_uq_avg_normalized_by_domain{{domain="{dom}"}} {avg_norm if avg_norm is not None else "nan"}'
            )
        lines.append(
            "# HELP uamm_uq_samples_by_domain_total SNNE samples processed by domain"
        )
        lines.append("# TYPE uamm_uq_samples_by_domain_total counter")
        for dom, stats in uq_by_dom.items():
            samples = int(stats.get("samples_total", 0) or 0)
            lines.append(f'uamm_uq_samples_by_domain_total{{domain="{dom}"}} {samples}')
    answers_total = float(m.get("answers", 0) or 0)
    abstain_total = float(m.get("abstain", 0) or 0)
    lines.append("# HELP uamm_abstain_rate Global abstain rate")
    lines.append("# TYPE uamm_abstain_rate gauge")
    global_abstain_rate = (abstain_total / answers_total) if answers_total else 0.0
    lines.append(f"uamm_abstain_rate {_prom_number(global_abstain_rate)}")
    if by_dom:
        lines.append("# HELP uamm_abstain_rate_by_domain Abstain rate by domain")
        lines.append("# TYPE uamm_abstain_rate_by_domain gauge")
        for dom, dm in by_dom.items():
            dom_answers = float(dm.get("answers", 0) or 0)
            dom_rate = (dm.get("abstain", 0) or 0) / dom_answers if dom_answers else 0.0
            lines.append(
                f'uamm_abstain_rate_by_domain{{domain="{dom}"}} {_prom_number(dom_rate)}'
            )
    # Approvals metrics
    approvals_store = getattr(request.app.state, "approvals", None)
    if approvals_store:
        approvals_snapshot = approvals_store.snapshot()
        request.app.state.metrics["approvals"] = approvals_snapshot
    else:
        approvals_snapshot = m.get("approvals", {}) or {}
    lines.append("# HELP uamm_approvals_pending_total Pending tool approvals")
    lines.append("# TYPE uamm_approvals_pending_total gauge")
    lines.append(f"uamm_approvals_pending_total {approvals_snapshot.get('pending', 0)}")
    lines.append(
        "# HELP uamm_approvals_approved_total Approved tool approvals awaiting consume"
    )
    lines.append("# TYPE uamm_approvals_approved_total gauge")
    lines.append(
        f"uamm_approvals_approved_total {approvals_snapshot.get('approved', 0)}"
    )
    lines.append(
        "# HELP uamm_approvals_denied_total Denied tool approvals awaiting consume"
    )
    lines.append("# TYPE uamm_approvals_denied_total gauge")
    lines.append(f"uamm_approvals_denied_total {approvals_snapshot.get('denied', 0)}")
    lines.append(
        "# HELP uamm_approvals_pending_age_seconds Maximum pending approval age (seconds)"
    )
    lines.append("# TYPE uamm_approvals_pending_age_seconds gauge")
    lines.append(
        f"uamm_approvals_pending_age_seconds {approvals_snapshot.get('max_pending_age', 0.0)}"
    )
    lines.append(
        "# HELP uamm_approvals_avg_pending_age_seconds Average pending approval age (seconds)"
    )
    lines.append("# TYPE uamm_approvals_avg_pending_age_seconds gauge")
    lines.append(
        f"uamm_approvals_avg_pending_age_seconds {approvals_snapshot.get('avg_pending_age', 0.0)}"
    )
    # CP stats & alerts
    cp_stats = m.get("cp_stats") or cp_store.domain_stats(settings.db_path)
    request.app.state.metrics["cp_stats"] = cp_stats
    target = float(getattr(settings, "cp_target_mis", 0.05) or 0.0)
    tolerance = float(getattr(settings, "cp_alert_tolerance", 0.02) or 0.0)
    lines.append(
        "# HELP uamm_cp_false_accept_rate False-accept among accepted per domain"
    )
    lines.append("# TYPE uamm_cp_false_accept_rate gauge")
    cp_alert_domains: Dict[str, float] = {}
    for dom, stats in cp_stats.items():
        rate = float(stats.get("rate_false_accept", 0.0) or 0.0)
        lines.append(f'uamm_cp_false_accept_rate{{domain="{dom}"}} {rate}')
        if rate > target + tolerance:
            cp_alert_domains[dom] = rate
    cp_alerts_map: Dict[str, Dict[str, Any]] = {
        dom: {"false_accept_rate": rate, "target": target, "tolerance": tolerance}
        for dom, rate in cp_alert_domains.items()
    }
    cp_refs = m.get("cp_reference", {}) or {}
    if cp_refs:
        lines.append("# HELP uamm_cp_tau_threshold CP acceptance threshold by domain")
        lines.append("# TYPE uamm_cp_tau_threshold gauge")
        for dom, info in cp_refs.items():
            tau = info.get("tau")
            if tau is not None:
                lines.append(f'uamm_cp_tau_threshold{{domain="{dom}"}} {float(tau)}')
    cp_recent_quantiles = m.get("cp_recent_quantiles", {}) or {}
    if cp_recent_quantiles:
        lines.append(
            "# HELP uamm_cp_recent_quantile Recent SNNE quantiles from calibration artifacts"
        )
        lines.append("# TYPE uamm_cp_recent_quantile gauge")
        for dom, payload in cp_recent_quantiles.items():
            quantiles = payload.get("quantiles", {}) or {}
            for q_label, value in quantiles.items():
                lines.append(
                    f'uamm_cp_recent_quantile{{domain="{dom}",quantile="{q_label}"}} {float(value)}'
                )
        lines.append(
            "# HELP uamm_cp_recent_quantile_samples Sample size for recent SNNE quantiles"
        )
        lines.append("# TYPE uamm_cp_recent_quantile_samples gauge")
        for dom, payload in cp_recent_quantiles.items():
            lines.append(
                f'uamm_cp_recent_quantile_samples{{domain="{dom}"}} {int(payload.get("samples", 0) or 0)}'
            )
    cp_drift = m.get("cp_quantile_drift", {}) or {}
    if cp_drift:
        lines.append(
            "# HELP uamm_cp_snne_quantile_delta_max Max SNNE quantile drift absolute delta by domain"
        )
        lines.append("# TYPE uamm_cp_snne_quantile_delta_max gauge")
        for dom, payload in cp_drift.items():
            max_delta = float(payload.get("max_abs_delta", 0.0) or 0.0)
            lines.append(
                f'uamm_cp_snne_quantile_delta_max{{domain="{dom}"}} {max_delta}'
            )
            if max_delta > tolerance:
                cp_alerts_map.setdefault(dom, {})["snne_quantile_delta"] = max_delta
    alerts_state = dict(m.get("alerts", {}) or {})
    if cp_alerts_map:
        alerts_state["cp"] = cp_alerts_map
    pending_threshold = int(
        getattr(settings, "approvals_pending_alert_threshold", 5) or 0
    )
    age_threshold = int(
        getattr(settings, "approvals_pending_age_threshold_seconds", 300) or 0
    )
    approvals_alerts: Dict[str, Any] = {}
    if approvals_snapshot.get("pending", 0) > pending_threshold:
        approvals_alerts["pending"] = {
            "current": approvals_snapshot.get("pending", 0),
            "threshold": pending_threshold,
        }
    if approvals_snapshot.get("max_pending_age", 0.0) > age_threshold:
        approvals_alerts["pending_age"] = {
            "current": approvals_snapshot.get("max_pending_age", 0.0),
            "threshold": age_threshold,
        }
    if approvals_alerts:
        alerts_state["approvals"] = approvals_alerts
    if alerts_state:
        request.app.state.metrics["alerts"] = alerts_state
        cp_alert_state = alerts_state.get("cp") or {}
        if cp_alert_state:
            lines.append("# HELP uamm_alert_cp CP drift alert flag by domain")
            lines.append("# TYPE uamm_alert_cp gauge")
            for dom in cp_alert_state:
                lines.append(f'uamm_alert_cp{{domain="{dom}"}} 1')
        latency_alert_state = alerts_state.get("latency") or {}
        if latency_alert_state:
            lines.append("# HELP uamm_alert_latency Latency alert flag")
            lines.append("# TYPE uamm_alert_latency gauge")
            for scope in latency_alert_state:
                lines.append(f'uamm_alert_latency{{scope="{scope}"}} 1')
        abstain_alert_state = alerts_state.get("abstain") or {}
        if abstain_alert_state:
            lines.append("# HELP uamm_alert_abstain Abstain alert flag")
            lines.append("# TYPE uamm_alert_abstain gauge")
            for scope in abstain_alert_state:
                lines.append(f'uamm_alert_abstain{{scope="{scope}"}} 1')
        lines.append("# HELP uamm_alert_approvals Pending approvals alert flag")
        lines.append("# TYPE uamm_alert_approvals gauge")
        lines.append(f"uamm_alert_approvals {1 if approvals_alerts else 0}")
    content = "\n".join(lines) + "\n"
    from fastapi.responses import Response as FastResponse

    return FastResponse(content=content, media_type="text/plain; version=0.0.4")


@router.get("/dashboards/summary")
def dashboard_summary(request: Request):
    settings = request.app.state.settings
    metrics_state = getattr(request.app.state, "metrics", {}) or {}
    return build_dashboard_summary(metrics_state, settings)


class CPArtifactsRequest(BaseModel):
    run_id: str
    domain: str = "default"
    items: list[dict]


@router.post("/cp/artifacts")
def cp_artifacts(req: CPArtifactsRequest, request: Request):
    settings = request.app.state.settings
    items = []
    for it in req.items:
        items.append(
            (
                float(it.get("S", 0.0)),
                bool(it.get("accepted", False)),
                bool(it.get("correct", False)),
            )
        )
    n = cp_store.add_artifacts(
        settings.db_path, run_id=req.run_id, domain=req.domain, items=items
    )
    tau = cp_store.compute_threshold(
        settings.db_path, domain=req.domain, target_mis=settings.cp_target_mis
    )
    stats = cp_store.domain_stats(settings.db_path, domain=req.domain).get(
        req.domain, {}
    )
    cache = getattr(request.app.state, "cp_cache", None)
    if cache is not None:
        cache.set(req.domain, tau, settings.cp_target_mis, stats)
    return {"inserted": n, "tau": tau, "stats": stats}


@router.get("/cp/threshold")
def cp_threshold(
    request: Request, domain: str = "default", target_mis: float | None = None
):
    settings = request.app.state.settings
    target = target_mis or settings.cp_target_mis
    cache = getattr(request.app.state, "cp_cache", None)
    if cache is not None and target == settings.cp_target_mis:
        cached = cache.get(domain, target)
        if cached is not None:
            return {"domain": domain, "tau": cached, "cached": True}
    tau = cp_store.compute_threshold(settings.db_path, domain=domain, target_mis=target)
    stats = cp_store.domain_stats(settings.db_path, domain=domain).get(domain, {})
    if cache is not None and target == settings.cp_target_mis:
        cache.set(domain, tau, target, stats)
    return {"domain": domain, "tau": tau, "cached": False, "stats": stats}


@router.get("/cp/stats")
def cp_stats(request: Request, domain: str | None = None):
    settings = request.app.state.settings
    return cp_store.domain_stats(settings.db_path, domain=domain)


class GoVCheckRequest(BaseModel):
    dag: Dict[str, Any]
    verified_pcn: list[str] = Field(default_factory=list)


@router.post("/gov/check")
def gov_check(req: GoVCheckRequest):
    """Validate and evaluate a compact GoV DAG.

    Body example:
      {
        "dag": {"nodes": [...], "edges": [...]},
        "verified_pcn": ["token123"]
      }
    """
    valid, validation_failures = validate_dag(req.dag)
    if not valid:
        return {"ok": False, "failures": validation_failures, "validation_ok": False}
    verified = set(req.verified_pcn)

    def _pcn_status(token_id: str) -> str | None:
        return "verified" if token_id in verified else None

    ok, failures = evaluate_dag(req.dag, pcn_status=_pcn_status)
    return {"ok": ok, "failures": failures, "validation_ok": True}


@router.get("/steps/recent")
def steps_recent(
    request: Request,
    limit: int = 50,
    include_trace: bool = False,
    domain: str | None = None,
    action: str | None = None,
):
    import sqlite3
    import ast

    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    q = "SELECT id, ts, domain, action, s1, s2, final_score, cp_accept, change_summary, pack_ids, trace_json FROM steps"
    where = []
    args: list = []
    if domain:
        where.append("domain = ?")
        args.append(domain)
    if action:
        where.append("action = ?")
        args.append(action)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    rows = con.execute(q, tuple(args)).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            packs = ast.literal_eval(r["pack_ids"]) if r["pack_ids"] else []
        except Exception:
            packs = []
        item = {
            "id": r["id"],
            "ts": r["ts"],
            "domain": r["domain"],
            "action": r["action"],
            "s1": r["s1"],
            "s2": r["s2"],
            "final_score": r["final_score"],
            "cp_accept": bool(r["cp_accept"]),
            "change_summary": r["change_summary"],
            "pack_ids": packs,
        }
        if include_trace:
            try:
                import json as _json

                item["trace"] = (
                    _json.loads(r["trace_json"]) if r["trace_json"] else None
                )
            except Exception:
                item["trace"] = None
        out.append(item)
    return {"steps": out}


@router.get("/steps/{step_id}")
def step_detail(step_id: str, request: Request):
    import sqlite3
    import ast
    import json as _json

    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT id, ts, step, question, answer, domain, s1, s2, final_score, cp_accept, action, reason, is_refinement, status, latency_ms, usage, pack_ids, issues, tools_used, change_summary, trace_json FROM steps WHERE id=?",
        (step_id,),
    ).fetchone()
    con.close()
    if not row:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="step not found")

    def _parse_json_field(val):
        try:
            return _json.loads(val) if val else None
        except Exception:
            return None

    def _parse_list_repr(val):
        try:
            return ast.literal_eval(val) if val else []
        except Exception:
            return []

    def _parse_dict_repr(val):
        try:
            return ast.literal_eval(val) if val else {}
        except Exception:
            return {}

    return {
        "id": row["id"],
        "ts": row["ts"],
        "step": row["step"],
        "question": row["question"],
        "answer": row["answer"],
        "domain": row["domain"],
        "s1": row["s1"],
        "s2": row["s2"],
        "final_score": row["final_score"],
        "cp_accept": bool(row["cp_accept"]),
        "action": row["action"],
        "reason": row["reason"],
        "is_refinement": bool(row["is_refinement"]),
        "status": row["status"],
        "latency_ms": row["latency_ms"],
        "usage": _parse_dict_repr(row["usage"]),
        "pack_ids": _parse_list_repr(row["pack_ids"]),
        "issues": _parse_list_repr(row["issues"]),
        "tools_used": _parse_list_repr(row["tools_used"]),
        "change_summary": row["change_summary"],
        "trace": _parse_json_field(row["trace_json"]),
    }


class TableQueryRequest(BaseModel):
    sql: str
    params: list = []
    limit: int = 100
    domain: str | None = None


@router.post("/table/query")
def table_query(req: TableQueryRequest, request: Request):
    settings = request.app.state.settings
    sql = req.sql
    if not is_read_only_select(sql):
        return JSONResponse(
            status_code=400,
            content={
                "code": "sql_disallowed",
                "message": "Only SELECT statements allowed",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )
    # Determine allowed tables (domain-aware overrides)
    allowed_tables = settings.table_allowed
    if req.domain and getattr(settings, "table_allowed_by_domain", None):
        allowed_tables = settings.table_allowed_by_domain.get(
            req.domain, settings.table_allowed
        )
    if not tables_allowed(sql, allowed_tables):
        return JSONResponse(
            status_code=403,
            content={
                "code": "table_forbidden",
                "message": "Table not allowed",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )
    # Aggregate table policies across referenced tables
    from uamm.security.sql_guard import referenced_tables

    tables = referenced_tables(sql)
    pol = settings.table_policies or {}
    # defaults
    max_rows_eff = max(0, min(req.limit, 1000))
    time_limit_ms_eff = 500
    rate_limit_eff = 60
    for t in tables:
        tp = pol.get(t, {})
        if tp.get("max_rows") is not None:
            max_rows_eff = min(max_rows_eff, int(tp["max_rows"]))
        if tp.get("max_time_ms") is not None:
            time_limit_ms_eff = min(time_limit_ms_eff, int(tp["max_time_ms"]))
        if tp.get("max_rate_per_min") is not None:
            rate_limit_eff = min(rate_limit_eff, int(tp["max_rate_per_min"]))
    # Rate limiting per table
    rl = getattr(request.app.state, "table_rates", {})
    now = time.time()
    for t in tables:
        bucket = rl.setdefault(t, [])
        # prune >60s
        rl[t] = [ts for ts in bucket if now - ts < 60]
        if len(rl[t]) >= rate_limit_eff:
            request.app.state.table_rates = rl
            return JSONResponse(
                status_code=429,
                content={
                    "code": "rate_limited",
                    "message": f"Too many queries for table {t}",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )
        rl[t].append(now)
    request.app.state.table_rates = rl

    rows = db_table_query(
        settings.db_path,
        sql,
        req.params,
        max_rows=max_rows_eff,
        time_limit_ms=time_limit_ms_eff,
    )
    # map to dicts using cursor description
    import sqlite3

    con = sqlite3.connect(settings.db_path)
    cur = con.execute(sql, req.params or [])
    col_names = [d[0] for d in cur.description]
    con.close()
    out = [
        {
            col_names[i] if i < len(col_names) else str(i): val
            for i, val in enumerate(row)
        }
        for row in rows
    ]
    return {"rows": out}


# Duplicate CP endpoints removed; see canonical definitions earlier in file
