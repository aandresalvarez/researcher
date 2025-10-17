from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, Iterable, List
from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File, Form
from pathlib import Path
import sqlite3
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from starlette.responses import RedirectResponse
from pydantic import BaseModel, Field
from io import BytesIO
import zipfile
import hashlib
import hmac
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
    list_suites as eval_list_suites,
    get_suite as eval_get_suite,
    load_items as eval_load_items,
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
from uamm.verification.faithfulness import compute_faithfulness
from uamm.obs.dashboard import build_dashboard_summary
from uamm.security.sql_guard import is_read_only_select, tables_allowed
from uamm.tools.table_query import table_query as db_table_query
from uamm.pcn.sql_checks import evaluate_checks
from uamm.tuner import TunerAgent, TunerTargets
from uamm.rag.vector_store import LanceDBUnavailable, upsert_document_embedding
from uamm.rag.ingest import scan_folder, make_chunks, ALLOWED_EXTS
from uamm.gov.executor import evaluate_dag
from uamm.gov.validator import validate_dag
from uamm.policy.assertions import (
    load_assertions as _load_assertions,
    evaluate_assertions as _eval_assertions,
)
from uamm.security.auth import (
    issue_api_key as ws_issue_key,
    list_keys as ws_list_keys,
    list_workspaces as ws_list,
    get_workspace as ws_get,
    create_workspace as ws_create,
    deactivate_key as ws_deactivate,
)
from uamm.storage.workspaces import (
    ensure_allowed_root,
    normalize_root,
    ensure_workspace_fs,
    resolve_paths as ws_resolve_paths,
)
from uamm.config.policy_packs import list_policies, load_policy
from uamm.tools.registry import (
    get_registry as _get_tool_registry,
    import_callable as _import_callable,
    ensure_builtins as _ensure_tool_builtins,
)


router = APIRouter()


DRIFT_QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
_LAT_BUCKET_KEYS = ["0.1", "0.5", "1", "2.5", "6", "+Inf"]
_LAT_BUCKET_VALUES = [0.1, 0.5, 1.0, 2.5, 6.0, float("inf")]

# Ensure built-in tools are present for registry-backed usage
try:
    _ensure_tool_builtins(_get_tool_registry())
except Exception:
    pass


def _parse_policy_blob(raw: Any) -> Dict[str, Any]:
    """Parse a workspace policy blob (JSON-preferred, repr fallback) into dict.

    Back-compat: older rows may store Python repr strings; we tolerate those.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except Exception:
        try:
            import ast

            val = ast.literal_eval(raw)
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}


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
    list[Dict[str, Any]],
]:
    pcn_map: Dict[str, Dict[str, Any]] = dict(existing_pcn or {})
    tool_events: list[Dict[str, Any]] = []
    score_events: list[Dict[str, Any]] = []
    uq_events: list[Dict[str, Any]] = []
    gov_events: list[Dict[str, Any]] = []
    planning_events: list[Dict[str, Any]] = []
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
        elif evt == "planning":
            planning_events.append(data)
    return pcn_map, tool_events, score_events, uq_events, gov_events, planning_events


def _prepare_trace_blob(
    final: AgentResultModel,
    events: list[tuple[str, Dict[str, Any]]],
    existing_pcn: Dict[str, Dict[str, Any]] | None = None,
) -> tuple[str, Dict[str, Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    pcn_map, tool_events, score_events, uq_events, gov_events, planning_events = (
        _bucket_event_lists(events, existing_pcn)
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
                # include planning events for observability
                "planning": planning_events,
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
    # Guardrails metrics from events inside trace blob
    try:
        blob = json.loads(trace_blob)
        events_map = blob.get("events", {}) if isinstance(blob, dict) else {}
        gr_events = events_map.get("guardrails", []) or []
    except Exception:
        gr_events = []
    if gr_events:
        guard = metrics_state.setdefault(
            "guardrails", {"pre": 0, "post": 0, "by_domain": {}}
        )
        by_dom = guard.setdefault("by_domain", {})
        dom = req.domain
        dom_counters = by_dom.setdefault(dom, {"pre": 0, "post": 0})
        for evt in gr_events:
            stage = str(evt.get("stage", "")).lower()
            if stage == "pre":
                guard["pre"] = int(guard.get("pre", 0)) + 1
                dom_counters["pre"] = int(dom_counters.get("pre", 0)) + 1
            elif stage == "post":
                guard["post"] = int(guard.get("post", 0)) + 1
                dom_counters["post"] = int(dom_counters.get("post", 0)) + 1
    # Planning metrics (from trace blob events)
    try:
        blob = json.loads(trace_blob)
        events_map = blob.get("events", {}) if isinstance(blob, dict) else {}
        p_events = events_map.get("planning", []) or []
        runs = len(p_events)
        improvements = sum(
            1 for e in p_events if isinstance(e, dict) and e.get("improved")
        )
        if runs:
            pstats = metrics_state.setdefault(
                "planning", {"runs": 0, "improvements": 0}
            )
            pstats["runs"] = int(pstats.get("runs", 0)) + runs
            pstats["improvements"] = int(pstats.get("improvements", 0)) + int(
                improvements
            )
        # Units checks from PCN map
        pcn_map = events_map.get("pcn", {}) or {}
        unit_runs = unit_fail = 0
        if isinstance(pcn_map, dict):
            for entry in pcn_map.values():
                if not isinstance(entry, dict):
                    continue
                pol = (
                    entry.get("policy")
                    if isinstance(entry.get("policy"), dict)
                    else None
                )
                if pol and pol.get("units"):
                    unit_runs += 1
                    if str(entry.get("status", "")).lower() == "failed":
                        unit_fail += 1
        if unit_runs:
            units = metrics_state.setdefault("units_checks", {"runs": 0, "fail": 0})
            units["runs"] = int(units.get("runs", 0)) + unit_runs
            units["fail"] = int(units.get("fail", 0)) + unit_fail
    except Exception:
        pass
    # Claim-level faithfulness (graceful on errors)
    try:
        faith = compute_faithfulness(final.final, final.pack_used)
    except Exception:
        faith = {
            "score": None,
            "claim_count": 0,
            "supported_count": 0,
            "unsupported_claims": [],
        }
    if isinstance(faith, dict):
        f_global = metrics_state.setdefault(
            "faithfulness",
            {"count": 0, "sum": 0.0, "claim_count": 0, "unsupported_total": 0},
        )
        f_dom_map = metrics_state.setdefault("faithfulness_by_domain", {})
        f_dom = f_dom_map.setdefault(
            req.domain,
            {"count": 0, "sum": 0.0, "claim_count": 0, "unsupported_total": 0},
        )
        score = faith.get("score")
        claim_count = int(faith.get("claim_count", 0) or 0)
        unsupported = faith.get("unsupported_claims") or []
        if score is not None:
            f_global["count"] = int(f_global.get("count", 0)) + 1
            f_global["sum"] = float(f_global.get("sum", 0.0)) + float(score)
            f_dom["count"] = int(f_dom.get("count", 0)) + 1
            f_dom["sum"] = float(f_dom.get("sum", 0.0)) + float(score)
        if claim_count:
            f_global["claim_count"] = int(f_global.get("claim_count", 0)) + claim_count
            f_dom["claim_count"] = int(f_dom.get("claim_count", 0)) + claim_count
        if unsupported:
            n_uns = len(unsupported)
            f_global["unsupported_total"] = (
                int(f_global.get("unsupported_total", 0)) + n_uns
            )
            f_dom["unsupported_total"] = int(f_dom.get("unsupported_total", 0)) + n_uns
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
        workspace=getattr(request.state, "workspace", None),
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
    stream_lite: bool = Field(
        False,
        description="If true, the SSE stream emits only ready/final (and tokens if present), suppressing score/tool/trace/pcn/gov events.",
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


class ToolRegisterRequest(BaseModel):
    name: str
    path: str  # module:attr or module.attr
    overwrite: bool = False


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
    # Resolve per-request workspace policy overlay (do not mutate global settings)
    overlay: Dict[str, Any] = {}
    try:
        ws = _resolve_workspace(request)
        con = sqlite3.connect(settings.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT json FROM workspace_policies WHERE workspace = ?",
            (ws,),
        ).fetchone()
        con.close()
        if row:
            overlay = _parse_policy_blob(row["json"]) or {}
    except Exception:
        overlay = {}
    # Borderline delta override per-request
    if "borderline_delta" in overlay and "borderline_delta" not in req.model_fields_set:
        try:
            req.borderline_delta = float(overlay["borderline_delta"])  # type: ignore[assignment]
        except Exception:
            pass
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
    tau_accept_eff = float(overlay.get("accept_threshold", settings.accept_threshold))
    policy = PolicyConfig(tau_accept=tau_accept_eff, delta=req.borderline_delta)
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
        "tool_budget_per_refinement",
        int(
            overlay.get(
                "tool_budget_per_refinement",
                getattr(settings, "tool_budget_per_refinement", 2),
            )
        ),
    )
    params.setdefault(
        "tool_budget_per_turn",
        int(
            overlay.get(
                "tool_budget_per_turn", getattr(settings, "tool_budget_per_turn", 4)
            )
        ),
    )
    params.setdefault("max_refinements", req.max_refinements)
    params.setdefault("snne_samples", req.snne_samples)
    params.setdefault("snne_tau", getattr(settings, "snne_tau", 0.3))
    params.setdefault("cp_target_mis", req.cp_target_mis)
    # Use per-request resolved DB path when available
    eff_db_path = getattr(request.state, "db_path", None) or settings.db_path
    params.setdefault("db_path", eff_db_path)
    params.setdefault(
        "rag_weight_sparse",
        float(
            overlay.get(
                "rag_weight_sparse", getattr(settings, "rag_weight_sparse", 0.5)
            )
        ),
    )
    params.setdefault(
        "rag_weight_dense",
        float(
            overlay.get("rag_weight_dense", getattr(settings, "rag_weight_dense", 0.5))
        ),
    )
    params.setdefault(
        "vector_backend",
        str(overlay.get("vector_backend", getattr(settings, "vector_backend", "none"))),
    )
    params.setdefault(
        "lancedb_uri",
        overlay.get(
            "lancedb_uri",
            getattr(request.state, "lancedb_uri", getattr(settings, "lancedb_uri", "")),
        ),
    )
    params.setdefault(
        "lancedb_table",
        str(
            overlay.get(
                "lancedb_table", getattr(settings, "lancedb_table", "rag_vectors")
            )
        ),
    )
    params.setdefault(
        "lancedb_metric",
        str(
            overlay.get("lancedb_metric", getattr(settings, "lancedb_metric", "cosine"))
        ),
    )
    params.setdefault(
        "lancedb_k", overlay.get("lancedb_k", getattr(settings, "lancedb_k", None))
    )
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
    # Planning defaults
    params.setdefault("planning_enabled", getattr(settings, "planning_enabled", False))
    params.setdefault("planning_mode", getattr(settings, "planning_mode", "tot"))
    params.setdefault("planning_budget", getattr(settings, "planning_budget", 3))
    params.setdefault("planning_when", getattr(settings, "planning_when", "borderline"))
    # Tool approvals config
    params["tools_requiring_approval"] = list(
        overlay.get(
            "tools_requiring_approval",
            getattr(settings, "tools_requiring_approval", []),
        )
    )
    # Tool allowlist config (optional)
    if "tools_allowed" in overlay:
        setattr(request.state, "tools_allowed", list(overlay.get("tools_allowed", [])))
        params["tools_allowed"] = list(overlay.get("tools_allowed", []))
    else:
        allowed_tools = getattr(request.state, "tools_allowed", None)
        if allowed_tools is not None:
            params["tools_allowed"] = list(allowed_tools)
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


@router.get("/tools", summary="List registered tools", tags=["Tools"])
def tools_list(request: Request) -> Dict[str, Any]:
    reg = _get_tool_registry()
    return {"tools": reg.list()}


@router.post(
    "/tools/register", summary="Register a tool by import path", tags=["Tools"]
)
def tools_register(req: ToolRegisterRequest, request: Request):
    _require_role(request, {"admin"})
    reg = _get_tool_registry()
    name = req.name.strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "name_required"})
    if (not req.overwrite) and reg.get(name):
        return JSONResponse(status_code=409, content={"error": "exists"})
    try:
        fn = _import_callable(req.path)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    reg.register(name, fn)
    return {"ok": True, "name": name}


@router.delete("/tools/{name}", summary="Unregister a tool", tags=["Tools"])
def tools_unregister(name: str, request: Request):
    _require_role(request, {"admin"})
    reg = _get_tool_registry()
    if not reg.get(name):
        return JSONResponse(status_code=404, content={"error": "not_found"})
    reg.unregister(name)
    return {"ok": True}


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
    # Apply workspace policy overlay similar to non-streaming path
    try:
        ws = _resolve_workspace(request)
        con = sqlite3.connect(settings.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT json FROM workspace_policies WHERE workspace = ?",
            (ws,),
        ).fetchone()
        con.close()
        overlay = _parse_policy_blob(row["json"]) if row else {}
    except Exception:
        overlay = {}
    if "borderline_delta" not in req.model_fields_set:
        req.borderline_delta = float(
            overlay.get(
                "borderline_delta",
                getattr(settings, "borderline_delta", req.borderline_delta),
            )
        )
    if "snne_samples" not in req.model_fields_set:
        req.snne_samples = getattr(settings, "snne_samples", req.snne_samples)
    if "max_refinements" not in req.model_fields_set:
        req.max_refinements = getattr(
            settings, "max_refinement_steps", req.max_refinements
        )
    if "cp_target_mis" not in req.model_fields_set:
        req.cp_target_mis = getattr(settings, "cp_target_mis", req.cp_target_mis)
    tau_accept_eff = float(overlay.get("accept_threshold", settings.accept_threshold))
    policy = PolicyConfig(tau_accept=tau_accept_eff, delta=req.borderline_delta)
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
            # Planning defaults
            params.setdefault(
                "planning_enabled", getattr(settings, "planning_enabled", False)
            )
            params.setdefault(
                "planning_mode", getattr(settings, "planning_mode", "tot")
            )
            params.setdefault(
                "planning_budget", getattr(settings, "planning_budget", 3)
            )
            params.setdefault(
                "planning_when", getattr(settings, "planning_when", "borderline")
            )
            # Faithfulness defaults
            params.setdefault(
                "faithfulness_enabled", getattr(settings, "faithfulness_enabled", True)
            )
            params.setdefault(
                "faithfulness_threshold",
                getattr(settings, "faithfulness_threshold", 0.6),
            )
            # Faithfulness defaults
            params.setdefault(
                "faithfulness_enabled", getattr(settings, "faithfulness_enabled", True)
            )
            params.setdefault(
                "faithfulness_threshold",
                getattr(settings, "faithfulness_threshold", 0.6),
            )
            params.setdefault("cp_target_mis", req.cp_target_mis)
            # Use per-request resolved DB path when available
            eff_db_path = getattr(request.state, "db_path", None) or settings.db_path
            params.setdefault("db_path", eff_db_path)
            params.setdefault(
                "rag_weight_sparse", getattr(settings, "rag_weight_sparse", 0.5)
            )
            params.setdefault(
                "rag_weight_dense", getattr(settings, "rag_weight_dense", 0.5)
            )
            params.setdefault(
                "vector_backend", getattr(settings, "vector_backend", "none")
            )
            params.setdefault(
                "lancedb_uri",
                getattr(
                    request.state, "lancedb_uri", getattr(settings, "lancedb_uri", "")
                ),
            )
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
            # Tool approvals and allowlist (request-scoped overlay)
            params["tools_requiring_approval"] = list(
                overlay.get(
                    "tools_requiring_approval",
                    getattr(settings, "tools_requiring_approval", []),
                )
            )
            if "tools_allowed" in overlay:
                setattr(
                    request.state,
                    "tools_allowed",
                    list(overlay.get("tools_allowed", [])),
                )
                params["tools_allowed"] = list(overlay.get("tools_allowed", []))
            else:
                allowed_tools = getattr(request.state, "tools_allowed", None)
                if allowed_tools is not None:
                    params["tools_allowed"] = list(allowed_tools)
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
                        if getattr(req, "stream_lite", False) and evt in {
                            "score",
                            "tool",
                            "pcn",
                            "gov",
                            "trace",
                        }:
                            continue
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
    _require_role(request, {"admin", "editor"})
    red_text, _ = redact(req.text)
    # Environment override for tests/dev takes precedence
    eff_db = (
        os.getenv("UAMM_DB_PATH")
        or getattr(request.state, "db_path", None)
        or settings.db_path
    )
    mid = db_add_memory(
        eff_db,
        key=req.key,
        text=red_text,
        domain=req.domain,
        workspace=getattr(request.state, "workspace", "default"),
        created_by=getattr(request.state, "user", "anonymous"),
    )
    return {"id": mid}


class RagDocRequest(BaseModel):
    title: str
    url: str | None = None
    text: str


@router.post("/rag/docs")
def rag_add(req: RagDocRequest, request: Request):
    """Ingest a document into the hybrid RAG corpus.

    If the text is long and chunking is configured, splits into overlapping
    chunks and indexes each chunk for FTS and optional vector search.
    """
    settings = request.app.state.settings
    _require_role(request, {"admin", "editor"})
    red_text, _ = redact(req.text)
    chunks = make_chunks(red_text, settings=settings)
    if not chunks:
        chunks = [red_text]
    ids: list[str] = []
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    for idx, segment in enumerate(chunks):
        meta = {
            "title": req.title or "",
            "chunk_index": idx,
            "chunk_total": len(chunks),
        }
        if req.url:
            meta["url"] = req.url
        did = rag_add_doc(
            eff_db,
            title=req.title,
            url=req.url,
            text=segment,
            meta=meta,
            workspace=getattr(request.state, "workspace", "default"),
            created_by=getattr(request.state, "user", "anonymous"),
        )
        ids.append(did)
        if getattr(settings, "vector_backend", "none").lower() == "lancedb":
            try:
                # Override lancedb_uri per workspace without mutating global settings
                from types import SimpleNamespace

                ws_uri = getattr(
                    request.state, "lancedb_uri", getattr(settings, "lancedb_uri", "")
                )
                s_ovr = SimpleNamespace(
                    vector_backend=getattr(settings, "vector_backend", "none"),
                    lancedb_uri=ws_uri,
                    lancedb_table=getattr(settings, "lancedb_table", "rag_vectors"),
                    lancedb_metric=getattr(settings, "lancedb_metric", "cosine"),
                )
                upsert_document_embedding(s_ovr, did, segment, meta=meta)
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
    return {"ids": ids}


class RagIngestFolderRequest(BaseModel):
    path: str | None = None


@router.post("/rag/ingest-folder")
def rag_ingest_folder(req: RagIngestFolderRequest, request: Request):
    """Scan and ingest documents from a local folder.

    If `path` is omitted, uses `settings.docs_dir`. Only text/markdown/html files
    up to 2MB are processed. Results include counts of ingested and skipped files.
    """
    settings = request.app.state.settings
    _require_role(request, {"admin", "editor"})
    base = req.path or getattr(
        request.state, "docs_dir", getattr(settings, "docs_dir", "data/docs")
    )
    # Restrict to configured base directory (and its workspace subfolder) for safety
    configured = Path(
        getattr(request.state, "docs_dir", getattr(settings, "docs_dir", "data/docs"))
    ).resolve()
    target = Path(base).resolve()
    ws = _resolve_workspace(request) or "default"
    ws_dir = (configured / ws).resolve()
    allowed_roots = {configured, ws_dir}

    def _is_within(child: Path, parent: Path) -> bool:
        try:
            child = child.resolve()
            parent = parent.resolve()
        except Exception:
            return False
        return parent == child or parent in child.parents

    if not any(_is_within(target, root) for root in allowed_roots):
        # Fallback: if explicit env var matches exactly, allow (test/dev convenience)
        try:
            env_docs = os.getenv("UAMM_DOCS_DIR")
            if env_docs and Path(env_docs).resolve() == target:
                pass
            else:
                return JSONResponse(
                    status_code=400,
                    content={"error": "path must be within configured docs_dir"},
                )
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": "path must be within configured docs_dir"},
            )
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    # Environment warnings (parsers/ocr availability)
    warnings: list[str] = []
    try:
        import pypdf  # type: ignore  # noqa: F401
    except Exception:
        warnings.append("pdf_parser_missing")
    try:
        import docx  # type: ignore  # noqa: F401
    except Exception:
        warnings.append("docx_parser_missing")
    try:
        if getattr(settings, "docs_ocr_enabled", True):
            from pdf2image import convert_from_path  # type: ignore  # noqa: F401
            import pytesseract  # type: ignore  # noqa: F401
    except Exception:
        if getattr(settings, "docs_ocr_enabled", True):
            warnings.append("ocr_deps_missing")
    stats = scan_folder(eff_db, str(target), settings=settings)
    return {"ok": True, "path": str(target), "warnings": warnings, **stats}


@router.get("/rag/env")
def rag_env(request: Request):
    """Report ingestion environment readiness (parsers and OCR libraries/binaries)."""
    import shutil as _shutil

    settings = request.app.state.settings

    def _has(mod):
        try:
            __import__(mod)
            return True
        except Exception:
            return False

    env = {
        "python": {
            "pypdf": _has("pypdf"),
            "python_docx": _has("docx"),
            "pdf2image": _has("pdf2image"),
            "pytesseract": _has("pytesseract"),
        },
        "binaries": {
            "poppler": bool(_shutil.which("pdftoppm") or _shutil.which("pdfinfo")),
            "tesseract": bool(_shutil.which("tesseract")),
        },
        "ocr_enabled": bool(getattr(settings, "docs_ocr_enabled", True)),
        "allowed_exts": sorted(list(ALLOWED_EXTS)),
        "docs_dir": getattr(
            request.state, "docs_dir", getattr(settings, "docs_dir", "data/docs")
        ),
    }
    return env


@router.post("/rag/upload-file")
async def rag_upload_file(
    request: Request,
    file: bytes = File(...),
    filename: str | None = Form(None),
):
    """Upload a single document and ingest into the current workspace.

    Requires editor/admin role when auth is enabled.
    """
    _require_role(request, {"admin", "editor"})
    settings = request.app.state.settings
    ws = _resolve_workspace(request)
    docs_root = Path(
        getattr(request.state, "docs_dir", getattr(settings, "docs_dir", "data/docs"))
    ).resolve()
    target_dir = docs_root / ws
    target_dir.mkdir(parents=True, exist_ok=True)
    fn = filename or "upload.bin"
    dest = target_dir / Path(fn).name
    data = file or b""
    if len(data or b"") > 2 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "file_too_large"})
    dest.write_bytes(data or b"")
    # Prepare warnings based on file type and environment
    warnings: list[str] = []
    ext = dest.suffix.lower()
    if ext and ext not in ALLOWED_EXTS:
        warnings.append("unsupported_extension")
    if ext == ".pdf":
        try:
            import pypdf  # type: ignore  # noqa: F401
        except Exception:
            warnings.append("pdf_parser_missing")
        try:
            if getattr(settings, "docs_ocr_enabled", True):
                from pdf2image import convert_from_path  # type: ignore  # noqa: F401
                import pytesseract  # type: ignore  # noqa: F401
        except Exception:
            if getattr(settings, "docs_ocr_enabled", True):
                warnings.append("ocr_deps_missing")
    if ext == ".docx":
        try:
            import docx  # type: ignore  # noqa: F401
        except Exception:
            warnings.append("docx_parser_missing")
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    # Create a settings object with workspace information
    settings_with_ws = type(settings)(**settings.__dict__)
    settings_with_ws.workspace = ws
    stats = scan_folder(eff_db, str(target_dir), settings=settings_with_ws)
    return {"ok": True, "workspace": ws, "warnings": warnings, **stats}


@router.post("/rag/upload-files")
async def rag_upload_files(request: Request, files: List[UploadFile] = File(...)):
    """Upload multiple documents into the current workspace. Editor/admin only when auth is enabled."""
    _require_role(request, {"admin", "editor"})
    settings = request.app.state.settings
    ws = _resolve_workspace(request)
    docs_root = Path(
        getattr(request.state, "docs_dir", getattr(settings, "docs_dir", "data/docs"))
    ).resolve()
    target_dir = docs_root / ws
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    skipped = 0
    seen_pdf = False
    seen_docx = False
    seen_unsupported = False
    for uf in files or []:
        fn = (uf.filename or "upload.bin").strip()
        data = await uf.read()
        if len(data or b"") > 2 * 1024 * 1024:
            skipped += 1
            continue
        path = target_dir / Path(fn).name
        path.write_bytes(data or b"")
        ext = path.suffix.lower()
        if ext == ".pdf":
            seen_pdf = True
        elif ext == ".docx":
            seen_docx = True
        elif ext not in ALLOWED_EXTS:
            seen_unsupported = True
        saved += 1
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    # Create a settings object with workspace information
    settings_with_ws = type(settings)(**settings.__dict__)
    settings_with_ws.workspace = ws
    stats = scan_folder(eff_db, str(target_dir), settings=settings_with_ws)
    warnings: list[str] = []
    if seen_unsupported:
        warnings.append("unsupported_extension")
    if seen_pdf:
        try:
            import pypdf  # type: ignore  # noqa: F401
        except Exception:
            warnings.append("pdf_parser_missing")
        try:
            if getattr(settings, "docs_ocr_enabled", True):
                from pdf2image import convert_from_path  # type: ignore  # noqa: F401
                import pytesseract  # type: ignore  # noqa: F401
        except Exception:
            if getattr(settings, "docs_ocr_enabled", True):
                warnings.append("ocr_deps_missing")
    if seen_docx:
        try:
            import docx  # type: ignore  # noqa: F401
        except Exception:
            warnings.append("docx_parser_missing")
    return {
        "ok": True,
        "workspace": ws,
        "saved": saved,
        "skipped": skipped,
        "warnings": warnings,
        **stats,
    }


@router.get("/rag/search")
def rag_search(request: Request, q: str, k: int = 5):
    """Search the RAG corpus for relevant snippets."""
    settings = request.app.state.settings
    # View permission sufficient for reads when auth is enabled
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        # If auth is disabled, _require_role may still raise; we ignore if disabled
        if getattr(settings, "auth_required", False):
            raise
    _check_rate_limit(request)
    ws = _resolve_workspace(request)
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    hits = rag_search_docs(eff_db, q, k=k, workspace=ws)
    return {"hits": hits}


@router.get("/memory/search")
def memory_search(request: Request, q: str, k: int = 5):
    """Search previously stored memory items."""
    settings = request.app.state.settings
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    _check_rate_limit(request)
    ws = _resolve_workspace(request)
    # Mirror env override precedence used in memory_add for test/dev parity
    eff_db = (
        os.getenv("UAMM_DB_PATH")
        or getattr(request.state, "db_path", None)
        or settings.db_path
    )
    hits = db_search_memory(eff_db, q, k=k, workspace=ws)
    return {"hits": hits}


class MemoryPackRequest(BaseModel):
    question: str
    memory_budget: int = 8


@router.post("/memory/pack")
def memory_pack(req: MemoryPackRequest, request: Request):
    """Build a memory pack constrained by the supplied budget."""
    settings = request.app.state.settings
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    _check_rate_limit(request)
    ws = _resolve_workspace(request)
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    hits = db_search_memory(eff_db, req.question, k=req.memory_budget, workspace=ws)
    pack = [MemoryPackItem(**h) for h in hits]
    return {"pack": [p.model_dump() for p in pack]}


# (Removed duplicate workspace endpoints, see consolidated definitions below.)


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
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    _check_rate_limit(request)
    ws = _resolve_workspace(request)
    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    m_hits = db_search_memory(eff_db, req.question, k=req.memory_k, workspace=ws)
    c_hits = rag_search_docs(eff_db, req.question, k=req.corpus_k, workspace=ws)
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


# Workspaces & Keys
class WorkspaceCreateRequest(BaseModel):
    slug: str
    name: str | None = None
    root: str | None = None


@router.post("/workspaces")
def workspace_create(req: WorkspaceCreateRequest, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    try:
        root = None
        if req.root:
            # Normalize and validate against allowed bases
            root = normalize_root(req.root)
            ensure_allowed_root(
                root,
                tuple(getattr(settings, "workspace_base_dirs", []) or []),
                bool(getattr(settings, "workspace_restrict_to_bases", False)),
            )
            # Initialize FS and workspace DB
            ensure_workspace_fs(root, settings.schema_path)
        ws_create(con, req.slug, req.name or req.slug, root)
    finally:
        con.close()
    ws = ws_get(settings.db_path, req.slug)
    return {"workspace": ws}


@router.get("/workspaces")
def workspace_list(request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    return {"workspaces": ws_list(settings.db_path)}


@router.get("/workspaces/{slug}")
def workspace_get(slug: str, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    ws = ws_get(settings.db_path, slug)
    if not ws:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return {"workspace": ws}


@router.get("/workspaces/{slug}/stats")
def workspace_stats(slug: str, request: Request):
    """Return basic stats for a workspace: paths, counts, last activity.

    Counts are resolved against the effective workspace DB. If the workspace uses
    a shared DB, counts are filtered by workspace slug where applicable.
    """
    import sqlite3 as _sqlite3

    settings = request.app.state.settings
    # Resolve effective paths
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    dbp = paths.get("db_path", settings.db_path)
    doc_root = paths.get("docs_dir", settings.docs_dir)
    out = {
        "paths": {
            "db_path": dbp,
            "docs_dir": doc_root,
            "lancedb_uri": paths.get("lancedb_uri", ""),
        },
        "counts": {"steps": 0, "docs": 0},
        "last_step_ts": None,
        "last_step_id": None,
        "doc_latest": None,
    }
    try:
        con = _sqlite3.connect(dbp)
        con.row_factory = _sqlite3.Row
        # Steps
        try:
            # If shared DB, filter by workspace; otherwise table may omit workspace filter but it's harmless
            row = con.execute(
                "SELECT COUNT(*) as c, MAX(ts) as last FROM steps WHERE workspace = ?",
                (slug,),
            ).fetchone()
            if row:
                out["counts"]["steps"] = int(row["c"] or 0)
                out["last_step_ts"] = (
                    float(row["last"]) if row["last"] is not None else None
                )
            row2 = con.execute(
                "SELECT id FROM steps WHERE workspace = ? ORDER BY ts DESC LIMIT 1",
                (slug,),
            ).fetchone()
            if row2:
                out["last_step_id"] = row2["id"]
        except Exception:
            pass
        # Docs corpus
        try:
            row = con.execute(
                "SELECT COUNT(*) as c FROM corpus WHERE workspace = ?",
                (slug,),
            ).fetchone()
            if row:
                out["counts"]["docs"] = int(row["c"] or 0)
            rowd = con.execute(
                "SELECT id, title, url, ts FROM corpus WHERE workspace = ? ORDER BY ts DESC LIMIT 1",
                (slug,),
            ).fetchone()
            if rowd:
                out["doc_latest"] = {
                    "id": rowd["id"],
                    "title": rowd["title"],
                    "url": rowd["url"],
                    "ts": float(rowd["ts"]) if rowd["ts"] is not None else None,
                }
        except Exception:
            pass
        con.close()
    except Exception:
        pass
    return out


@router.get("/workspaces/{slug}/corpus")
def workspace_corpus_list(slug: str, request: Request, limit: int = 20):
    """List recent corpus documents for a workspace (id, title, url, ts, meta excerpt).

    Read-only; allows viewer/editor/admin when auth is enabled.
    """
    import sqlite3 as _sqlite3

    settings = request.app.state.settings
    # View permission sufficient for reads when auth is enabled
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    limit = max(1, min(200, int(limit or 20)))
    # Resolve effective workspace paths by slug (do not rely solely on middleware state)
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    db_path = paths.get("db_path", settings.db_path)
    con = _sqlite3.connect(db_path)
    con.row_factory = _sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, ts, title, url, meta FROM corpus WHERE workspace = ? ORDER BY ts DESC LIMIT ?",
            (slug, limit),
        ).fetchall()
        docs: list[dict[str, object]] = []
        for r in rows:
            # meta is stored as JSON; return raw string to keep payload small
            docs.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "url": r["url"],
                    "ts": float(r["ts"]) if r["ts"] is not None else None,
                    "meta": r["meta"],
                }
            )
    finally:
        con.close()
    return {"workspace": slug, "docs": docs}


@router.get("/workspaces/{slug}/corpus/files")
def workspace_corpus_files(slug: str, request: Request, limit: int = 50):
    """List recent files and their ingestion status from `corpus_files` for a workspace.

    Filters paths under <docs_dir>/<workspace>. Returns: path, name, mtime, doc_id, status, reason, chunks, ext, size.
    """
    import sqlite3 as _sqlite3
    import json as _json
    from pathlib import Path as _Path

    settings = request.app.state.settings
    # View permission sufficient for reads when auth is enabled
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    limit = max(1, min(500, int(limit or 50)))
    # Resolve effective workspace paths by slug (do not rely solely on middleware state)
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    docs_root = _Path(
        paths.get("docs_dir", getattr(settings, "docs_dir", "data/docs"))
    ).resolve()
    ws_dir = (docs_root / slug).resolve()
    db_path = paths.get("db_path", settings.db_path)
    con = _sqlite3.connect(db_path)
    con.row_factory = _sqlite3.Row
    try:
        # Ensure table exists (defensive)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_files (
              path TEXT PRIMARY KEY,
              mtime REAL,
              doc_id TEXT,
              meta TEXT,
              workspace TEXT
            )
            """
        )
        # Best-effort backfill of workspace for existing rows by path prefix
        try:
            like_pf = str(ws_dir) + "%"
            con.execute(
                "UPDATE corpus_files SET workspace = ? WHERE (workspace IS NULL OR workspace = '') AND path LIKE ?",
                (slug, like_pf),
            )
            con.commit()
        except Exception:
            pass
        # Filter by workspace first, then by path prefix as fallback
        rows = con.execute(
            "SELECT path, mtime, doc_id, meta, workspace FROM corpus_files WHERE workspace = ? ORDER BY mtime DESC LIMIT ?",
            (slug, limit),
        ).fetchall()
        # If no results with workspace filter, fall back to path-based filtering
        if not rows:
            like = str(ws_dir) + "%"
            rows = con.execute(
                "SELECT path, mtime, doc_id, meta, workspace FROM corpus_files WHERE path LIKE ? ORDER BY mtime DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        files: list[dict[str, object]] = []
        for r in rows:
            meta_raw = r["meta"] or "{}"
            try:
                meta_obj = _json.loads(meta_raw)
            except Exception:
                meta_obj = {"raw": meta_raw}
            name = _Path(str(r["path"])).name
            files.append(
                {
                    "path": r["path"],
                    "name": name,
                    "mtime": float(r["mtime"]) if r["mtime"] is not None else None,
                    "doc_id": r["doc_id"],
                    "status": meta_obj.get("status"),
                    "reason": meta_obj.get("reason"),
                    "chunks": meta_obj.get("chunks"),
                    "ext": meta_obj.get("ext"),
                    "size": meta_obj.get("size"),
                    "workspace": r["workspace"],
                }
            )
    finally:
        con.close()
    return {"workspace": slug, "files": files}


@router.get("/workspaces/{slug}/corpus/file")
def workspace_corpus_file_detail(
    slug: str, request: Request, path: str, limit_chunks: int = 100
):
    """Return detail for a specific ingested file: status row and related corpus chunks.

    The `path` must be an absolute path within <docs_dir>/<workspace>. Chunks are sourced from
    `corpus` by matching the `url` column to `file:{path}` and filtering by workspace.
    """
    import sqlite3 as _sqlite3
    import json as _json
    from pathlib import Path as _Path

    settings = request.app.state.settings
    # View permission sufficient for reads when auth is enabled
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    # Resolve effective workspace paths and safety-check: path must be under docs_dir/<workspace>
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    docs_root = _Path(
        paths.get("docs_dir", getattr(settings, "docs_dir", "data/docs"))
    ).resolve()
    ws_dir = (docs_root / slug).resolve()
    p = _Path(path).resolve()
    if ws_dir not in p.parents and ws_dir != p:
        return JSONResponse(status_code=400, content={"error": "path_out_of_workspace"})
    limit_chunks = max(1, min(500, int(limit_chunks or 100)))
    db_path = paths.get("db_path", getattr(request.state, "db_path", settings.db_path))
    con = _sqlite3.connect(db_path)
    con.row_factory = _sqlite3.Row
    try:
        # Fetch file status
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_files (
              path TEXT PRIMARY KEY,
              mtime REAL,
              doc_id TEXT,
              meta TEXT,
              workspace TEXT
            )
            """
        )
        rowf = con.execute(
            "SELECT path, mtime, doc_id, meta FROM corpus_files WHERE path = ?",
            (str(p),),
        ).fetchone()
        file_info = None
        if rowf:
            try:
                meta_obj = _json.loads(rowf["meta"] or "{}")
            except Exception:
                meta_obj = {"raw": rowf["meta"]}
            file_info = {
                "path": rowf["path"],
                "name": _Path(str(rowf["path"])).name,
                "mtime": float(rowf["mtime"]) if rowf["mtime"] is not None else None,
                "doc_id": rowf["doc_id"],
                "status": meta_obj.get("status"),
                "reason": meta_obj.get("reason"),
                "chunks": meta_obj.get("chunks"),
                "ext": meta_obj.get("ext"),
                "size": meta_obj.get("size"),
            }
        # Fetch related corpus chunks
        url = f"file:{str(p)}"
        rows = con.execute(
            "SELECT id, ts, title, url, text, meta FROM corpus WHERE workspace = ? AND url = ? ORDER BY ts ASC LIMIT ?",
            (slug, url, limit_chunks),
        ).fetchall()
        chunks = []
        for r in rows:
            try:
                meta = _json.loads(r["meta"] or "{}")
            except Exception:
                meta = {}
            ci = meta.get("chunk_index")
            ct = meta.get("chunk_total")
            text = r["text"] or ""
            snippet = (text[:480]) if text else ""
            chunks.append(
                {
                    "id": r["id"],
                    "ts": float(r["ts"]) if r["ts"] is not None else None,
                    "title": r["title"],
                    "snippet": snippet,
                    "chunk_index": ci,
                    "chunk_total": ct,
                    "meta": meta,
                }
            )
        chunks.sort(
            key=lambda x: (
                x["chunk_index"] if isinstance(x.get("chunk_index"), int) else 1e9
            )
        )
    finally:
        con.close()
    summary = {
        "chunks": len(chunks),
        "first_snippet": chunks[0]["snippet"] if chunks else "",
    }
    return {"workspace": slug, "file": file_info, "chunks": chunks, "summary": summary}


@router.get("/workspaces/{slug}/corpus/files/history")
def workspace_corpus_files_history(slug: str, request: Request, limit: int = 100):
    """List recent file ingestion events from corpus_files_history for a workspace."""
    import sqlite3 as _sqlite3
    import os as _os

    settings = request.app.state.settings
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    limit = max(1, min(1000, int(limit or 100)))
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    db_path = paths.get("db_path", getattr(request.state, "db_path", settings.db_path))
    con = _sqlite3.connect(db_path)
    con.row_factory = _sqlite3.Row
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_files_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT,
              mtime REAL,
              ts REAL,
              doc_id TEXT,
              status TEXT,
              reason TEXT,
              ext TEXT,
              size INTEGER,
              workspace TEXT
            )
            """
        )
        rows = con.execute(
            "SELECT id, path, mtime, ts, doc_id, status, reason, ext, size FROM corpus_files_history WHERE workspace = ? ORDER BY ts DESC LIMIT ?",
            (slug, limit),
        ).fetchall()
        events = []
        for r in rows:
            events.append(
                {
                    "id": r["id"],
                    "path": r["path"],
                    "name": _os.path.basename(r["path"]) if r["path"] else "",
                    "mtime": float(r["mtime"]) if r["mtime"] is not None else None,
                    "ts": float(r["ts"]) if r["ts"] is not None else None,
                    "doc_id": r["doc_id"],
                    "status": r["status"],
                    "reason": r["reason"],
                    "ext": r["ext"],
                    "size": r["size"],
                }
            )
    finally:
        con.close()
    return {"workspace": slug, "events": events}


@router.get("/workspaces/{slug}/corpus/file/history")
def workspace_corpus_file_history(
    slug: str, request: Request, path: str, limit: int = 200
):
    """List history events for a specific file path within a workspace."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    settings = request.app.state.settings
    try:
        _require_role(request, {"admin", "editor", "viewer"})
    except HTTPException:
        if getattr(settings, "auth_required", False):
            raise
    # Resolve effective workspace paths and safety-check
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    docs_root = _Path(
        paths.get("docs_dir", getattr(settings, "docs_dir", "data/docs"))
    ).resolve()
    ws_dir = (docs_root / slug).resolve()
    p = _Path(path).resolve()
    if ws_dir not in p.parents and ws_dir != p:
        return JSONResponse(status_code=400, content={"error": "path_out_of_workspace"})
    limit = max(1, min(1000, int(limit or 200)))
    db_path = paths.get("db_path", getattr(request.state, "db_path", settings.db_path))
    con = _sqlite3.connect(db_path)
    con.row_factory = _sqlite3.Row
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_files_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT,
              mtime REAL,
              ts REAL,
              doc_id TEXT,
              status TEXT,
              reason TEXT,
              ext TEXT,
              size INTEGER,
              workspace TEXT
            )
            """
        )
        rows = con.execute(
            "SELECT id, path, mtime, ts, doc_id, status, reason, ext, size FROM corpus_files_history WHERE workspace = ? AND path = ? ORDER BY ts DESC LIMIT ?",
            (slug, str(p), limit),
        ).fetchall()
        events = []
        for r in rows:
            events.append(
                {
                    "id": r["id"],
                    "path": r["path"],
                    "mtime": float(r["mtime"]) if r["mtime"] is not None else None,
                    "ts": float(r["ts"]) if r["ts"] is not None else None,
                    "doc_id": r["doc_id"],
                    "status": r["status"],
                    "reason": r["reason"],
                    "ext": r["ext"],
                    "size": r["size"],
                }
            )
    finally:
        con.close()
    return {"workspace": slug, "path": str(p), "events": events}


class WorkspaceDeleteRequest(BaseModel):
    purge: bool = False  # when true, attempt to remove filesystem root safely


@router.post("/workspaces/{slug}/delete")
def workspace_delete(slug: str, req: WorkspaceDeleteRequest, request: Request):
    """Delete a workspace record (and optionally purge its filesystem root).

    Purge only removes files under the recorded root when configured bases allow it.
    The operation is guarded by admin role.
    """
    _require_role(request, {"admin"})
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    settings = request.app.state.settings
    # Fetch workspace to identify root before deleting
    con = _sqlite3.connect(settings.db_path)
    con.row_factory = _sqlite3.Row
    ws = con.execute(
        "SELECT root FROM workspaces WHERE slug = ?",
        (slug,),
    ).fetchone()
    if not ws:
        con.close()
        return JSONResponse(status_code=404, content={"error": "not_found"})
    root = ws["root"]
    # Delete associated keys/members/policies first for referential hygiene
    with con:
        con.execute("DELETE FROM workspace_keys WHERE workspace = ?", (slug,))
        con.execute("DELETE FROM workspace_members WHERE workspace = ?", (slug,))
        con.execute("DELETE FROM workspace_policies WHERE workspace = ?", (slug,))
        con.execute("DELETE FROM workspaces WHERE slug = ?", (slug,))
    con.close()
    removed = False
    if req.purge and root:
        try:
            # Only purge when restriction allows the root under allowed bases
            ensure_allowed_root(
                normalize_root(root),
                tuple(getattr(settings, "workspace_base_dirs", []) or []),
                bool(getattr(settings, "workspace_restrict_to_bases", False)),
            )
            r = _Path(root).resolve()
            # Only remove if within allowed bases; remove files best-effort (non-recursive heavy safety)
            import shutil as _shutil

            _shutil.rmtree(r, ignore_errors=True)
            removed = True
        except Exception:
            removed = False
    return {"ok": True, "purged": removed}


@router.get("/workspaces/{slug}/trend")
def workspace_trend(slug: str, request: Request, days: int = 7):
    """Return simple doc/step counts per day for the last N days for a workspace.

    Buckets by UTC day. Intended for tiny sparkline displays in the UI.
    """
    import time as _t
    import sqlite3 as _sqlite3

    settings = request.app.state.settings
    paths = ws_resolve_paths(settings.db_path, slug, settings)
    dbp = paths.get("db_path", settings.db_path)
    now_day = int(_t.time() // 86400)
    days = max(1, min(30, int(days or 7)))
    buckets = list(range(now_day - (days - 1), now_day + 1))
    steps_counts = {d: 0 for d in buckets}
    docs_counts = {d: 0 for d in buckets}
    try:
        con = _sqlite3.connect(dbp)
        con.row_factory = _sqlite3.Row
        # Steps per day
        try:
            rows = con.execute(
                "SELECT CAST(ts/86400 AS INT) AS day, COUNT(*) AS c FROM steps WHERE workspace = ? GROUP BY day",
                (slug,),
            ).fetchall()
            for r in rows:
                d = int(r["day"]) if r["day"] is not None else None
                if d in steps_counts:
                    steps_counts[d] = int(r["c"] or 0)
        except Exception:
            pass
        # Docs per day
        try:
            rows = con.execute(
                "SELECT CAST(ts/86400 AS INT) AS day, COUNT(*) AS c FROM corpus WHERE workspace = ? GROUP BY day",
                (slug,),
            ).fetchall()
            for r in rows:
                d = int(r["day"]) if r["day"] is not None else None
                if d in docs_counts:
                    docs_counts[d] = int(r["c"] or 0)
        except Exception:
            pass
        con.close()
    except Exception:
        pass
    return {
        "days": buckets,
        "steps": [steps_counts[d] for d in buckets],
        "docs": [docs_counts[d] for d in buckets],
    }


class IssueKeyRequest(BaseModel):
    role: str
    label: str


@router.post("/workspaces/{slug}/keys")
def workspace_issue_key(slug: str, req: IssueKeyRequest, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    token = ws_issue_key(
        settings.db_path,
        workspace=slug,
        role=req.role,
        label=req.label,
        prefix=getattr(settings, "api_key_prefix", "wk_"),
    )
    return {"api_key": token}


@router.get("/workspaces/{slug}/keys")
def workspace_list_keys(slug: str, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    keys = ws_list_keys(settings.db_path, workspace=slug)
    redacted = [
        {
            "id": k.id,
            "workspace": k.workspace,
            "role": k.role,
            "label": k.label,
            "active": k.active,
            "created": k.created,
        }
        for k in keys
    ]
    return {"keys": redacted}


@router.post("/workspaces/{slug}/keys/{key_id}/deactivate")
def workspace_deactivate_key(slug: str, key_id: str, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    ws_deactivate(settings.db_path, key_id=key_id)
    return {"ok": True}


# Workspace members
class MemberRequest(BaseModel):
    user_id: str
    role: str


@router.post("/workspaces/{slug}/members")
def workspace_add_member(slug: str, req: MemberRequest, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO workspace_members(workspace, user_id, role, added) VALUES (?, ?, ?, ?)",
            (slug, req.user_id, req.role, time.time()),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True}


@router.get("/workspaces/{slug}/members")
def workspace_list_members(slug: str, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT workspace, user_id, role, added FROM workspace_members WHERE workspace = ?",
            (slug,),
        ).fetchall()
    finally:
        con.close()
    return {
        "members": [
            {
                "workspace": r["workspace"],
                "user_id": r["user_id"],
                "role": r["role"],
                "added": float(r["added"]),
            }
            for r in rows
        ]
    }


@router.delete("/workspaces/{slug}/members/{user_id}")
def workspace_remove_member(slug: str, user_id: str, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    try:
        con.execute(
            "DELETE FROM workspace_members WHERE workspace = ? AND user_id = ?",
            (slug, user_id),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True}


# Audit
@router.get("/audit/contributions")
def audit_contributions(request: Request, user: str | None = None):
    settings = request.app.state.settings
    ws = _resolve_workspace(request)
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    try:
        params = [ws]
        where_user = ""
        if user:
            where_user = " AND created_by = ?"
            params.append(user)
        mem = con.execute(
            f"SELECT created_by, COUNT(*) as n, MAX(ts) as last_ts FROM memory WHERE workspace = ?{where_user} GROUP BY created_by",
            params,
        ).fetchall()
        cor = con.execute(
            f"SELECT created_by, COUNT(*) as n, MAX(ts) as last_ts FROM corpus WHERE workspace = ?{where_user} GROUP BY created_by",
            params,
        ).fetchall()
    finally:
        con.close()
    return {
        "workspace": ws,
        "memory": [
            {
                "created_by": r["created_by"],
                "n": int(r["n"]),
                "last_ts": float(r["last_ts"]) if r["last_ts"] is not None else None,
            }
            for r in mem
        ],
        "corpus": [
            {
                "created_by": r["created_by"],
                "n": int(r["n"]),
                "last_ts": float(r["last_ts"]) if r["last_ts"] is not None else None,
            }
            for r in cor
        ],
    }


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
        llm_enabled=bool(body.get("llm_enabled", False)),
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


@router.get("/evals/suites")
def evals_suites(request: Request):
    """List available eval suites and basic metadata for UI selection."""
    suites = [
        {
            "id": s.id,
            "label": s.label,
            "description": s.description,
            "category": s.category,
            "cp_enabled": s.cp_enabled,
            "use_cp_decision": s.use_cp_decision,
            "max_refinements": s.max_refinements,
            "tool_budget_per_refinement": s.tool_budget_per_refinement,
            "tool_budget_per_turn": s.tool_budget_per_turn,
            "tags": list(s.tags),
        }
        for s in eval_list_suites()
    ]
    return {"suites": suites}


@router.get("/evals/env")
def evals_env(request: Request):
    """Report whether real LLM generation is available on this server.

    This checks for pydantic_ai + openai client importability and the presence of
    an OpenAI API key in the environment. It does not make a network call.
    """
    import importlib
    import os as _os

    ok_pai = False
    ok_openai = False
    try:
        importlib.import_module("pydantic_ai")
        ok_pai = True
    except Exception:
        ok_pai = False
    try:
        importlib.import_module("openai")
        ok_openai = True
    except Exception:
        ok_openai = False
    key_present = bool(_os.getenv("OPENAI_API_KEY"))
    embedding_backend = _os.getenv("UAMM_EMBEDDING_BACKEND", "openai").lower()
    return {
        "llm_available": bool(ok_pai and ok_openai and key_present),
        "openai_key": bool(key_present),
        "embedding_backend": embedding_backend,
    }


@router.get("/evals/run/stream")
def evals_run_stream(
    request: Request,
    suites: str,
    update_cp: bool = True,
    llm: bool = False,
    run_id: str | None = None,
) -> Response:
    """Stream per-item progress for one or more suites via SSE.

    Query params:
    - suites: comma-separated suite IDs (e.g., CP-B1,Stack-G1)
    - update_cp: when true, write CP artifacts for suites that record them
    - llm: when true, attempt real LLM generation if available
    - run_id: optional run id label (else auto)
    """
    ids = [s.strip() for s in (suites or "").split(",") if s.strip()]
    if not ids:
        return JSONResponse(status_code=400, content={"error": "missing suites"})
    settings = request.app.state.settings
    rid = run_id or f"run-{int(time.time())}"

    def se(evt: str, data: Dict[str, Any]) -> str:
        return _sse_event(evt, data)

    def gen():
        yield se("ready", {"run_id": rid})
        for sid in ids:
            try:
                suite = eval_get_suite(sid)
            except KeyError:
                yield se("error", {"suite_id": sid, "message": "unknown_suite"})
                continue
            items = eval_load_items(suite.dataset_path)
            yield se(
                "suite_start",
                {"suite_id": sid, "label": suite.label, "total": len(items)},
            )
            recs: list[dict] = []
            for idx, item in enumerate(items, start=1):
                # Run single-item eval for immediate result
                rs = run_evals(
                    items=[item],
                    accept_threshold=settings.accept_threshold,
                    cp_enabled=suite.cp_enabled,
                    tool_budget_per_refinement=suite.tool_budget_per_refinement,
                    tool_budget_per_turn=suite.tool_budget_per_turn,
                    max_refinements=suite.max_refinements,
                    use_cp_decision=suite.use_cp_decision,
                    llm_enabled=llm,
                )
                rec = rs[0] if rs else {}
                recs.append(rec)
                # Incremental metrics
                m = suite_summarize_records(recs)
                yield se(
                    "item",
                    {
                        "suite_id": sid,
                        "index": idx,
                        "record": rec,
                        "metrics": m,
                        "total": len(items),
                    },
                )
            # Finalize suite
            metrics = suite_summarize_records(recs)
            by_dom = suite_summarize_by_domain(recs)
            # Persist run + optional CP artifacts
            try:
                store_eval_run(
                    settings.db_path,
                    run_id=rid,
                    suite_id=sid,
                    metrics=metrics,
                    by_domain=by_dom,
                    records=recs,
                    notes={"type": "suite"},
                )
                if update_cp and suite.record_cp_artifacts:
                    total_inserted = 0
                    refs: Dict[str, Any] = {}
                    grouped: Dict[str, list[dict]] = {}
                    for r in recs:
                        dom = str(r.get("domain", "default"))
                        grouped.setdefault(dom, []).append(r)
                    for dom, rs in grouped.items():
                        tuples = [
                            (float(r["S"]), bool(r["accepted"]), bool(r["correct"]))
                            for r in rs
                        ]
                        total_inserted += cp_store.add_artifacts(
                            settings.db_path, run_id=rid, domain=dom, items=tuples
                        )
                        tau = cp_store.compute_threshold(
                            settings.db_path,
                            domain=dom,
                            target_mis=settings.cp_target_mis,
                        )
                        stats_dom = cp_store.domain_stats(
                            settings.db_path, domain=dom
                        ).get(dom, {})
                        quantiles = quantiles_from_scores(
                            [float(r["S"]) for r in rs], DRIFT_QUANTILES
                        )
                        upsert_reference(
                            settings.db_path,
                            domain=dom,
                            run_id=rid,
                            target_mis=settings.cp_target_mis,
                            tau=tau,
                            stats=stats_dom,
                            snne_quantiles=quantiles,
                        )
                        refs[dom] = {
                            "tau": tau,
                            "stats": stats_dom,
                            "quantiles": quantiles,
                        }
            except Exception:
                pass
            yield se(
                "suite_done",
                {
                    "suite_id": sid,
                    "metrics": metrics,
                    "by_domain": by_dom,
                    "count": len(recs),
                },
            )
        yield se("final", {"run_id": rid, "suites": ids})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/evals/run/adhoc/stream")
def evals_run_adhoc_stream(
    request: Request,
    items: str,
    run_id: str | None = None,
    domain: str | None = None,
    max_refinements: int = 0,
    tool_budget_per_turn: int = 0,
    tool_budget_per_refinement: int = 0,
    cp_enabled: bool = False,
    use_cp_decision: bool | None = None,
    llm: bool = False,
    suite_name: str | None = "adhoc",
    record_cp: bool = False,
) -> Response:
    """Stream ad-hoc eval items via SSE.

    Query params:
    - items: JSON-encoded array of {question, domain, correct}
    - run_id: optional run id label (else auto)
    - domain: optional default domain to apply when missing in items
    - max_refinements/tool_budget_*: planning controls
    - cp_enabled/use_cp_decision: CP gating behaviour
    - llm: when true, attempt real LLM generation if available
    - suite_name: stored label for this ad-hoc run (default 'adhoc')
    - record_cp: when true, persist CP artifacts and update references
    """
    try:
        parsed = json.loads(items)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid items json"})
    if not isinstance(parsed, list) or not all(isinstance(x, dict) for x in parsed):
        return JSONResponse(
            status_code=400, content={"error": "items must be a JSON array of objects"}
        )
    settings = request.app.state.settings
    rid = run_id or f"run-{int(time.time())}"

    # Normalize items with default domain fallback
    norm_items: list[dict[str, Any]] = []
    for it in parsed:
        entry = dict(it)
        if domain and not entry.get("domain"):
            entry["domain"] = str(domain)
        if not entry.get("domain"):
            entry["domain"] = "default"
        norm_items.append(entry)

    def se(evt: str, data: Dict[str, Any]) -> str:
        return _sse_event(evt, data)

    def gen():
        yield se("ready", {"run_id": rid, "count": len(norm_items)})
        recs: list[dict] = []
        for idx, item in enumerate(norm_items, start=1):
            rs = run_evals(
                items=[item],
                accept_threshold=settings.accept_threshold,
                cp_enabled=cp_enabled,
                tool_budget_per_refinement=tool_budget_per_refinement,
                tool_budget_per_turn=tool_budget_per_turn,
                max_refinements=max_refinements,
                use_cp_decision=use_cp_decision,
                llm_enabled=llm,
            )
            rec = rs[0] if rs else {}
            recs.append(rec)
            # Incremental metrics
            m = suite_summarize_records(recs)
            yield se(
                "item",
                {"index": idx, "record": rec, "metrics": m, "total": len(norm_items)},
            )
        # Finalize and persist
        metrics = suite_summarize_records(recs)
        by_dom = suite_summarize_by_domain(recs)
        try:
            store_eval_run(
                settings.db_path,
                run_id=rid,
                suite_id=suite_name or "adhoc",
                metrics=metrics,
                by_domain=by_dom,
                records=recs,
                notes={"type": "custom", "item_count": len(recs)},
            )
            if record_cp:
                total_inserted = 0
                refs: Dict[str, Any] = {}
                grouped: Dict[str, list[dict]] = {}
                for r in recs:
                    dom = str(r.get("domain", "default"))
                    grouped.setdefault(dom, []).append(r)
                for dom, rs in grouped.items():
                    tuples = [
                        (float(r["S"]), bool(r["accepted"]), bool(r["correct"]))
                        for r in rs
                    ]
                    total_inserted += cp_store.add_artifacts(
                        settings.db_path, run_id=rid, domain=dom, items=tuples
                    )
                    tau = cp_store.compute_threshold(
                        settings.db_path, domain=dom, target_mis=settings.cp_target_mis
                    )
                    stats_dom = cp_store.domain_stats(settings.db_path, domain=dom).get(
                        dom, {}
                    )
                    quantiles = quantiles_from_scores(
                        [float(r["S"]) for r in rs], DRIFT_QUANTILES
                    )
                    upsert_reference(
                        settings.db_path,
                        domain=dom,
                        run_id=rid,
                        target_mis=settings.cp_target_mis,
                        tau=tau,
                        stats=stats_dom,
                        snne_quantiles=quantiles,
                    )
                    refs[dom] = {"tau": tau, "stats": stats_dom, "quantiles": quantiles}
        except Exception:
            # Persist errors are non-fatal for streaming
            pass
        yield se(
            "final",
            {
                "run_id": rid,
                "metrics": metrics,
                "by_domain": by_dom,
                "count": len(recs),
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/evals/runs")
def evals_runs(request: Request, limit: int = 20):
    """List recent eval run_ids with summary info."""
    import sqlite3

    settings = request.app.state.settings
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT run_id, MAX(ts) as ts, COUNT(*) as suites
        FROM eval_runs
        GROUP BY run_id
        ORDER BY ts DESC
        LIMIT ?
        """,
        (max(1, min(200, limit)),),
    ).fetchall()
    con.close()
    out = [
        {"run_id": r["run_id"], "ts": float(r["ts"]), "suites": int(r["suites"])}
        for r in rows
    ]
    return {"runs": out}


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
        # Faithfulness summary (global and by_domain)
        faith = m.get("faithfulness", {}) or {}
        fbd = m.get("faithfulness_by_domain", {}) or {}
        try:
            f_count = int(faith.get("count", 0) or 0)
            f_sum = float(faith.get("sum", 0.0) or 0.0)
            f_claims = int(faith.get("claim_count", 0) or 0)
            f_unsupported = int(faith.get("unsupported_total", 0) or 0)
            f_avg = (f_sum / f_count) if f_count > 0 else None
            m_out["faithfulness_summary"] = {
                "avg_score": f_avg,
                "claims": f_claims,
                "unsupported": f_unsupported,
            }
            if fbd:
                summary: Dict[str, Any] = {}
                for dom, stats in fbd.items():
                    c = int((stats or {}).get("count", 0) or 0)
                    s = float((stats or {}).get("sum", 0.0) or 0.0)
                    cc = int((stats or {}).get("claim_count", 0) or 0)
                    uu = int((stats or {}).get("unsupported_total", 0) or 0)
                    avg = (s / c) if c > 0 else None
                    summary[dom] = {"avg_score": avg, "claims": cc, "unsupported": uu}
                m_out["faithfulness_by_domain_summary"] = summary
        except Exception:
            pass
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
        # MCP metrics snapshot (best-effort)
        try:
            from uamm.mcp.server import mcp_metrics_snapshot  # type: ignore

            mcp_stats = mcp_metrics_snapshot()
            if mcp_stats:
                m_out["mcp"] = mcp_stats
                request.app.state.metrics["mcp"] = mcp_stats
        except Exception:
            pass
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
    # GoV assertions
    govm = m.get("gov_assertions", {}) or {}
    lines.append("# HELP uamm_assertions_total GoV assertions runs")
    lines.append("# TYPE uamm_assertions_total counter")
    lines.append(f"uamm_assertions_total {int(govm.get('runs', 0) or 0)}")
    lines.append("# HELP uamm_assertions_fail_total GoV assertions failures")
    lines.append("# TYPE uamm_assertions_fail_total counter")
    lines.append(f"uamm_assertions_fail_total {int(govm.get('fail', 0) or 0)}")
    by_pred = govm.get("by_pred", {}) or {}
    if by_pred:
        lines.append(
            "# HELP uamm_assertions_by_pred_total GoV assertions runs by predicate"
        )
        lines.append("# TYPE uamm_assertions_by_pred_total counter")
        for pred, st in by_pred.items():
            lines.append(
                f'uamm_assertions_by_pred_total{{predicate="{pred}"}} {int((st or {}).get("runs", 0) or 0)}'
            )
        lines.append(
            "# HELP uamm_assertions_fail_by_pred_total GoV assertions failures by predicate"
        )
        lines.append("# TYPE uamm_assertions_fail_by_pred_total counter")
        for pred, st in by_pred.items():
            lines.append(
                f'uamm_assertions_fail_by_pred_total{{predicate="{pred}"}} {int((st or {}).get("fail", 0) or 0)}'
            )
    # Units checks
    units = m.get("units_checks", {}) or {}
    lines.append("# HELP uamm_units_checks_total Units checks runs")
    lines.append("# TYPE uamm_units_checks_total counter")
    lines.append(f"uamm_units_checks_total {int(units.get('runs', 0) or 0)}")
    lines.append("# HELP uamm_units_checks_fail_total Units checks failures")
    lines.append("# TYPE uamm_units_checks_fail_total counter")
    lines.append(f"uamm_units_checks_fail_total {int(units.get('fail', 0) or 0)}")
    # SQL checks
    sqlm = m.get("sql_checks", {}) or {}
    lines.append("# HELP uamm_sql_checks_total SQL checks runs")
    lines.append("# TYPE uamm_sql_checks_total counter")
    lines.append(f"uamm_sql_checks_total {int(sqlm.get('runs', 0) or 0)}")
    lines.append("# HELP uamm_sql_checks_fail_total SQL checks failures")
    lines.append("# TYPE uamm_sql_checks_fail_total counter")
    lines.append(f"uamm_sql_checks_fail_total {int(sqlm.get('fail', 0) or 0)}")
    # Memory promotions
    memory = m.get("memory", {}) or {}
    lines.append("# HELP uamm_memory_promotions_total Semantic memory promotions")
    lines.append("# TYPE uamm_memory_promotions_total counter")
    lines.append(
        f"uamm_memory_promotions_total {int(memory.get('promotions', 0) or 0)}"
    )
    # MCP metrics
    mcp = m.get("mcp", {}) or {}
    lines.append("# HELP uamm_mcp_requests_total MCP adapter requests")
    lines.append("# TYPE uamm_mcp_requests_total counter")
    lines.append(f"uamm_mcp_requests_total {int(mcp.get('requests', 0) or 0)}")
    lines.append("# HELP uamm_mcp_errors_total MCP adapter errors")
    lines.append("# TYPE uamm_mcp_errors_total counter")
    lines.append(f"uamm_mcp_errors_total {int(mcp.get('errors', 0) or 0)}")
    by_tool = mcp.get("by_tool", {}) or {}
    if by_tool:
        lines.append(
            "# HELP uamm_mcp_requests_by_tool_total MCP adapter requests by tool"
        )
        lines.append("# TYPE uamm_mcp_requests_by_tool_total counter")
        for tool, cnt in by_tool.items():
            lines.append(
                f'uamm_mcp_requests_by_tool_total{{tool="{tool}"}} {int(cnt or 0)}'
            )
    # Guardrails counters
    guard = m.get("guardrails", {}) or {}
    lines.append("# HELP uamm_guardrails_violations_pre_total Pre-guard violations")
    lines.append("# TYPE uamm_guardrails_violations_pre_total counter")
    lines.append(
        f"uamm_guardrails_violations_pre_total {int(guard.get('pre', 0) or 0)}"
    )
    lines.append("# HELP uamm_guardrails_violations_post_total Post-guard violations")
    lines.append("# TYPE uamm_guardrails_violations_post_total counter")
    lines.append(
        f"uamm_guardrails_violations_post_total {int(guard.get('post', 0) or 0)}"
    )
    # Planning counters
    planning = m.get("planning", {}) or {}
    lines.append("# HELP uamm_planning_runs_total Planning invocations observed")
    lines.append("# TYPE uamm_planning_runs_total counter")
    lines.append(f"uamm_planning_runs_total {int(planning.get('runs', 0) or 0)}")
    lines.append(
        "# HELP uamm_planning_improvements_total Planning rounds with improvement"
    )
    lines.append("# TYPE uamm_planning_improvements_total counter")
    lines.append(
        f"uamm_planning_improvements_total {int(planning.get('improvements', 0) or 0)}"
    )
    # Faithfulness (global)
    faith = m.get("faithfulness", {}) or {}
    f_count = int(faith.get("count", 0) or 0)
    f_sum = float(faith.get("sum", 0.0) or 0.0)
    f_claims = int(faith.get("claim_count", 0) or 0)
    f_unsupported = int(faith.get("unsupported_total", 0) or 0)
    lines.append(
        "# HELP uamm_faithfulness_score Average claim faithfulness score (0..1)"
    )
    lines.append("# TYPE uamm_faithfulness_score gauge")
    avg_f = (f_sum / f_count) if f_count > 0 else float("nan")
    lines.append(f"uamm_faithfulness_score {_prom_number(avg_f)}")
    lines.append("# HELP uamm_claims_total Total extracted claims")
    lines.append("# TYPE uamm_claims_total counter")
    lines.append(f"uamm_claims_total {f_claims}")
    lines.append("# HELP uamm_claims_unsupported_total Total unsupported claims")
    lines.append("# TYPE uamm_claims_unsupported_total counter")
    lines.append(f"uamm_claims_unsupported_total {f_unsupported}")
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
    # Guardrails by domain
    guard = m.get("guardrails", {}) or {}
    gb = guard.get("by_domain", {}) or {}
    if gb:
        lines.append(
            "# HELP uamm_guardrails_violations_pre_by_domain_total Pre-guard violations by domain"
        )
        lines.append("# TYPE uamm_guardrails_violations_pre_by_domain_total counter")
        for dom, stats in gb.items():
            lines.append(
                f'uamm_guardrails_violations_pre_by_domain_total{{domain="{dom}"}} {int((stats or {}).get("pre", 0) or 0)}'
            )
        lines.append(
            "# HELP uamm_guardrails_violations_post_by_domain_total Post-guard violations by domain"
        )
        lines.append("# TYPE uamm_guardrails_violations_post_by_domain_total counter")
        for dom, stats in gb.items():
            lines.append(
                f'uamm_guardrails_violations_post_by_domain_total{{domain="{dom}"}} {int((stats or {}).get("post", 0) or 0)}'
            )
    # Faithfulness by domain
    fbd = m.get("faithfulness_by_domain", {}) or {}
    if fbd:
        lines.append(
            "# HELP uamm_faithfulness_score_by_domain Average claim faithfulness score by domain"
        )
        lines.append("# TYPE uamm_faithfulness_score_by_domain gauge")
        for dom, stats in fbd.items():
            c = int((stats or {}).get("count", 0) or 0)
            s = float((stats or {}).get("sum", 0.0) or 0.0)
            avg = (s / c) if c > 0 else float("nan")
            lines.append(
                f'uamm_faithfulness_score_by_domain{{domain="{dom}"}} {_prom_number(avg)}'
            )
        lines.append(
            "# HELP uamm_claims_by_domain_total Total extracted claims by domain"
        )
        lines.append("# TYPE uamm_claims_by_domain_total counter")
        for dom, stats in fbd.items():
            cc = int((stats or {}).get("claim_count", 0) or 0)
            lines.append(f'uamm_claims_by_domain_total{{domain="{dom}"}} {cc}')
        lines.append(
            "# HELP uamm_claims_unsupported_by_domain_total Total unsupported claims by domain"
        )
        lines.append("# TYPE uamm_claims_unsupported_by_domain_total counter")
        for dom, stats in fbd.items():
            uu = int((stats or {}).get("unsupported_total", 0) or 0)
            lines.append(
                f'uamm_claims_unsupported_by_domain_total{{domain="{dom}"}} {uu}'
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
    # SQL checks by domain
    sqlm = m.get("sql_checks", {}) or {}
    sqlbd = sqlm.get("by_domain", {}) or {}
    if sqlbd:
        lines.append("# HELP uamm_sql_checks_by_domain_total SQL checks runs by domain")
        lines.append("# TYPE uamm_sql_checks_by_domain_total counter")
        for dom, st in sqlbd.items():
            lines.append(
                f'uamm_sql_checks_by_domain_total{{domain="{dom}"}} {int((st or {}).get("runs", 0) or 0)}'
            )
        lines.append(
            "# HELP uamm_sql_checks_fail_by_domain_total SQL checks failures by domain"
        )
        lines.append("# TYPE uamm_sql_checks_fail_by_domain_total counter")
        for dom, st in sqlbd.items():
            lines.append(
                f'uamm_sql_checks_fail_by_domain_total{{domain="{dom}"}} {int((st or {}).get("fail", 0) or 0)}'
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
    assertions: list[Dict[str, Any]] = Field(default_factory=list)


@router.post("/gov/check")
def gov_check(req: GoVCheckRequest, request: Request):
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
    assertions_in = _load_assertions(req.assertions)
    assertions_out, gov_metrics = _eval_assertions(
        dag=req.dag,
        verified_pcn=req.verified_pcn,
        assertions=assertions_in,
        dag_ok=ok,
        dag_failures=failures,
        validate_dag_fn=validate_dag,
    )

    # Update global metrics for assertions
    try:
        metrics = request.app.state.metrics
        govm = metrics.setdefault(
            "gov_assertions", {"runs": 0, "fail": 0, "by_pred": {}}
        )
        total_fails = sum(1 for a in assertions_out if not a.get("passed"))
        govm["runs"] = int(govm.get("runs", 0)) + 1
        if total_fails:
            govm["fail"] = int(govm.get("fail", 0)) + 1
        by = govm.setdefault("by_pred", {})
        for a in assertions_out:
            key = str(a.get("predicate", ""))
            pred_m = by.setdefault(key, {"runs": 0, "fail": 0})
            pred_m["runs"] = int(pred_m.get("runs", 0)) + 1
            if not a.get("passed"):
                pred_m["fail"] = int(pred_m.get("fail", 0)) + 1
    except Exception:
        pass
    return {
        "ok": ok,
        "failures": failures,
        "validation_ok": True,
        "assertions": assertions_out,
    }


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
    # Force env override for DB path when present (tests/dev)
    try:
        env_db = os.getenv("UAMM_DB_PATH")
        if env_db:
            setattr(settings, "db_path", env_db)
    except Exception:
        pass
    import sqlite3 as _sqlite3  # ensure name for overlay

    # Apply workspace policy overlay for table guard resolution
    try:
        ws = _resolve_workspace(request)
        con = _sqlite3.connect(settings.db_path)
        con.row_factory = _sqlite3.Row
        row = con.execute(
            "SELECT json FROM workspace_policies WHERE workspace = ?",
            (ws,),
        ).fetchone()
        con.close()
        pack = _parse_policy_blob(row["json"]) if row else {}
        overlay_allowed = pack.get("table_allowed") if isinstance(pack, dict) else None
        overlay_policies = (
            pack.get("table_policies") if isinstance(pack, dict) else None
        )
        overlay_allowed_by_domain = (
            pack.get("table_allowed_by_domain") if isinstance(pack, dict) else None
        )
        if isinstance(pack, dict) and "tools_allowed" in pack:
            setattr(request.state, "tools_allowed", list(pack["tools_allowed"]))
    except Exception:
        overlay_allowed = overlay_policies = overlay_allowed_by_domain = None
    # Tool allowlist enforcement: require TABLE_QUERY when allowlist present
    allowed_tools = getattr(request.state, "tools_allowed", None)
    if allowed_tools is not None and "TABLE_QUERY" not in set(allowed_tools):
        return JSONResponse(
            status_code=403,
            content={
                "code": "tool_forbidden",
                "message": "TABLE_QUERY not allowed for this workspace",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )
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
    allowed_tables = (
        overlay_allowed if overlay_allowed is not None else settings.table_allowed
    )
    if req.domain:
        by_dom = (
            overlay_allowed_by_domain
            if overlay_allowed_by_domain is not None
            else getattr(settings, "table_allowed_by_domain", None)
        )
        if by_dom:
            allowed_tables = by_dom.get(req.domain, allowed_tables)
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
    pol = (
        overlay_policies if overlay_policies is not None else settings.table_policies
    ) or {}
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

    eff_db = getattr(request.state, "db_path", None) or settings.db_path
    rows = db_table_query(
        eff_db,
        sql,
        req.params,
        max_rows=max_rows_eff,
        time_limit_ms=time_limit_ms_eff,
    )
    # map to dicts using cursor description
    import sqlite3

    con = sqlite3.connect(eff_db)
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
    # SQL property checks from table_policies checks merged across referenced tables
    checks_combined: Dict[str, Dict[str, Any]] = {}
    try:
        for t in tables:
            tp = pol.get(t, {})
            ch = tp.get("checks") if isinstance(tp, dict) else None
            if isinstance(ch, dict):
                for col, spec in ch.items():
                    if isinstance(spec, dict):
                        checks_combined[col] = dict(spec)
        violations = evaluate_checks(out, checks_combined) if checks_combined else []
    except Exception:
        violations = []
    # Update metrics for checks
    if checks_combined:
        m = request.app.state.metrics
        sqlm = m.setdefault("sql_checks", {"runs": 0, "fail": 0, "by_domain": {}})
        sqlm["runs"] = int(sqlm.get("runs", 0)) + 1
        if violations:
            sqlm["fail"] = int(sqlm.get("fail", 0)) + 1
        dom_map = sqlm.setdefault("by_domain", {})
        dom = req.domain or getattr(request.state, "workspace", "default") or "default"
        dom_entry = dom_map.setdefault(dom, {"runs": 0, "fail": 0})
        dom_entry["runs"] = int(dom_entry.get("runs", 0)) + 1
        if violations:
            dom_entry["fail"] = int(dom_entry.get("fail", 0)) + 1
    return {
        "rows": out,
        "checks": {
            "applied": checks_combined,
            "violations": violations,
            "ok": not bool(violations),
        },
    }


# Duplicate CP endpoints removed; see canonical definitions earlier in file
def _require_role(request: Request, allowed: set[str]) -> None:
    settings = request.app.state.settings
    # Only enforce when auth is required; endpoints can still call this opt-in
    import os

    required = (
        bool(getattr(settings, "auth_required", False))
        or os.getenv("UAMM_AUTH_REQUIRED", "0") == "1"
    )
    if not required:
        return
    # Ensure we have a resolved role; resolve inline if middleware didn't run
    role = getattr(request.state, "role", None)
    if role is None:
        from uamm.security.auth import lookup_key, parse_bearer

        key = request.headers.get(getattr(settings, "api_key_header", "X-API-Key"))
        if not key:
            key = parse_bearer(request.headers.get("Authorization"))
        if not key:
            raise HTTPException(status_code=401, detail="missing_api_key")
        rec = lookup_key(settings.db_path, key)
        if not rec or not rec.active:
            raise HTTPException(status_code=401, detail="invalid_api_key")
        request.state.role = rec.role
        request.state.workspace = rec.workspace
        request.state.user = f"key:{rec.label}" if rec.label else "key:unknown"
        role = rec.role
    if role not in allowed:
        raise HTTPException(status_code=403, detail="forbidden")


def _check_rate_limit(request: Request) -> None:
    settings = request.app.state.settings
    if not getattr(settings, "rate_limit_enabled", False):
        return
    ws = _resolve_workspace(request)
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = {}
        setattr(request.app.state, "rate_limiter", limiter)
    import time as _t

    role = getattr(request.state, "role", None)
    per_min = None
    if role == "viewer":
        per_min = getattr(settings, "rate_limit_viewer_per_minute", None)
    elif role == "editor":
        per_min = getattr(settings, "rate_limit_editor_per_minute", None)
    elif role == "admin":
        per_min = getattr(settings, "rate_limit_admin_per_minute", None)
    if not per_min:
        per_min = getattr(settings, "rate_limit_per_minute", 120)
    per_min = max(1, int(per_min))
    now = int(_t.time())
    window = now // 60
    state = limiter.get(ws)
    if not state or state.get("window") != window:
        state = {"window": window, "counts": {}}
        limiter[ws] = state
    counts = state["counts"]
    role_key = role or "anonymous"
    counts[role_key] = int(counts.get(role_key, 0)) + 1
    if counts[role_key] > per_min:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


def _resolve_workspace(request: Request) -> str:
    ws = getattr(request.state, "workspace", None)
    if ws:
        return ws
    from uamm.security.auth import lookup_key, parse_bearer

    settings = request.app.state.settings
    key = request.headers.get(getattr(settings, "api_key_header", "X-API-Key"))
    if not key:
        key = parse_bearer(request.headers.get("Authorization"))
    if key:
        rec = lookup_key(settings.db_path, key)
        if rec and rec.active:
            request.state.workspace = rec.workspace
            request.state.role = rec.role
            request.state.user = f"key:{rec.label}" if rec.label else "key:unknown"
            return rec.workspace
    ws = request.headers.get("X-Workspace", "default").strip() or "default"
    request.state.workspace = ws
    return ws


# Settings management (ops)
@router.get("/settings")
def settings_get(request: Request):
    settings = request.app.state.settings
    snap = {
        "env": getattr(settings, "env", None),
        "accept_threshold": getattr(settings, "accept_threshold", None),
        "borderline_delta": getattr(settings, "borderline_delta", None),
        "snne_samples": getattr(settings, "snne_samples", None),
        "snne_tau": getattr(settings, "snne_tau", None),
        "max_refinement_steps": getattr(settings, "max_refinement_steps", None),
        "cp_target_mis": getattr(settings, "cp_target_mis", None),
        "rate_limit_enabled": getattr(settings, "rate_limit_enabled", False),
        "rate_limit_per_minute": getattr(settings, "rate_limit_per_minute", 120),
        "docs_chunk_mode": getattr(settings, "docs_chunk_mode", "chars"),
        "docs_chunk_chars": getattr(settings, "docs_chunk_chars", 1400),
        "docs_overlap_chars": getattr(settings, "docs_overlap_chars", 200),
        "docs_chunk_tokens": getattr(settings, "docs_chunk_tokens", 600),
        "docs_overlap_tokens": getattr(settings, "docs_overlap_tokens", 100),
        "docs_ocr_enabled": getattr(settings, "docs_ocr_enabled", True),
        "vector_backend": getattr(settings, "vector_backend", "none"),
        "lancedb_uri": getattr(settings, "lancedb_uri", None),
        "lancedb_table": getattr(settings, "lancedb_table", None),
        "lancedb_metric": getattr(settings, "lancedb_metric", None),
        "lancedb_k": getattr(settings, "lancedb_k", None),
        "egress_block_private_ip": getattr(settings, "egress_block_private_ip", None),
        "egress_enforce_tls": getattr(settings, "egress_enforce_tls", None),
        "egress_allow_redirects": getattr(settings, "egress_allow_redirects", None),
        "egress_max_payload_bytes": getattr(settings, "egress_max_payload_bytes", None),
        "egress_allowlist_hosts": getattr(settings, "egress_allowlist_hosts", []),
        "egress_denylist_hosts": getattr(settings, "egress_denylist_hosts", []),
        "tools_requiring_approval": getattr(settings, "tools_requiring_approval", []),
    }
    return {"settings": snap}


class SettingsPatchRequest(BaseModel):
    changes: Dict[str, Any]


@router.patch("/settings")
def settings_patch(req: SettingsPatchRequest, request: Request):
    settings = request.app.state.settings
    allowed = {
        "accept_threshold",
        "borderline_delta",
        "snne_samples",
        "snne_tau",
        "max_refinement_steps",
        "cp_target_mis",
        "rate_limit_enabled",
        "rate_limit_per_minute",
        "docs_chunk_mode",
        "docs_chunk_chars",
        "docs_overlap_chars",
        "docs_chunk_tokens",
        "docs_overlap_tokens",
        "docs_ocr_enabled",
        "vector_backend",
        "lancedb_uri",
        "lancedb_table",
        "lancedb_metric",
        "lancedb_k",
        "egress_block_private_ip",
        "egress_enforce_tls",
        "egress_allow_redirects",
        "egress_max_payload_bytes",
        "egress_allowlist_hosts",
        "egress_denylist_hosts",
        "tools_requiring_approval",
    }
    applied: Dict[str, Any] = {}
    for k, v in (req.changes or {}).items():
        if k not in allowed:
            continue
        # Coerce types for known scalars
        if k in {"accept_threshold", "borderline_delta", "snne_tau", "cp_target_mis"}:
            v = float(v)
        if k in {
            "snne_samples",
            "max_refinement_steps",
            "rate_limit_per_minute",
            "lancedb_k",
            "egress_allow_redirects",
            "egress_max_payload_bytes",
        }:
            v = int(v)
        if k in {
            "rate_limit_enabled",
            "docs_ocr_enabled",
            "egress_block_private_ip",
            "egress_enforce_tls",
        }:
            v = bool(v)
        setattr(settings, k, v)
        applied[k] = getattr(settings, k)
    return {"applied": applied}


# Policy packs (workspace-scoped)
@router.get("/policies")
def policies_list():
    return {"policies": list_policies()}


@router.get("/policies/{name}")
def policies_get(name: str):
    pack = load_policy(name)
    if not pack:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return {"name": name, "policy": pack}


@router.get("/policies/export")
def policies_export(request: Request):
    # Export current policy files as a zip
    from io import BytesIO
    import zipfile

    _require_role(request, {"admin"})
    buf = BytesIO()
    base = Path(os.getenv("UAMM_POLICIES_DIR", "config/policies")).resolve()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        if base.exists():
            for p in base.glob("*.yaml"):
                z.writestr(p.name, p.read_text(encoding="utf-8"))
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=policies.zip"}
    return Response(content=buf.read(), media_type="application/zip", headers=headers)


@router.post("/policies/import")
async def policies_import(request: Request, file: UploadFile = File(...)):
    # Import a zip of YAML policy files into the policies directory. Admin only.
    import zipfile
    from io import BytesIO

    _require_role(request, {"admin"})
    data = await file.read()
    if not data:
        return JSONResponse(status_code=400, content={"error": "missing_file"})
    try:
        z = zipfile.ZipFile(BytesIO(data))
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_zip"})
    base = Path(os.getenv("UAMM_POLICIES_DIR", "config/policies")).resolve()
    base.mkdir(parents=True, exist_ok=True)
    imported = 0
    for name in z.namelist():
        if not name.lower().endswith(".yaml"):
            continue
        # Prevent path traversal
        safe = Path(name).name
        content = z.read(name)
        try:
            text = content.decode("utf-8")
        except Exception:
            continue
        (base / safe).write_text(text, encoding="utf-8")
        imported += 1
    return {"ok": True, "imported": imported}


@router.get("/workspaces/{slug}/policies/preview/{name}")
def policies_preview(slug: str, name: str, request: Request):
    """Preview differences between current applied policy and a named pack."""
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    # Env override for DB path in tests/dev
    env_db = os.getenv("UAMM_DB_PATH")
    if env_db:
        setattr(settings, "db_path", env_db)
    new_pack = load_policy(name)
    if not new_pack:
        return JSONResponse(status_code=404, content={"error": "unknown_policy"})
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT json FROM workspace_policies WHERE workspace = ?",
        (slug,),
    ).fetchone()
    con.close()
    old_pack = _parse_policy_blob(row["json"]) if (row and row["json"]) else {}
    keys = set(list(old_pack.keys()) + list(new_pack.keys()))
    diff = {}
    for k in sorted(keys):
        ov = old_pack.get(k)
        nv = new_pack.get(k)
        if ov != nv:
            diff[k] = {"old": ov, "new": nv}
    return {"workspace": slug, "policy": name, "diff": diff}


class ApplyPolicyRequest(BaseModel):
    name: str


@router.post("/workspaces/{slug}/policies/apply")
def policies_apply(slug: str, req: ApplyPolicyRequest, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    env_db = os.getenv("UAMM_DB_PATH")
    if env_db:
        setattr(settings, "db_path", env_db)
    pack = load_policy(req.name)
    if not pack:
        return JSONResponse(status_code=404, content={"error": "unknown_policy"})
    con = sqlite3.connect(settings.db_path)
    try:
        import time as _t

        import json as _json

        con.execute(
            "INSERT OR REPLACE INTO workspace_policies(workspace, policy_name, json, updated) VALUES (?, ?, ?, ?)",
            (slug, req.name, _json.dumps(pack), _t.time()),
        )
        con.commit()
    finally:
        con.close()
    return {"workspace": slug, "applied": req.name}


@router.get("/workspaces/{slug}/policies")
def policies_current(slug: str, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    env_db = os.getenv("UAMM_DB_PATH")
    if env_db:
        setattr(settings, "db_path", env_db)
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT workspace, policy_name, json, updated FROM workspace_policies WHERE workspace = ?",
            (slug,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {"workspace": slug, "policy": None}
    return {
        "workspace": row["workspace"],
        "name": row["policy_name"],
        "policy": row["json"],
        "updated": float(row["updated"]) if row["updated"] is not None else None,
    }


class OverlayPolicyRequest(BaseModel):
    overlay: Dict[str, Any]


@router.post("/workspaces/{slug}/policies/overlay")
def policies_overlay(slug: str, req: OverlayPolicyRequest, request: Request):
    """Set a workspace-specific policy overlay (ad-hoc JSON).

    Stores the overlay under policy_name='overlay' and applies as an override at runtime.
    Admin role required when auth is enabled.
    """
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    # Env override for DB path in tests/dev
    env_db = os.getenv("UAMM_DB_PATH")
    if env_db:
        setattr(settings, "db_path", env_db)
    import time as _t

    con = sqlite3.connect(settings.db_path)
    try:
        import json as _json

        con.execute(
            "INSERT OR REPLACE INTO workspace_policies(workspace, policy_name, json, updated) VALUES (?, ?, ?, ?)",
            (slug, "overlay", _json.dumps(dict(req.overlay or {})), _t.time()),
        )
        con.commit()
    finally:
        con.close()
    return {"workspace": slug, "applied": "overlay", "overlay": dict(req.overlay or {})}


@router.get("/config/export")
def config_export(request: Request):
    settings = request.app.state.settings
    # Global settings snapshot reusing /settings fields
    snap = settings_get(request)["settings"]
    # Export applied workspace policies
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT workspace, policy_name, json, updated FROM workspace_policies"
        ).fetchall()
    finally:
        con.close()
    ws_policies = [
        {
            "workspace": r["workspace"],
            "policy_name": r["policy_name"],
            "policy": r["json"],
            "updated": float(r["updated"]) if r["updated"] is not None else None,
        }
        for r in rows
    ]
    return {"settings": snap, "workspace_policies": ws_policies}


@router.get("/config/bundle")
def config_bundle(
    request: Request, include_db: bool = False, workspaces: str | None = None
):
    """Export a full environment bundle (zip) with settings.json, workspace_policies.json, and optional SQLite DB.

    Use include_db=true cautiously; it contains all workspace data.
    """
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        # settings.json
        import json as _json

        z.writestr(
            "settings.json", _json.dumps(settings_get(request)["settings"], indent=2)
        )
        # workspace_policies.json
        con = sqlite3.connect(settings.db_path)
        con.row_factory = sqlite3.Row
        if workspaces:
            ws_list = [w.strip() for w in workspaces.split(",") if w.strip()]
            qmarks = ",".join(["?"] * len(ws_list)) or "?"
            rows = con.execute(
                f"SELECT workspace, policy_name, json, updated FROM workspace_policies WHERE workspace IN ({qmarks})",
                ws_list if ws_list else [""],
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT workspace, policy_name, json, updated FROM workspace_policies"
            ).fetchall()
        con.close()
        policies = [
            {
                "workspace": r["workspace"],
                "policy_name": r["policy_name"],
                "policy": r["json"],
                "updated": float(r["updated"]) if r["updated"] is not None else None,
            }
            for r in rows
        ]
        z.writestr("workspace_policies.json", _json.dumps(policies, indent=2))
        if include_db and os.path.exists(settings.db_path):
            with open(settings.db_path, "rb") as f:
                z.writestr(Path(settings.db_path).name, f.read())
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=env_bundle.zip"}
    return Response(content=buf.read(), media_type="application/zip", headers=headers)


@router.get("/workspaces/{slug}/export")
def workspace_export(
    slug: str,
    request: Request,
    since_ts: float | None = None,
    until_ts: float | None = None,
):
    """Export a workspace bundle (zip) with memory/corpus/steps JSON and applied policy.

    Intended for migrating content between environments.
    """
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        import json as _json

        con = sqlite3.connect(settings.db_path)
        con.row_factory = sqlite3.Row
        # Pull rows
        if since_ts or until_ts:
            mem = con.execute(
                "SELECT id, ts, key, text, domain, recency, tokens, embedding_model FROM memory WHERE workspace = ? AND ts BETWEEN ? AND ?",
                (slug, since_ts or 0.0, until_ts or 1e12),
            ).fetchall()
            cor = con.execute(
                "SELECT id, ts, title, url, text, meta FROM corpus WHERE workspace = ? AND ts BETWEEN ? AND ?",
                (slug, since_ts or 0.0, until_ts or 1e12),
            ).fetchall()
            steps = con.execute(
                "SELECT id, ts, step, question, answer, domain, s1, s2, final_score, cp_accept, action, reason, is_refinement, status, latency_ms, usage, pack_ids, issues, tools_used, change_summary, trace_json FROM steps WHERE workspace = ? AND ts BETWEEN ? AND ?",
                (slug, since_ts or 0.0, until_ts or 1e12),
            ).fetchall()
        else:
            mem = con.execute(
                "SELECT id, ts, key, text, domain, recency, tokens, embedding_model FROM memory WHERE workspace = ?",
                (slug,),
            ).fetchall()
            cor = con.execute(
                "SELECT id, ts, title, url, text, meta FROM corpus WHERE workspace = ?",
                (slug,),
            ).fetchall()
            steps = con.execute(
                "SELECT id, ts, step, question, answer, domain, s1, s2, final_score, cp_accept, action, reason, is_refinement, status, latency_ms, usage, pack_ids, issues, tools_used, change_summary, trace_json FROM steps WHERE workspace = ?",
                (slug,),
            ).fetchall()
        # Build json bytes and checksums
        files = {}
        mem_bytes = _json.dumps([dict(r) for r in mem], indent=2).encode("utf-8")
        z.writestr("memory.json", mem_bytes)
        files["memory.json"] = {
            "sha256": hashlib.sha256(mem_bytes).hexdigest(),
            "bytes": len(mem_bytes),
            "count": len(mem),
        }
        cor_bytes = _json.dumps([dict(r) for r in cor], indent=2).encode("utf-8")
        z.writestr("corpus.json", cor_bytes)
        files["corpus.json"] = {
            "sha256": hashlib.sha256(cor_bytes).hexdigest(),
            "bytes": len(cor_bytes),
            "count": len(cor),
        }
        steps_bytes = _json.dumps([dict(r) for r in steps], indent=2).encode("utf-8")
        z.writestr("steps.json", steps_bytes)
        files["steps.json"] = {
            "sha256": hashlib.sha256(steps_bytes).hexdigest(),
            "bytes": len(steps_bytes),
            "count": len(steps),
        }
        pol = con.execute(
            "SELECT policy_name, json, updated FROM workspace_policies WHERE workspace = ?",
            (slug,),
        ).fetchone()
        con.close()
        if pol:
            pol_bytes = _json.dumps(
                {
                    "name": pol["policy_name"],
                    "policy": pol["json"],
                    "updated": pol["updated"],
                },
                indent=2,
            ).encode("utf-8")
            z.writestr("policy.json", pol_bytes)
            files["policy.json"] = {
                "sha256": hashlib.sha256(pol_bytes).hexdigest(),
                "bytes": len(pol_bytes),
                "count": 1,
            }
        # Write manifest with optional HMAC
        import time as _t

        manifest = {
            "schema_version": "1.0",
            "type": "workspace_bundle",
            "workspace": slug,
            "created_at": _t.time(),
            "files": files,
        }
        key = os.getenv("UAMM_BACKUP_SIGN_KEY")
        if key:
            mcopy = dict(manifest)
            mcopy["hmac"] = None
            serialized = _json.dumps(
                mcopy, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            sig = hmac.new(key.encode("utf-8"), serialized, hashlib.sha256).hexdigest()
            manifest["hmac"] = sig
        z.writestr("manifest.json", _json.dumps(manifest, indent=2))
    buf.seek(0)
    headers = {"Content-Disposition": f"attachment; filename=workspace_{slug}.zip"}
    return Response(content=buf.read(), media_type="application/zip", headers=headers)


@router.post("/workspaces/{slug}/import")
async def workspace_import(
    slug: str, request: Request, file: UploadFile = File(...), replace: bool = False
):
    """Import a workspace bundle (zip) with memory/corpus/steps/policy. Admin only.

    Records are merged; duplicate IDs are ignored.
    """
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    data = await file.read()
    if not data:
        return JSONResponse(status_code=400, content={"error": "missing_file"})
    try:
        z = zipfile.ZipFile(BytesIO(data))
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_zip"})
    import json as _json

    con = sqlite3.connect(settings.db_path)
    try:
        # Verify manifest if present
        try:
            manifest = _json.loads(z.read("manifest.json").decode("utf-8"))
            files = manifest.get("files", {})
            # Verify hmac if key provided
            key = os.getenv("UAMM_BACKUP_SIGN_KEY")
            if key and "hmac" in manifest:
                mcopy = dict(manifest)
                sig = mcopy.pop("hmac", None)
                serialized = _json.dumps(
                    mcopy, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                vsig = hmac.new(
                    key.encode("utf-8"), serialized, hashlib.sha256
                ).hexdigest()
                if sig != vsig:
                    return JSONResponse(
                        status_code=400, content={"error": "invalid_signature"}
                    )
            # Verify checksums
            for name in ["memory.json", "corpus.json", "steps.json", "policy.json"]:
                if name in z.namelist() and name in files:
                    data_bytes = z.read(name)
                    csum = hashlib.sha256(data_bytes).hexdigest()
                    if csum != files[name].get("sha256"):
                        return JSONResponse(
                            status_code=400,
                            content={"error": f"checksum_mismatch:{name}"},
                        )
        except KeyError:
            pass
        except Exception:
            # ignore malformed manifest; import proceeds without verification
            pass
        if replace:
            # delete existing rows for workspace (policy retained until new policy applied)
            con.execute("DELETE FROM memory WHERE workspace = ?", (slug,))
            con.execute("DELETE FROM corpus WHERE workspace = ?", (slug,))
            con.execute("DELETE FROM steps WHERE workspace = ?", (slug,))
            con.execute("DELETE FROM workspace_policies WHERE workspace = ?", (slug,))
        # memory
        try:
            mem = _json.loads(z.read("memory.json").decode("utf-8"))
            for r in mem:
                con.execute(
                    "INSERT OR IGNORE INTO memory(id, ts, key, text, embedding, domain, recency, tokens, embedding_model, workspace, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.get("id"),
                        r.get("ts"),
                        r.get("key"),
                        r.get("text"),
                        None,
                        r.get("domain"),
                        r.get("recency"),
                        r.get("tokens"),
                        r.get("embedding_model"),
                        slug,
                        r.get("created_by", "import"),
                    ),
                )
        except Exception:
            pass
        # corpus
        try:
            cor = _json.loads(z.read("corpus.json").decode("utf-8"))
            for r in cor:
                con.execute(
                    "INSERT OR IGNORE INTO corpus(id, ts, title, url, text, meta, workspace, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.get("id"),
                        r.get("ts"),
                        r.get("title"),
                        r.get("url"),
                        r.get("text"),
                        r.get("meta"),
                        slug,
                        r.get("created_by", "import"),
                    ),
                )
        except Exception:
            pass
        # steps (optional)
        try:
            steps = _json.loads(z.read("steps.json").decode("utf-8"))
            for r in steps:
                con.execute(
                    "INSERT OR IGNORE INTO steps(id, ts, step, question, answer, domain, workspace, s1, s2, final_score, cp_accept, action, reason, is_refinement, status, latency_ms, usage, pack_ids, issues, tools_used, change_summary, trace_json, eval_id, dataset_case_id, is_gold, gold_correct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.get("id"),
                        r.get("ts"),
                        r.get("step", 0),
                        r.get("question"),
                        r.get("answer"),
                        r.get("domain"),
                        slug,
                        r.get("s1"),
                        r.get("s2"),
                        r.get("final_score"),
                        1 if r.get("cp_accept") else 0,
                        r.get("action"),
                        r.get("reason"),
                        1 if r.get("is_refinement") else 0,
                        r.get("status"),
                        r.get("latency_ms", 0),
                        str(r.get("usage")),
                        str(r.get("pack_ids")),
                        str(r.get("issues")),
                        str(r.get("tools_used")),
                        r.get("change_summary"),
                        r.get("trace_json"),
                        r.get("eval_id"),
                        r.get("dataset_case_id"),
                        r.get("is_gold"),
                        r.get("gold_correct"),
                    ),
                )
        except Exception:
            pass
        # policy
        try:
            pol = _json.loads(z.read("policy.json").decode("utf-8"))
            con.execute(
                "INSERT OR REPLACE INTO workspace_policies(workspace, policy_name, json, updated) VALUES (?, ?, ?, ?)",
                (
                    slug,
                    pol.get("name", "bundle"),
                    pol.get("policy"),
                    pol.get("updated"),
                ),
            )
        except Exception:
            pass
        con.commit()
    finally:
        con.close()
    return {"ok": True}


@router.post("/workspaces/{slug}/vector/reindex")
def vector_reindex(
    slug: str,
    request: Request,
    since_ts: float | None = None,
    until_ts: float | None = None,
    limit: int | None = None,
):
    """Rebuild vector embeddings for corpus documents in a workspace (admin only).

    Only effective when vector_backend=lancedb. Returns counts of attempts and successes.
    """
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    backend = str(getattr(settings, "vector_backend", "none") or "none").lower()
    if backend != "lancedb":
        return {"workspace": slug, "ok": False, "reason": "vector_backend_not_lancedb"}
    import sqlite3 as _sqlite3

    con = _sqlite3.connect(settings.db_path)
    con.row_factory = _sqlite3.Row
    try:
        args = [slug]
        where = "WHERE workspace = ?"
        if since_ts or until_ts:
            where += " AND ts BETWEEN ? AND ?"
            args.extend([since_ts or 0.0, until_ts or 1e12])
        sql = f"SELECT id, text, title, url FROM corpus {where} ORDER BY ts DESC"
        if limit and limit > 0:
            sql += " LIMIT ?"
            args.append(int(limit))
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    attempted = 0
    upserted = 0
    errors = 0
    for r in rows:
        attempted += 1
        text = r["text"] or ""
        meta = {"title": r["title"] or ""}
        if r["url"]:
            meta["url"] = r["url"]
        try:
            upsert_document_embedding(settings, r["id"], text, meta=meta)
            upserted += 1
        except LanceDBUnavailable:
            return {
                "workspace": slug,
                "ok": False,
                "reason": "lancedb_unavailable",
                "attempted": attempted,
                "upserted": upserted,
                "errors": errors,
            }
        except Exception:
            errors += 1
    return {
        "workspace": slug,
        "ok": True,
        "attempted": attempted,
        "upserted": upserted,
        "errors": errors,
    }


class ConfigImportRequest(BaseModel):
    settings: Dict[str, Any] | None = None
    workspace_policies: List[Dict[str, Any]] | None = None


@router.post("/config/import")
def config_import(req: ConfigImportRequest, request: Request):
    _require_role(request, {"admin"})
    settings = request.app.state.settings
    # Apply settings (same allowed keys as /settings PATCH)
    if req.settings:
        patch_req = SettingsPatchRequest(changes=req.settings)
        settings_patch(patch_req, request)
    # Apply workspace policies
    applied = []
    if req.workspace_policies:
        con = sqlite3.connect(settings.db_path)
        try:
            import time as _t

            for item in req.workspace_policies:
                ws = str(item.get("workspace", "")).strip()
                if not ws:
                    continue
                name = item.get("policy_name")
                policy_json = None
                if name:
                    pack = load_policy(str(name))
                    if not pack:
                        continue
                    import json as _json

                    policy_json = _json.dumps(pack)
                    pname = str(name)
                else:
                    # Accept inline policy dict
                    inline = item.get("policy")
                    blob = _parse_policy_blob(inline)
                    if not isinstance(blob, dict):
                        continue
                    import json as _json

                    policy_json = _json.dumps(blob)
                    pname = "inline"
                con.execute(
                    "INSERT OR REPLACE INTO workspace_policies(workspace, policy_name, json, updated) VALUES (?, ?, ?, ?)",
                    (ws, pname, policy_json, _t.time()),
                )
                applied.append({"workspace": ws, "name": pname})
            con.commit()
        finally:
            con.close()
    return {"ok": True, "applied": applied}


@router.get("/config/export_yaml")
def config_export_yaml(request: Request):
    try:
        import yaml  # type: ignore
    except Exception:
        return JSONResponse(status_code=400, content={"error": "yaml_not_available"})
    data = config_export(request)
    text = yaml.safe_dump(data, sort_keys=False)
    headers = {"Content-Type": "application/x-yaml"}
    return Response(content=text, media_type="application/x-yaml", headers=headers)


@router.post("/config/import_yaml")
async def config_import_yaml(request: Request, file: UploadFile = File(...)):
    _require_role(request, {"admin"})
    try:
        import yaml  # type: ignore
    except Exception:
        return JSONResponse(status_code=400, content={"error": "yaml_not_available"})
    data = await file.read()
    try:
        payload = yaml.safe_load(data) or {}
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_yaml"})
    req = ConfigImportRequest(**payload)
    return config_import(req, request)


@router.get("/rate_limits")
def rate_limits_status(request: Request):
    settings = request.app.state.settings
    rl = getattr(request.app.state, "rate_limiter", {})
    out = {}
    for ws, state in rl.items():
        counts = state.get("counts", {})
        out[ws] = {"window": state.get("window"), "counts": counts}
    limits = {
        "enabled": getattr(settings, "rate_limit_enabled", False),
        "global_per_minute": getattr(settings, "rate_limit_per_minute", 120),
        "viewer_per_minute": getattr(settings, "rate_limit_viewer_per_minute", None),
        "editor_per_minute": getattr(settings, "rate_limit_editor_per_minute", None),
        "admin_per_minute": getattr(settings, "rate_limit_admin_per_minute", None),
    }
    return {"limits": limits, "windows": out}


@router.get("/favicon.ico")
def favicon_redirect():
    return RedirectResponse(url="/static/favicon.svg")
