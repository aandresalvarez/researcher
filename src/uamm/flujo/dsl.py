from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field

from uamm.flujo.nodes import (
    FluentPipeline,
    FlujoNode,
    GoVNode,
    MainAgentNode,
    MemoryNode,
    PCNNode,
    PolicyNode,
    RefinementNode,
    RetrieverNode,
    ToolNode,
    VerifierNode,
)

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


class NodeConfig(BaseModel):
    type: str
    name: str | None = None
    options: Dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    nodes: List[NodeConfig]


_NODE_REGISTRY: Dict[str, Type[FlujoNode[Any, Any]]] = {
    "retriever": RetrieverNode,
    "memory": MemoryNode,
    "main_agent": MainAgentNode,
    "verifier": VerifierNode,
    "policy": PolicyNode,
    "refinement": RefinementNode,
    "tool": ToolNode,
    "pcn": PCNNode,
    "gov": GoVNode,
}


def register_node(kind: str, cls: Type[FlujoNode[Any, Any]]) -> None:
    _NODE_REGISTRY[kind.lower()] = cls


def load_pipeline_from_yaml(path: str | Path) -> FluentPipeline:
    if yaml is None:
        raise RuntimeError("pyyaml is not installed; cannot load Flujo DSL.")
    location = Path(path)
    payload = yaml.safe_load(location.read_text(encoding="utf-8"))
    config = PipelineConfig(**payload)
    nodes: List[tuple[FlujoNode[Any, Any], Dict[str, Any]]] = []
    for node_cfg in config.nodes:
        node_cls = _NODE_REGISTRY.get(node_cfg.type.lower())
        if node_cls is None:
            # allow dotted path fallback
            module_name, _, cls_name = node_cfg.type.rpartition(".")
            if module_name:
                module = importlib.import_module(module_name)
                node_cls = getattr(module, cls_name)
            if node_cls is None:
                raise ValueError(f"Unknown Flujo node type '{node_cfg.type}'")
        node = node_cls(name=node_cfg.name)
        nodes.append((node, dict(node_cfg.options)))
    return FluentPipeline(nodes)
