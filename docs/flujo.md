# Flujo Integration

This project now exposes typed node wrappers that make it straightforward to embed the UAMM pipeline inside a Flujo graph orchestrator. The integration focuses on preserving strict input/output contracts so that nodes can be wired together or swapped without ad-hoc glue code.

## Available Nodes

All nodes live under `uamm.flujo.nodes` and expose Pydantic models describing their interfaces.

| Node | Purpose | Input model | Output model |
| ---- | ------- | ----------- | ------------- |
| `RetrieverNode` | Build a hybrid RAG pack (memory + corpus + optional LanceDB vector hits). | `RetrieverInput` | `RetrieverOutput` (list of `MemoryPackItem`). |
| `MemoryNode` | Query the long-term memory table directly. | `MemoryInput` | `MemoryOutput`. |
| `MainAgentNode` | Run the orchestration loop (SNNE, verifier, refinement, CP). | `MainAgentInput` | `MainAgentOutput` (`AgentResultModel`). |
| `VerifierNode` | Execute the structured verifier (S₂). | `VerifierInput` | `VerifierOutput`. |
| `PolicyNode` | Apply the policy/CP gate to SNNE + S₂ scores. | `PolicyInput` | `PolicyOutput`. |
| `RefinementNode` | Generate the refinement prompt given issues and snippets. | `RefinementInput` | `RefinementOutput`. |
| `ToolNode` | Invoke first-party tools (`WEB_SEARCH`, `WEB_FETCH`, `MATH_EVAL`, `TABLE_QUERY`). | `ToolInput` | `ToolOutput`. |
| `PCNNode` | Register/verify policy-controlled numbers. | `PCNInput` | `PCNOutput`. |
| `GoVNode` | Evaluate a graph-of-verification DAG. | `GoVInput` | `GoVOutput`. |

Each node inherits from `FlujoNode` and can be used directly as callables:

```python
from uamm.flujo.nodes import RetrieverNode, MainAgentNode

retriever = RetrieverNode()
pack = retriever({"question": "Explain modular memory", "db_path": "data/uamm.sqlite"})

agent = MainAgentNode()
result = agent({
    "question": "Explain modular memory",
    "params": {"db_path": "data/uamm.sqlite"},
    "evidence_pack": [item.model_dump() for item in pack.pack],
})
```

## YAML DSL Loader

`uamm.flujo.dsl.load_pipeline_from_yaml` consumes a YAML definition and builds a `FluentPipeline`. Default parameters declared in YAML are automatically merged with the dynamic context produced by upstream nodes.

Example (`pipeline.yaml`):

```yaml
nodes:
  - type: retriever
    options:
      db_path: data/uamm.sqlite
  - type: main_agent
    options:
      params:
        db_path: data/uamm.sqlite
```

Usage:

```python
from uamm.flujo.dsl import load_pipeline_from_yaml

pipeline = load_pipeline_from_yaml("pipeline.yaml")
result = pipeline.run({"question": "Explain modular memory"})
print(result["final"])
```

## Extending the Registry

Custom nodes can be registered at runtime:

```python
from uamm.flujo.dsl import register_node
from uamm.flujo.nodes import FlujoNode

class CustomNode(FlujoNode[MyInput, MyOutput]):
    ...

register_node("custom", CustomNode)
```

The DSL will resolve `type: custom` entries to this class automatically.

## Testing

The new node layer ships with `tests/test_flujo_nodes.py`, which exercises the retriever, verifier, policy, GoV, and YAML loader paths. These tests rely on hash-based embeddings (`UAMM_EMBEDDING_BACKEND=hash`) to avoid external dependencies during CI.
