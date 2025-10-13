from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple

_ALLOWED_TYPES: Set[str] = {
    "premise",
    "claim",
    "calculation",
    "evidence",
    "observation",
}


def _detect_cycle(adj: Dict[str, List[str]]) -> List[str]:
    visiting: Set[str] = set()
    visited: Set[str] = set()
    failures: List[str] = []

    def dfs(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            failures.append(f"cycle:{node}")
            return
        visiting.add(node)
        for nxt in adj.get(node, []):
            dfs(nxt)
        visiting.remove(node)
        visited.add(node)

    for node in adj:
        if node not in visited:
            dfs(node)
    return failures


def _build_adjacency(edges: Iterable[Dict[str, str]]) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if not src or not dst:
            continue
        adj.setdefault(src, []).append(dst)
    return adj


def validate_dag(dag: Dict) -> Tuple[bool, List[str]]:
    """Validate DAG structure and semantics for GoV (PRD §7.9).

    Checks include:
    - node IDs referenced by edges must exist
    - node types must be in the allowed set
    - claim nodes require at least one incoming edge
    - graph must be acyclic
    """
    nodes = {str(n.get("id")): n for n in dag.get("nodes", []) if n.get("id")}
    edges = list(dag.get("edges", []) or [])
    failing: List[str] = []

    # Edge references
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src not in nodes:
            failing.append(f"missing_node:{src}")
        if dst not in nodes:
            failing.append(f"missing_node:{dst}")

    # Node type enforcement
    for node_id, node in nodes.items():
        node_type = node.get("type")
        if node_type not in _ALLOWED_TYPES:
            failing.append(f"invalid_type:{node_id}")

    # Claim support requirement
    incoming: Dict[str, int] = {node_id: 0 for node_id in nodes}
    for edge in edges:
        dst = edge.get("to")
        if dst in incoming:
            incoming[dst] += 1
    for node_id, node in nodes.items():
        if node.get("type") == "claim" and incoming.get(node_id, 0) <= 0:
            failing.append(f"unsupported_claim:{node_id}")

    # Cycle detection
    adj = _build_adjacency(edges)
    failing.extend(_detect_cycle(adj))

    unique_failures = list(dict.fromkeys(failing))
    ok = len(unique_failures) == 0
    return ok, unique_failures
