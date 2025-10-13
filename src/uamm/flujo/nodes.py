from __future__ import annotations

from abc import ABC, abstractmethod
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
)

from pydantic import BaseModel, Field

from uamm.models.schemas import AgentResultModel, MemoryPackItem
from uamm.rag.pack import build_pack
from uamm.storage.memory import search_memory
from uamm.agents.main_agent import MainAgent
from uamm.agents.verifier import Verifier
from uamm.policy.policy import PolicyConfig, final_score, decide
from uamm.policy.cp import ConformalGate
from uamm.refine.prompt import build_refinement_prompt
from uamm.gov.executor import evaluate_dag
from uamm.pcn.verification import PCNVerifier
from uamm.tools.web_fetch import web_fetch
from uamm.tools.web_search import web_search
from uamm.tools.math_eval import math_eval
from uamm.tools.table_query import table_query


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class FlujoNode(ABC, Generic[InputT, OutputT]):
    """Base class for Flujo-compatible nodes with typed I/O contracts."""

    name: str
    input_model: Type[InputT]
    output_model: Type[OutputT]

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.__class__.__name__

    def __call__(self, payload: Dict[str, Any] | InputT, **kwargs: Any) -> OutputT:
        data = (
            payload if isinstance(payload, BaseModel) else self.input_model(**payload)
        )
        return self.run(data, **kwargs)

    def run(self, payload: InputT, **kwargs: Any) -> OutputT:
        return self.execute(payload, **kwargs)

    @abstractmethod
    def execute(self, payload: InputT, **kwargs: Any) -> OutputT:
        raise NotImplementedError


class RetrieverInput(BaseModel):
    question: str
    db_path: str
    memory_k: int = 8
    corpus_k: int = 8
    budget: int = 8
    min_score: float = 0.1
    w_sparse: float = 0.5
    w_dense: float = 0.5
    vector_backend: str = "none"
    lancedb_uri: str | None = None
    lancedb_table: str | None = None
    lancedb_metric: str | None = None
    lancedb_k: int | None = None


class RetrieverOutput(BaseModel):
    pack: List[MemoryPackItem]


class RetrieverNode(FlujoNode[RetrieverInput, RetrieverOutput]):
    input_model = RetrieverInput
    output_model = RetrieverOutput

    def execute(self, payload: RetrieverInput, **kwargs: Any) -> RetrieverOutput:
        pack = build_pack(
            payload.db_path,
            payload.question,
            memory_k=payload.memory_k,
            corpus_k=payload.corpus_k,
            budget=payload.budget,
            min_score=payload.min_score,
            w_sparse=payload.w_sparse,
            w_dense=payload.w_dense,
            vector_backend=payload.vector_backend,
            lancedb_uri=payload.lancedb_uri,
            lancedb_table=payload.lancedb_table,
            lancedb_metric=payload.lancedb_metric,
            lancedb_k=payload.lancedb_k,
        )
        return RetrieverOutput(pack=[MemoryPackItem(**p) for p in pack])


class MemoryInput(BaseModel):
    db_path: str
    query: str
    limit: int = 8


class MemoryOutput(BaseModel):
    hits: List[MemoryPackItem]


class MemoryNode(FlujoNode[MemoryInput, MemoryOutput]):
    input_model = MemoryInput
    output_model = MemoryOutput

    def execute(self, payload: MemoryInput, **kwargs: Any) -> MemoryOutput:
        hits = search_memory(payload.db_path, payload.query, k=payload.limit)
        return MemoryOutput(hits=[MemoryPackItem(**hit) for hit in hits])


class MainAgentInput(BaseModel):
    question: str
    params: Dict[str, Any] = Field(default_factory=dict)
    evidence_pack: List[Dict[str, Any]] = Field(default_factory=list)


class MainAgentOutput(AgentResultModel):
    """Alias for clarity in node signatures."""


class MainAgentNode(FlujoNode[MainAgentInput, MainAgentOutput]):
    input_model = MainAgentInput
    output_model = MainAgentOutput

    def __init__(
        self,
        *,
        cp_enabled: bool = False,
        policy: PolicyConfig | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._agent = MainAgent(cp_enabled=cp_enabled, policy=policy)

    def execute(self, payload: MainAgentInput, **kwargs: Any) -> MainAgentOutput:
        params = dict(payload.params)
        params.setdefault("question", payload.question)
        result = self._agent.answer(params=params, emit=None)
        return MainAgentOutput(**result)


class VerifierInput(BaseModel):
    question: str
    answer: str


class VerifierOutput(BaseModel):
    score: float
    issues: List[str]
    needs_fix: bool


class VerifierNode(FlujoNode[VerifierInput, VerifierOutput]):
    input_model = VerifierInput
    output_model = VerifierOutput

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._verifier = Verifier()

    def execute(self, payload: VerifierInput, **kwargs: Any) -> VerifierOutput:
        score, issues, needs_fix = self._verifier.verify(
            payload.question, payload.answer
        )
        return VerifierOutput(score=score, issues=list(issues), needs_fix=needs_fix)


class PolicyInput(BaseModel):
    snne: float
    s2: float
    config: PolicyConfig = Field(default_factory=PolicyConfig)
    cp_enabled: bool = False
    cp_tau: float | None = None


class PolicyOutput(BaseModel):
    final_score: float
    cp_accept: bool
    action: str


class PolicyNode(FlujoNode[PolicyInput, PolicyOutput]):
    input_model = PolicyInput
    output_model = PolicyOutput

    def execute(self, payload: PolicyInput, **kwargs: Any) -> PolicyOutput:
        threshold_supplier = (
            (lambda: payload.cp_tau) if payload.cp_tau is not None else None
        )
        gate = ConformalGate(
            enabled=payload.cp_enabled, threshold_supplier=threshold_supplier
        )
        final = final_score(payload.snne, payload.s2, cfg=payload.config)
        cp_accept = gate.accept(final)
        action = decide(final, payload.config, cp_accept)
        return PolicyOutput(final_score=final, cp_accept=cp_accept, action=action)


class RefinementInput(BaseModel):
    question: str
    previous_answer: str
    issues: List[str]
    context_snippets: List[str] = Field(default_factory=list)


class RefinementOutput(BaseModel):
    prompt: str


class RefinementNode(FlujoNode[RefinementInput, RefinementOutput]):
    input_model = RefinementInput
    output_model = RefinementOutput

    def execute(self, payload: RefinementInput, **kwargs: Any) -> RefinementOutput:
        prompt = build_refinement_prompt(
            question=payload.question,
            previous_answer=payload.previous_answer,
            issues=payload.issues,
            context_snippets=payload.context_snippets,
        )
        return RefinementOutput(prompt=prompt)


class ToolInput(BaseModel):
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)


class ToolOutput(BaseModel):
    result: Any


_TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    "WEB_SEARCH": web_search,
    "WEB_FETCH": web_fetch,
    "MATH_EVAL": math_eval,
    "TABLE_QUERY": table_query,
}


class ToolNode(FlujoNode[ToolInput, ToolOutput]):
    input_model = ToolInput
    output_model = ToolOutput

    def execute(self, payload: ToolInput, **kwargs: Any) -> ToolOutput:
        tool = _TOOL_REGISTRY.get(payload.name.upper())
        if tool is None:
            raise ValueError(f"Unknown tool '{payload.name}'")
        result = tool(**payload.args)
        return ToolOutput(result=result)


class PCNInput(BaseModel):
    token_id: str
    policy: Dict[str, Any] | None = None
    provenance: Dict[str, Any] | None = None
    value: Any | None = None
    failure_reason: str | None = None


class PCNOutput(BaseModel):
    status: str
    entry: Dict[str, Any]


class PCNNode(FlujoNode[PCNInput, PCNOutput]):
    input_model = PCNInput
    output_model = PCNOutput

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._pcn = PCNVerifier()

    def execute(self, payload: PCNInput, **kwargs: Any) -> PCNOutput:
        if payload.failure_reason:
            entry = self._pcn.fail(payload.token_id, payload.failure_reason)
        elif payload.value is not None:
            entry = self._pcn.verify_math(
                payload.token_id, expr="provided", observed_value=payload.value
            )
        else:
            entry = self._pcn.register(
                payload.token_id, policy=payload.policy, provenance=payload.provenance
            )
        return PCNOutput(status=entry["status"], entry=entry)


class GoVInput(BaseModel):
    dag: Dict[str, Any]
    pcn_status: Callable[[str], Optional[str]] | None = None


class GoVOutput(BaseModel):
    ok: bool
    failing: List[str]


class GoVNode(FlujoNode[GoVInput, GoVOutput]):
    input_model = GoVInput
    output_model = GoVOutput

    def execute(self, payload: GoVInput, **kwargs: Any) -> GoVOutput:
        status_fn = payload.pcn_status or (lambda _token: None)
        ok, failing = evaluate_dag(payload.dag, pcn_status=status_fn)
        return GoVOutput(ok=ok, failing=list(failing))


class FluentPipeline:
    """Convenience wrapper to chain nodes and pass the output onward."""

    def __init__(
        self,
        nodes: Iterable[
            Tuple[FlujoNode[Any, Any], Dict[str, Any]] | FlujoNode[Any, Any]
        ],
    ) -> None:
        prepared: List[Tuple[FlujoNode[Any, Any], Dict[str, Any]]] = []
        for entry in nodes:
            if isinstance(entry, tuple):
                node, defaults = entry
                prepared.append((node, dict(defaults)))
            else:
                prepared.append((entry, {}))
        self._nodes = prepared

    def run(self, initial: Dict[str, Any] | BaseModel) -> Dict[str, Any]:
        context: Dict[str, Any]
        if isinstance(initial, BaseModel):
            context = initial.model_dump()
        elif isinstance(initial, dict):
            context = dict(initial)
        else:
            context = {}
        payload: Any = initial
        for node, defaults in self._nodes:
            merged = {**defaults, **context}
            payload = node(merged)
            if isinstance(payload, BaseModel):
                context.update(payload.model_dump())
            elif isinstance(payload, dict):
                context.update(payload)
            else:
                context["result"] = payload
        if isinstance(payload, BaseModel):
            return payload.model_dump()
        if isinstance(payload, dict):
            return dict(payload)
        return {"result": payload}
