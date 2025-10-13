from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, condecimal, constr


class MemoryPackItem(BaseModel):
    id: str
    snippet: constr(max_length=240)
    why: str
    score: condecimal(ge=0, le=1)
    url: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    sparse_score: Optional[float] = None
    dense_score: Optional[float] = None


class StepTraceModel(BaseModel):
    step_index: int
    is_refinement: bool
    s1_or_snne: float
    s2: float
    final_score: float
    cp_accept: bool
    issues: List[str] = []
    tools_used: List[str] = []
    action: Literal["accept", "iterate", "abstain"]
    reason: str
    latency_ms: int
    usage: Dict[str, Any] = {}
    change_summary: Optional[str] = None
    llm: Optional[Dict[str, Any]] = None


class UncertaintyModel(BaseModel):
    mode: Literal["snne", "se", "logprob"]
    snne: Optional[float] = None
    snne_raw: Optional[float] = None
    snne_sample_count: Optional[int] = None
    s2: float
    final_score: float
    cp_accept: bool
    prediction_set_size: Optional[int] = None


class AgentResultModel(BaseModel):
    final: str
    stop_reason: str
    uncertainty: UncertaintyModel
    trace: List[StepTraceModel]
    pack_used: List[MemoryPackItem]
    usage: Dict[str, Any] = {}
