from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def policies_dir() -> Path:
    return Path(os.getenv("UAMM_POLICIES_DIR", "config/policies")).resolve()


def list_policies() -> list[str]:
    base = policies_dir()
    if not base.exists():
        return []
    names: list[str] = []
    for p in base.glob("*.yaml"):
        names.append(p.stem)
    return sorted(names)


def load_policy(name: str) -> Dict[str, Any]:
    base = policies_dir()
    path = base / f"{name}.yaml"
    if not path.exists() or yaml is None:
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # Accept only known keys
    allowed = {
        "accept_threshold",
        "borderline_delta",
        "tool_budget_per_refinement",
        "tool_budget_per_turn",
        "tools_requiring_approval",
        "table_allowed",
        "table_policies",
        "table_allowed_by_domain",
        "rag_weight_sparse",
        "rag_weight_dense",
        "docs_chunk_mode",
        "docs_chunk_chars",
        "docs_overlap_chars",
        "docs_chunk_tokens",
        "docs_overlap_tokens",
        "vector_backend",
        "lancedb_uri",
        "lancedb_table",
        "lancedb_metric",
        "lancedb_k",
    }
    return {k: v for k, v in data.items() if k in allowed}
