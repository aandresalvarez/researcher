from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Set, Tuple

from .validator import validate_dag


Node = Dict[str, any]
Dag = Dict[str, Iterable]


def evaluate_dag(
    dag: Dag,
    *,
    pcn_status: Callable[[str], str | None],
) -> Tuple[bool, List[str]]:
    """Evaluate a GoV DAG and return (ok, failing_reasons).

    Each node may include:
      - `pcn`: token ID that must be verified (`pcn_status` == "verified")
      - `assert`: boolean expression on available context (not used yet; reserved)

    Failing reasons follow the pattern:
      - `pcn_failure:<node_id>`
      - `dependency_failure:<node_id>`
      - `unknown_type:<node_id>`
    """
    valid, validation_failures = validate_dag(dag)
    if not valid:
        return False, validation_failures

    node_map: Dict[str, Node] = {str(n["id"]): dict(n) for n in dag.get("nodes", [])}
    edges = list(dag.get("edges", []) or [])

    parents: Dict[str, List[str]] = {}
    children: Dict[str, List[str]] = {}
    indegree: Dict[str, int] = {node_id: 0 for node_id in node_map}

    for edge in edges:
        src = str(edge.get("from"))
        dst = str(edge.get("to"))
        if src not in node_map or dst not in node_map:
            continue
        parents.setdefault(dst, []).append(src)
        children.setdefault(src, []).append(dst)
        indegree[dst] += 1

    queue: List[str] = [node_id for node_id, deg in indegree.items() if deg == 0]
    failures: List[str] = []
    failed_nodes: Set[str] = set()

    while queue:
        node_id = queue.pop(0)
        node = node_map[node_id]
        node_type = node.get("type", "claim")
        if node_type in {"premise", "calculation", "evidence", "observation"}:
            pcn_token = node.get("pcn")
            if pcn_token:
                status = pcn_status(pcn_token) if pcn_status else None
                if status != "verified":
                    failures.append(f"pcn_failure:{node_id}")
                    failed_nodes.add(node_id)
        elif node_type == "claim":
            # A claim fails if any parent failed.
            for parent_id in parents.get(node_id, []):
                if parent_id in failed_nodes:
                    failures.append(f"dependency_failure:{node_id}")
                    failed_nodes.add(node_id)
                    break
        else:
            failures.append(f"unknown_type:{node_id}")
            failed_nodes.add(node_id)

        for child in children.get(node_id, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    ok = len(failures) == 0
    return ok, failures


__all__ = ["evaluate_dag"]
