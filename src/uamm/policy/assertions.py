from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def load_assertions(src: Any) -> List[Dict[str, Any]]:
    """Load assertions from a list/dict/path or JSON/YAML string.

    Accepts:
    - list[dict] (returned as-is)
    - dict with key 'assertions'
    - str path to JSON/YAML file, or JSON/YAML content
    - None → []
    """
    if src is None:
        return []
    if isinstance(src, list):
        return [dict(a) for a in src]
    if isinstance(src, dict):
        if "assertions" in src and isinstance(src["assertions"], list):
            return [dict(a) for a in src["assertions"]]
        return []
    if isinstance(src, str):
        p = Path(src)
        if p.exists() and p.is_file():
            data = p.read_text(encoding="utf-8")
            return load_assertions(data)
        # try json then yaml
        try:
            obj = json.loads(src)
            return load_assertions(obj)
        except Exception:
            pass
        if yaml is not None:
            try:
                obj = yaml.safe_load(src)
                return load_assertions(obj)
            except Exception:
                pass
    return []


def _adjacency(edges: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {}
    for e in edges:
        s = str(e.get("from"))
        t = str(e.get("to"))
        if s and t:
            adj.setdefault(s, []).append(t)
    return adj


def _max_depth(nodes: Dict[str, Dict[str, Any]], edges: List[Dict[str, Any]], dag_ok: bool) -> int:
    if not dag_ok:
        return -1
    adj = _adjacency(edges)
    memo: Dict[str, int] = {}

    def dfs(u: str) -> int:
        if u in memo:
            return memo[u]
        nxt = adj.get(u, [])
        if not nxt:
            memo[u] = 1
            return 1
        depth = 1 + max((dfs(v) for v in nxt), default=0)
        memo[u] = depth
        return depth

    best = 0
    for n in nodes.keys():
        d = dfs(n)
        if d > best:
            best = d
    return best


def _reachable(edges: List[Dict[str, Any]], src: str, dst: str) -> bool:
    adj = _adjacency(edges)
    seen = set()
    stack = [src]
    while stack:
        u = stack.pop()
        if u == dst:
            return True
        if u in seen:
            continue
        seen.add(u)
        for v in adj.get(u, []):
            if v not in seen:
                stack.append(v)
    return False


def evaluate_assertions(
    *,
    dag: Dict[str, Any],
    verified_pcn: List[str] | None,
    assertions: List[Dict[str, Any]],
    dag_ok: bool,
    dag_failures: List[str],
    validate_dag_fn,  # callable returning (valid, failures)
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, int]]]:
    nodes = {str(n.get("id")): n for n in dag.get("nodes", []) if n.get("id")}
    edges = list(dag.get("edges", []) or [])
    pred_metrics: Dict[str, Dict[str, int]] = {}
    out: List[Dict[str, Any]] = []
    valid, v_fails = validate_dag_fn(dag)
    for a in assertions or []:
        pred = str(a.get("predicate", ""))
        passed = True
        details: Dict[str, Any] = {}
        if pred == "no_cycles":
            passed = bool(dag_ok)
        elif pred == "no_pcn_failures":
            passed = not any(isinstance(f, str) and f.startswith("pcn_failure:") for f in dag_failures)
        elif pred == "no_dependency_failures":
            passed = not any(isinstance(f, str) and f.startswith("dependency_failure:") for f in dag_failures)
        elif pred == "all_claims_supported":
            passed = not any(isinstance(f, str) and f.startswith("unsupported_claim:") for f in (v_fails or []))
        elif pred == "max_depth":
            try:
                max_allowed = int(a.get("value"))
                depth = _max_depth(nodes, edges, dag_ok)
                details["depth"] = depth
                passed = (depth >= 0 and depth <= max_allowed)
            except Exception:
                passed = True
        elif pred == "path_exists":
            src = str(a.get("source", ""))
            dst = str(a.get("target", ""))
            passed = _reachable(edges, src, dst)
        elif pred == "types_allowed":
            allowed = set(str(t) for t in (a.get("types") or []))
            seen_types = set(str(n.get("type") or "") for n in nodes.values())
            disallowed = [t for t in seen_types if t and t not in allowed]
            details["disallowed"] = disallowed
            passed = len(disallowed) == 0
        out.append({"predicate": pred, "passed": bool(passed), **({"details": details} if details else {})})
        pred_m = pred_metrics.setdefault(pred, {"runs": 0, "fail": 0})
        pred_m["runs"] = int(pred_m.get("runs", 0)) + 1
        if not passed:
            pred_m["fail"] = int(pred_m.get("fail", 0)) + 1
    return out, pred_metrics


__all__ = ["load_assertions", "evaluate_assertions"]
