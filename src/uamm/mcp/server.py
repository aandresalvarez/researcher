"""
Minimal MCP server adapters for UAMM using Pydantic AI MCP when available.

This module exposes safe wrappers around UAMM tools and the main agent.
It is designed to run without Pydantic AI installed (adapters usable for tests),
and to activate a proper MCP server only when the `pydantic_ai` MCP runtime is
available in the environment.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from uuid import uuid4

from uamm.config.settings import load_settings
from uamm.tools.web_search import web_search as _web_search
from uamm.tools.web_fetch import web_fetch as _web_fetch
from uamm.tools.math_eval import math_eval as _math_eval
from uamm.tools.table_query import table_query as _table_query
from uamm.security.egress import EgressPolicy
from uamm.api.state import ApprovalsStore
from uamm.agents.main_agent import MainAgent
from uamm.policy.policy import PolicyConfig


_APPROVALS = ApprovalsStore(ttl_seconds=1800)
_MCP_METRICS: Dict[str, Any] = {"requests": 0, "errors": 0, "by_tool": {}}


def _mcp_count(tool: str, ok: bool) -> None:
    try:
        _MCP_METRICS["requests"] = int(_MCP_METRICS.get("requests", 0)) + 1
        if not ok:
            _MCP_METRICS["errors"] = int(_MCP_METRICS.get("errors", 0)) + 1
        by = _MCP_METRICS.setdefault("by_tool", {})
        by[tool] = int(by.get(tool, 0)) + 1
    except Exception:
        pass


def _maybe_require_approval(
    tool_name: str, meta: Dict[str, Any], settings
) -> Optional[Dict[str, Any]]:
    required = set(getattr(settings, "tools_requiring_approval", []) or [])
    if tool_name in required:
        appr_id = str(uuid4())
        try:
            _APPROVALS.create(appr_id, {"tool": tool_name, **meta})
        except Exception:
            return {"status": "error", "message": "approvals_unavailable"}
        return {"status": "waiting_approval", "approval_id": appr_id}
    return None


def tool_web_search(q: str, k: int = 3, *, settings=None) -> Dict[str, Any]:
    settings = settings or load_settings()
    res = _maybe_require_approval("WEB_SEARCH", {"q": q, "k": k}, settings)
    if res:
        _mcp_count("WEB_SEARCH", ok=True)
        return res
    try:
        results = _web_search(q, k=k)
        _mcp_count("WEB_SEARCH", ok=True)
    except Exception:
        _mcp_count("WEB_SEARCH", ok=False)
        raise
    return {"status": "ok", "results": results}


def tool_web_fetch(url: str, *, settings=None) -> Dict[str, Any]:
    settings = settings or load_settings()
    res = _maybe_require_approval("WEB_FETCH", {"url": url}, settings)
    if res:
        return res
    policy = EgressPolicy(
        block_private_ip=getattr(settings, "egress_block_private_ip", True),
        enforce_tls=getattr(settings, "egress_enforce_tls", True),
        allow_redirects=getattr(settings, "egress_allow_redirects", 3),
        max_payload_bytes=getattr(
            settings, "egress_max_payload_bytes", 5 * 1024 * 1024
        ),
        allowlist_hosts=getattr(settings, "egress_allowlist_hosts", []),
        denylist_hosts=getattr(settings, "egress_denylist_hosts", []),
    )
    try:
        out = _web_fetch(url, policy=policy)
        _mcp_count("WEB_FETCH", ok=True)
    except Exception:
        _mcp_count("WEB_FETCH", ok=False)
        raise
    return {"status": "ok", "result": out}


def tool_math_eval(expr: str, *, settings=None) -> Dict[str, Any]:
    settings = settings or load_settings()
    res = _maybe_require_approval("MATH_EVAL", {"expr": expr}, settings)
    if res:
        return res
    try:
        value = _math_eval(expr)
        _mcp_count("MATH_EVAL", ok=True)
    except Exception:
        _mcp_count("MATH_EVAL", ok=False)
        raise
    return {"status": "ok", "value": value}


def tool_table_query(
    db_path: str, sql: str, params: Optional[List[Any]] = None, *, settings=None
) -> Dict[str, Any]:
    settings = settings or load_settings()
    res = _maybe_require_approval("TABLE_QUERY", {"sql": sql}, settings)
    if res:
        return res
    max_rows = int(getattr(settings, "table_query_max_rows", 25) or 25)
    time_limit_ms = int(getattr(settings, "table_query_time_limit_ms", 250) or 250)
    try:
        rows = _table_query(
            db_path, sql, params or [], max_rows=max_rows, time_limit_ms=time_limit_ms
        )
        _mcp_count("TABLE_QUERY", ok=True)
    except Exception:
        _mcp_count("TABLE_QUERY", ok=False)
        raise
    return {"status": "ok", "rows": [tuple(r) for r in rows]}


def uamm_answer(question: str, *, settings=None, **kwargs: Any) -> Dict[str, Any]:
    """Simple MCP-callable answer function mirroring /agent/answer behavior (non-stream)."""
    settings = settings or load_settings()
    policy = PolicyConfig(
        tau_accept=getattr(settings, "accept_threshold", 0.85),
        delta=getattr(settings, "borderline_delta", 0.05),
    )
    agent = MainAgent(cp_enabled=getattr(settings, "cp_enabled", False), policy=policy)
    params = {
        "question": question,
        "max_refinements": getattr(settings, "max_refinement_steps", 1),
        "tool_budget_per_refinement": getattr(
            settings, "tool_budget_per_refinement", 2
        ),
        "tool_budget_per_turn": getattr(settings, "tool_budget_per_turn", 4),
        "snne_samples": getattr(settings, "snne_samples", 5),
        "snne_tau": getattr(settings, "snne_tau", 0.3),
        # planning
        "planning_enabled": getattr(settings, "planning_enabled", False),
        "planning_mode": getattr(settings, "planning_mode", "tot"),
        "planning_budget": getattr(settings, "planning_budget", 3),
        "planning_when": getattr(settings, "planning_when", "borderline"),
        # approvals/tool allowlist
        "tools_requiring_approval": getattr(settings, "tools_requiring_approval", []),
    }
    params.update(kwargs or {})
    try:
        result = agent.answer(params=params, emit=None)
        _mcp_count("UAMM_ANSWER", ok=True)
    except Exception:
        _mcp_count("UAMM_ANSWER", ok=False)
        raise
    return {"status": "ok", "result": result}


def get_tool_adapters(*, settings=None) -> Dict[str, Any]:
    """Return MCP-exposable adapters for tools and `uamm_answer`.

    This API is independent of the Pydantic AI MCP runtime and safe for tests.
    """
    return {
        "WEB_SEARCH": lambda q, k=3: tool_web_search(q, k=k, settings=settings),
        "WEB_FETCH": lambda url: tool_web_fetch(url, settings=settings),
        "MATH_EVAL": lambda expr: tool_math_eval(expr, settings=settings),
        "TABLE_QUERY": lambda db_path, sql, params=None: tool_table_query(
            db_path, sql, params or [], settings=settings
        ),
        "UAMM_ANSWER": lambda question, **kw: uamm_answer(
            question, settings=settings, **kw
        ),
    }


def mcp_metrics_snapshot() -> Dict[str, Any]:
    return {
        "requests": int(_MCP_METRICS.get("requests", 0) or 0),
        "errors": int(_MCP_METRICS.get("errors", 0) or 0),
        "by_tool": dict(_MCP_METRICS.get("by_tool", {}) or {}),
    }


def run(
    host: str | None = None, port: int | None = None
) -> None:  # pragma: no cover - runtime only
    """Launch a Pydantic AI MCP server if available.

    The implementation defers import to avoid hard dependency at import time.
    """
    try:
        # Illustrative API shape; actual types vary across versions.
        from pydantic_ai.mcp import Server  # type: ignore
    except Exception as exc:  # noqa: F841
        raise RuntimeError(
            "Pydantic AI MCP runtime not available. Install pydantic-ai with MCP support."
        )
    settings = load_settings()
    server = Server(name="uamm")  # type: ignore[call-arg]
    adapters = get_tool_adapters(settings=settings)
    for name, fn in adapters.items():
        # Server API, adapted to available versions; may be e.g., server.tool(name)(fn)
        try:
            server.register_tool(name, fn)  # type: ignore[attr-defined]
        except Exception:
            try:
                server.tool(name)(fn)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: F841
                raise RuntimeError(f"Failed to register tool '{name}' in MCP server")
    # Bind/run – API differs; attempt common patterns
    try:
        server.run(host=host or "127.0.0.1", port=port or 8765)  # type: ignore[attr-defined]
    except Exception:
        # Fallback to stdio if socket run unsupported
        server.run_stdio()  # type: ignore[attr-defined]


__all__ = [
    "get_tool_adapters",
    "run",
    "tool_web_search",
    "tool_web_fetch",
    "tool_math_eval",
    "tool_table_query",
    "uamm_answer",
    "mcp_metrics_snapshot",
]
