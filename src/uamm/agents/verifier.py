import inspect
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError


class VerifierOutput(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: List[str] = Field(default_factory=list)
    needs_fix: bool


@dataclass
class _LLMVerifier:
    """Wrapper around PydanticAI for structured verification."""

    model_name: str = "gpt-4.1-mini"
    temperature: float = 0.0

    def __post_init__(self) -> None:
        self._agent = None
        self._ensure_agent()

    def evaluate(self, question: str, answer: str) -> Optional[VerifierOutput]:
        if self._agent is None:
            return None
        prompt = self._build_prompt(question=question, answer=answer)
        run_method = getattr(self._agent, "run_sync", None) or getattr(
            self._agent, "run", None
        )
        if run_method is None:
            return None
        try:
            result = run_method(prompt)
        except TypeError:
            result = run_method(input=prompt)
        except Exception as exc:  # pragma: no cover - handled fallback
            logging.getLogger("uamm.verifier").warning(
                "verifier_llm_failed due to %s", exc
            )
            return None
        try:
            if isinstance(result, VerifierOutput):
                return result
            if hasattr(result, "model_dump"):
                data = result.model_dump()
            elif isinstance(result, dict):
                data = result
            else:
                data = getattr(result, "data", result)
            return VerifierOutput.model_validate(data)
        except ValidationError as exc:
            logging.getLogger("uamm.verifier").warning(
                "verifier_llm_parse_error", extra={"errors": exc.errors()}
            )
            return None

    def _ensure_agent(self) -> None:
        try:
            pydantic_ai = __import__("pydantic_ai", fromlist=["Agent"])
            openai_models = __import__(
                "pydantic_ai.models.openai", fromlist=["OpenAIModel", "OpenAIChatModel"]
            )
            AgentCls = getattr(pydantic_ai, "Agent")
            OpenAIModel = getattr(openai_models, "OpenAIChatModel", None) or getattr(
                openai_models, "OpenAIModel"
            )
        except Exception as exc:  # pragma: no cover - dependency missing
            logging.getLogger("uamm.verifier").warning(
                "verifier_llm_unavailable due to %s", exc
            )
            self._agent = None
            return
        model_sig = inspect.signature(OpenAIModel)
        model_kwargs = {}
        if "model" in model_sig.parameters:
            model_kwargs["model"] = self.model_name
        elif "model_name" in model_sig.parameters:
            model_kwargs["model_name"] = self.model_name
        if "temperature" in model_sig.parameters:
            model_kwargs["temperature"] = self.temperature
        try:
            model = OpenAIModel(**model_kwargs)
        except Exception as exc:
            logging.getLogger("uamm.verifier").warning(
                "verifier_model_init_failed", extra={"error": str(exc)}
            )
            self._agent = None
            return
        agent_kwargs = {
            "model": model,
            "result_type": VerifierOutput,
            "system_prompt": self._system_prompt(),
        }
        try:
            self._agent = AgentCls(**agent_kwargs)
        except TypeError:
            # Some versions use 'result_model' instead of 'result_type'
            agent_kwargs.pop("result_type", None)
            agent_kwargs["result_model"] = VerifierOutput
            self._agent = AgentCls(**agent_kwargs)
        except Exception as exc:
            logging.getLogger("uamm.verifier").warning(
                "verifier_agent_init_failed", extra={"error": str(exc)}
            )
            self._agent = None

    def _system_prompt(self) -> str:
        return (
            "You are S2, a structured verifier for the UAMM agent. "
            "Evaluate an assistant answer for correctness, completeness, and risk. "
            "Return JSON with fields: score (0-1), issues (list of short strings), "
            "needs_fix (boolean)."
        )

    @staticmethod
    def _build_prompt(*, question: str, answer: str) -> str:
        return (
            "Question:\n"
            f"{question.strip() or '[blank]'}\n\n"
            "Assistant answer:\n"
            f"{answer.strip() or '[blank]'}\n\n"
            "Instructions:\n"
            "- score near 1.0 when answer is correct, grounded, and complete.\n"
            "- flag missing citations, numbers, or unsupported logic in issues.\n"
            "- set needs_fix true when any blocking issue remains.\n"
            "- keep issues concise (<=10 words each).\n"
        )


def _heuristic_verify(question: str, answer: str) -> VerifierOutput:
    issues: List[str] = []
    q = question.lower()
    has_digits = bool(re.search(r"\d", answer or ""))
    has_link = ("http://" in (answer or "")) or ("https://" in (answer or ""))
    needs_numbers = any(w in q for w in ["count", "number", "patients", "metric"])
    needs_cite = any(w in q for w in ["cite", "source", "reference", "citation"])
    needs_table = any(w in q for w in ["sql", "table", "database", "cohort"])
    if needs_numbers and not has_digits:
        issues.append("missing numbers")
    if needs_cite and not has_link:
        issues.append("missing citations")
    if needs_table and not has_digits:
        issues.append("missing table data")
    score = 1.0
    if issues:
        score = 0.2
    elif has_digits or has_link:
        score = 0.6
    return VerifierOutput(
        score=float(min(max(score, 0.0), 1.0)), issues=issues, needs_fix=bool(issues)
    )


class Verifier:
    """Structured verifier S₂ (PRD §7.3) with LLM-backed evaluation and heuristic fallback."""

    def __init__(self) -> None:
        self._llm = _LLMVerifier()

    def verify(self, question: str, answer: str) -> Tuple[float, List[str], bool]:
        result = self._llm.evaluate(question=question, answer=answer)
        if result is None:
            result = _heuristic_verify(question, answer)
        score = float(min(max(result.score, 0.0), 1.0))
        issues = [issue.strip() for issue in result.issues if issue and issue.strip()]
        needs_fix = bool(result.needs_fix or issues)
        return score, issues, needs_fix
