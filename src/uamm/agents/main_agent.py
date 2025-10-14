import inspect
import logging
import re
from importlib import import_module
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4
from uamm.policy.policy import final_score, PolicyConfig, decide
from uamm.policy.cp import ConformalGate
from uamm.tools.web_search import web_search
from uamm.tools.web_fetch import web_fetch
from uamm.tools.math_eval import math_eval
from uamm.tools.table_query import table_query
from uamm.agents.verifier import Verifier
from uamm.rag.pack import build_pack
from uamm.rag.embeddings import embed_text
from uamm.uq.snne import snne as snne_score, normalize as snne_normalize
from uamm.uq.sampling import generate_answer_variants
from uamm.uq.calibration import SNNECalibrator
from uamm.refine.prompt import build_refinement_prompt
from uamm.refine.compose import build_refined_answer
from uamm.security.egress import EgressPolicy
from uamm.security.prompt_guard import PromptInjectionError
from uamm.planning.strategies import plan_best_answer, PlanningConfig
from uamm.pcn.provenance import (
    build_url_provenance,
    build_math_provenance,
    build_sql_provenance,
)
from uamm.pcn.verification import PCNVerifier
from uamm.gov.executor import evaluate_dag
from uamm.verification.faithfulness import compute_faithfulness
from uamm.security.guardrails import GuardrailsConfig, pre_guard, post_guard


_PCN_PLACEHOLDER_RE = re.compile(r"\[PCN:[^\]]+\]")
_LOGGER = logging.getLogger("uamm.agent")


def _summarize(snippet: str) -> str:
    text = (snippet or "").strip()
    if len(text) > 240:
        text = text[:237].rstrip() + "..."
    return text or "Evidence retrieved but snippet was empty."


class LLMGenerator:
    """PydanticAI-backed generator with graceful fallback when unavailable."""

    def __init__(
        self,
        *,
        model_name: str = "gpt-4.1-mini",
        temperature: float = 0.2,
        max_output_tokens: int = 800,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._system_prompt = (
            "You are the UAMM main agent. Provide concise, evidence-grounded answers "
            "that cite sources using bracketed indices (e.g., [1]). Never fabricate citations. "
            "If evidence is insufficient, explain what is missing and signal the gap."
        )
        self._agent = None
        self._run_method: Optional[Callable[[Any], Any]] = None
        self._enabled = False
        self._ensure_agent()

    # Public API ---------------------------------------------------------

    def generate(
        self,
        *,
        question: str,
        evidence_pack: Sequence[Dict[str, Any]],
        instructions: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        if model_override and model_override != self.model_name:
            self.model_name = model_override
            self._ensure_agent()
        prompt = self._build_prompt(
            question=question, evidence_pack=evidence_pack, instructions=instructions
        )
        if self._enabled and self._run_method is not None:
            try:
                result = self._invoke_agent(prompt)
                text = self._extract_text(result)
                if not text:
                    raise ValueError("empty LLM response")
                tokens = self._extract_tokens(result, text)
                return text.strip(), {
                    "mode": "pydantic_ai",
                    "model": self.model_name,
                    "tokens": tokens,
                }
            except Exception as exc:  # pragma: no cover - fallback path
                _LOGGER.warning(
                    "llm_generate_fallback due to %s",
                    exc,
                    extra={"error": str(exc), "model": self.model_name},
                )
                self._enabled = False
        fallback_text = self._fallback_answer(evidence_pack)
        return fallback_text, {
            "mode": "fallback",
            "model": "heuristic",
            "tokens": fallback_text.split(),
        }

    # Internal helpers ---------------------------------------------------

    def _ensure_agent(self) -> None:
        try:
            pydantic_ai = import_module("pydantic_ai")
            models_mod = import_module("pydantic_ai.models.openai")
            AgentCls = getattr(pydantic_ai, "Agent")
            OpenAIModelCls = getattr(models_mod, "OpenAIChatModel", None) or getattr(
                models_mod, "OpenAIModel"
            )
        except Exception as exc:  # pragma: no cover - dependency missing
            _LOGGER.warning("llm_agent_unavailable", extra={"error": str(exc)})
            self._agent = None
            self._run_method = None
            self._enabled = False
            return

        openai_kwargs: Dict[str, Any] = {}
        openai_sig = inspect.signature(OpenAIModelCls)
        if "model" in openai_sig.parameters:
            openai_kwargs["model"] = self.model_name
        elif "model_name" in openai_sig.parameters:
            openai_kwargs["model_name"] = self.model_name
        if "temperature" in openai_sig.parameters:
            openai_kwargs["temperature"] = self.temperature
        if "max_output_tokens" in openai_sig.parameters:
            openai_kwargs["max_output_tokens"] = self.max_output_tokens

        try:
            model = OpenAIModelCls(**openai_kwargs)
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning(
                "llm_model_init_failed",
                extra={"error": str(exc), "kwargs": openai_kwargs},
            )
            self._agent = None
            self._run_method = None
            self._enabled = False
            return

        agent_kwargs: Dict[str, Any] = {"model": model}
        agent_sig = inspect.signature(AgentCls)
        if "result_type" in agent_sig.parameters:
            agent_kwargs["result_type"] = str
        elif "result_model" in agent_sig.parameters:
            agent_kwargs["result_model"] = str
        if "system_prompt" in agent_sig.parameters:
            agent_kwargs["system_prompt"] = self._system_prompt
        elif "prompt_template" in agent_sig.parameters:
            agent_kwargs["prompt_template"] = self._system_prompt

        try:
            self._agent = AgentCls(**agent_kwargs)
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning(
                "llm_agent_init_failed",
                extra={"error": str(exc), "kwargs": agent_kwargs},
            )
            self._agent = None
            self._run_method = None
            self._enabled = False
            return

        run_method = getattr(self._agent, "run_sync", None)
        if run_method is None:
            run_method = getattr(self._agent, "run", None)
        if not callable(run_method):
            self._agent = None
            self._run_method = None
            self._enabled = False
            _LOGGER.warning(
                "llm_agent_missing_run_method", extra={"model": self.model_name}
            )
            return
        self._run_method = run_method
        self._enabled = True

    def _build_prompt(
        self,
        *,
        question: str,
        evidence_pack: Sequence[Dict[str, Any]],
        instructions: Optional[str],
    ) -> str:
        evidence_lines: List[str] = []
        for idx, item in enumerate(evidence_pack[:5], start=1):
            snippet = _summarize(str(item.get("snippet", "")))
            provenance = item.get("url") or item.get("title") or item.get("source")
            if provenance:
                evidence_lines.append(f"{idx}. {snippet} (source: {provenance})")
            else:
                evidence_lines.append(f"{idx}. {snippet}")
        if not evidence_lines:
            evidence_lines.append("No external evidence retrieved.")
        extra = instructions.strip() if isinstance(instructions, str) else ""
        if extra:
            extra = f"\nAdditional operator guidance:\n{extra.strip()}"
        evidence_text = "\n".join(evidence_lines)
        return (
            f"{self._system_prompt}\n\n"
            f"Question:\n{question.strip() or '[blank]'}\n\n"
            "Grounding evidence:\n"
            f"{evidence_text}\n\n"
            "Respond with a concise paragraph that answers the question, cites supporting evidence "
            "using [index] notation, and clearly calls out missing information when the evidence is insufficient."
            f"{extra}"
        )

    def _invoke_agent(self, prompt: str) -> Any:
        assert self._run_method is not None  # for type checkers
        try:
            return self._run_method(prompt)
        except TypeError:
            try:
                return self._run_method(prompt=prompt)
            except TypeError:
                return self._run_method(input=prompt)

    def _extract_text(self, result: Any) -> str:
        # Common result attribute names across pydantic-ai releases.
        for attr in ("text", "result", "answer", "final", "output"):
            value = getattr(result, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        if hasattr(result, "data"):
            data = getattr(result, "data")
            if isinstance(data, str):
                return data
            if isinstance(data, dict):
                candidate = data.get("text") or data.get("answer")
                if isinstance(candidate, str):
                    return candidate
        if hasattr(result, "model_dump"):
            dumped = result.model_dump()  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                for key in ("text", "answer", "result", "final"):
                    candidate = dumped.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate
        return str(result)

    def _extract_tokens(self, result: Any, text: str) -> List[str]:
        tokens: List[str] = []
        for attr in ("tokens", "response_tokens", "response_text"):
            value = getattr(result, attr, None)
            if isinstance(value, (list, tuple)):
                tokens = [str(v) for v in value if isinstance(v, (str, int, float))]
                if tokens:
                    return tokens
        return text.split()

    @staticmethod
    def _fallback_answer(evidence_pack: Sequence[Dict[str, Any]]) -> str:
        if evidence_pack:
            top = evidence_pack[0]
            snippet = _summarize(str(top.get("snippet", "")))
            url = top.get("url")
            if url:
                return f"{snippet} (source: {url})"
            return snippet
        return "I do not have grounded evidence yet; need more context or documents."


def _resolve_pcn_placeholders(text: str, replacements: Dict[str, str]) -> str:
    if not text:
        return text

    def _repl(match: re.Match[str]) -> str:
        key = match.group(0)
        return replacements.get(key, "[unverified]")

    return _PCN_PLACEHOLDER_RE.sub(_repl, text)


class MainAgent:
    """Stub for the main agent orchestration (PRD §7.1).

    Responsibilities:
    - Stream initial answer
    - Compute SNNE (S1) + Verifier (S2)
    - Policy + CP gate
    - Optional refinement with tools
    - Return AgentResultModel
    """

    def __init__(
        self, *, cp_enabled: bool = False, policy: PolicyConfig | None = None
    ) -> None:
        self._cfg = policy or PolicyConfig()
        self._cp = ConformalGate(enabled=cp_enabled)
        self._verifier = Verifier()
        self._llm = LLMGenerator()
        self._snne_calibrators: Dict[str, SNNECalibrator] = {}
        self._pcn = PCNVerifier()

    def _get_snne_calibrator(self, db_path: Optional[str]) -> Optional[SNNECalibrator]:
        if not db_path:
            return None
        calibrator = self._snne_calibrators.get(db_path)
        if calibrator is None:
            calibrator = SNNECalibrator(db_path)
            self._snne_calibrators[db_path] = calibrator
        return calibrator

    def answer(
        self,
        params: Dict[str, Any],
        emit: Callable[[str, Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        """Answer with a simple policy + one-step refinement skeleton.

        Emits optional events via `emit(event, data)` for SSE: tool/trace/score.
        """
        question = params.get("question", "")
        max_refinements = int(params.get("max_refinements", 1))
        per_ref_budget = int(params.get("tool_budget_per_refinement", 2))
        tool_budget_turn = int(params.get("tool_budget_per_turn", 4))
        memory_budget = int(params.get("memory_budget", 8))
        db_path = params.get("db_path")
        domain = str(params.get("domain", "default") or "default").lower()

        def _emit(evt: str, data: Dict[str, Any]) -> None:
            if emit is not None:
                emit(evt, data)

        # Retrieve pack (memory + corpus) and keep for context
        pack_used: List[Dict[str, Any]] = []
        if db_path:
            w_sparse = float(params.get("rag_weight_sparse", 0.5))
            w_dense = float(params.get("rag_weight_dense", 0.5))
            vector_backend = str(params.get("vector_backend", "none") or "none").lower()
            raw_lancedb_k = params.get("lancedb_k")
            try:
                lancedb_k = (
                    int(raw_lancedb_k) if raw_lancedb_k not in (None, "") else None
                )
            except (TypeError, ValueError):
                lancedb_k = None
            pack_used = build_pack(
                db_path,
                question,
                memory_k=memory_budget,
                corpus_k=memory_budget,
                budget=memory_budget,
                w_sparse=w_sparse,
                w_dense=w_dense,
                vector_backend=vector_backend,
                lancedb_uri=params.get("lancedb_uri"),
                lancedb_table=params.get("lancedb_table"),
                lancedb_metric=params.get("lancedb_metric"),
                lancedb_k=lancedb_k,
            )
        # Tool allowlist (optional): when provided, only names in this set may execute
        raw_allowed = params.get("tools_allowed")
        allowed_tools: Optional[set[str]] = (
            set(map(str, raw_allowed))
            if isinstance(raw_allowed, (list, tuple, set))
            else None
        )

        def _tool_allowed(name: str) -> bool:
            return True if allowed_tools is None else name in allowed_tools

        # Guardrails config
        guard_enabled = bool(params.get("guardrails_enabled", False))
        guard_conf_path = params.get("guardrails_config_path")
        guard_cfg = GuardrailsConfig.load(
            str(guard_conf_path) if guard_conf_path else None
        )

        # Initial grounded answer using retrieved evidence
        if guard_enabled:
            ok, vios = pre_guard(question, config=guard_cfg)
            if not ok:
                _emit("guardrails", {"stage": "pre", "violations": vios})
        llm_model_override = params.get("llm_model")
        llm_instructions = params.get("llm_instructions")
        answer_text, llm_meta = self._llm.generate(
            question=question,
            evidence_pack=pack_used,
            instructions=llm_instructions,
            model_override=str(llm_model_override) if llm_model_override else None,
        )
        snne_calibrator = self._get_snne_calibrator(db_path)
        # SNNE scoring (normalized) using heuristic paraphrase variants
        snne_mode = str(params.get("uq_mode", "snne")).lower()
        sample_count = int(params.get("snne_samples", 5) or 5)
        snne_tau = max(1e-6, float(params.get("snne_tau", 0.3) or 0.3))
        snne_samples: List[str] = []
        raw_snne: float | None = None
        if snne_mode == "snne":
            try:
                evidence_snips = (
                    [p.get("snippet", "") for p in pack_used[:3]] if pack_used else []
                )
                snne_samples = generate_answer_variants(
                    answer_text,
                    question=question,
                    evidence_snippets=evidence_snips,
                    count=sample_count,
                )
                raw_snne = snne_score(
                    snne_samples, snne_tau, embed=lambda text: embed_text(text)
                )
                if snne_calibrator:
                    s1 = snne_calibrator.normalize(domain=domain, raw=raw_snne)
                else:
                    s1 = snne_normalize(raw_snne)
            except Exception:
                s1 = 1.0
                snne_samples = [answer_text or "No answer available."] * max(
                    2, sample_count
                )
        else:
            s1 = 1.0
        _emit(
            "uq",
            {
                "mode": snne_mode,
                "raw": raw_snne,
                "normalized": s1,
                "samples": snne_samples[: min(len(snne_samples), 3)],
            },
        )
        s2, issues, needs_fix = self._verifier.verify(question, answer_text)
        # Claim-level faithfulness integration (optional)
        try:
            faith_enabled = bool(params.get("faithfulness_enabled", True))
            faith_threshold = float(params.get("faithfulness_threshold", 0.6) or 0.6)
        except Exception:
            faith_enabled = True
            faith_threshold = 0.6
        if faith_enabled and pack_used:
            try:
                f = compute_faithfulness(answer_text, pack_used)
                fscore = f.get("score")
                if fscore is not None and fscore < faith_threshold:
                    if "unsupported claims" not in issues:
                        issues = list(issues) + ["unsupported claims"]
                        needs_fix = True
            except Exception:
                pass
        # Post guardrails check on answer
        if guard_enabled:
            try:
                ok_post, vios_post = post_guard(answer_text, config=guard_cfg)
                if not ok_post:
                    _emit("guardrails", {"stage": "post", "violations": vios_post})
                    if "policy_violation" not in issues:
                        issues = list(issues) + ["policy_violation"]
                        needs_fix = True
            except Exception:
                pass
        if needs_fix and snne_mode == "snne":
            s1 = min(1.0, s1 + 0.1 * len(issues))
        S = final_score(snne_norm=s1, s2=s2, cfg=self._cfg)
        cp_ok = self._cp.accept(S)
        if not cp_ok and getattr(self._cp, "last_reason", None) == "missing_tau":
            issues = list(issues) + ["cp_missing_calibration"]
        action = decide(S, self._cfg, cp_ok)

        # Selective planning (borderline cases by default)
        planning_enabled = bool(params.get("planning_enabled", False))
        planning_budget = int(params.get("planning_budget", 0) or 0)
        planning_mode = str(params.get("planning_mode", "tot") or "tot")
        planning_when = str(params.get("planning_when", "borderline") or "borderline")
        borderline = (
            (self._cfg.tau_accept - self._cfg.delta) <= S < self._cfg.tau_accept
        )
        if (
            planning_enabled
            and planning_budget > 0
            and (
                planning_when == "always"
                or borderline
                or (planning_when == "iterate" and action == "iterate")
            )
        ):
            try:
                plan_out = plan_best_answer(
                    question=question,
                    evidence_pack=pack_used,
                    base_answer=answer_text,
                    embed=lambda text: embed_text(text),
                    snne_calibrator=snne_calibrator,
                    verifier=self._verifier,
                    policy_cfg=self._cfg,
                    sample_count=max(2, sample_count // 2),
                    config=PlanningConfig(mode=planning_mode, budget=planning_budget),
                    domain=domain,
                )
            except Exception as _exc:  # pragma: no cover
                plan_out = {"improved": False, "best": {}, "base": {"S": S}}
            best = plan_out.get("best") or {}
            base = plan_out.get("base") or {"S": S}
            improved = bool(plan_out.get("improved"))
            _emit(
                "planning",
                {
                    "mode": planning_mode,
                    "budget": planning_budget,
                    "candidates": len(plan_out.get("candidates", []) or []),
                    "base_S": float(base.get("S", S) or S),
                    "best_S": float(best.get("S", S) or S),
                    "improved": improved,
                },
            )
            if improved:
                # Adopt improved candidate
                answer_text = str(plan_out.get("best_answer", answer_text))
                s1 = float(best.get("s1", s1) or s1)
                raw_snne = best.get("raw_snne", raw_snne)
                snne_samples = best.get("snne_samples", snne_samples)  # type: ignore[assignment]
                s2 = float(best.get("s2", s2) or s2)
                issues = list(best.get("issues", issues) or issues)
                needs_fix = bool(best.get("needs_fix", needs_fix) or needs_fix)
                S = float(best.get("S", S) or S)
                cp_ok = self._cp.accept(S)
                if (
                    not cp_ok
                    and getattr(self._cp, "last_reason", None) == "missing_tau"
                ):
                    if "cp_missing_calibration" not in issues:
                        issues = list(issues) + ["cp_missing_calibration"]
                action = decide(S, self._cfg, cp_ok)
        trace: List[Dict[str, Any]] = [
            {
                "step_index": 0,
                "is_refinement": False,
                "s1_or_snne": s1,
                "s2": s2,
                "final_score": S,
                "cp_accept": cp_ok,
                "issues": issues,
                "tools_used": [],
                "action": action,
                "reason": "initial",
                "latency_ms": 0,
                "usage": {},
                "llm": {
                    "mode": llm_meta.get("mode"),
                    "model": llm_meta.get("model"),
                },
            }
        ]
        _emit("score", {"s1": s1, "s2": s2, "final_score": S, "cp_accept": cp_ok})
        _emit(
            "trace",
            {"step": 0, "is_refinement": False, "issues": issues, "tools_used": []},
        )

        # Refinement attempt if borderline and budget allows
        def _extract_number(text: str) -> float | None:
            if not text:
                return None
            match = re.search(r"[-+]?(?:\d+\.\d+|\d+)", text)
            if not match:
                return None
            try:
                return float(match.group())
            except ValueError:
                return None

        def _guess_table_sql(
            question: str, context: List[str] | None = None
        ) -> str | None:
            q = (question or "").lower()
            ctx = " ".join(context or []).lower()
            if "demo" in q or "demo" in ctx:
                if any(term in q for term in ["count", "number", "patients", "rows"]):
                    return "select count(*) as count from demo"
                if any(term in q for term in ["list", "show", "records"]):
                    return "select * from demo limit 5"
            if "cohort" in q and "count" in q:
                return "select cohort, count(*) as count from demo group by cohort"
            return None

        approvals_store = params.get("approvals")
        requires_approval = set(params.get("tools_requiring_approval", []))
        approved_tools = set(params.get("approved_tools", []) or [])
        refined = False
        final_answer = answer_text
        final_s1 = s1
        final_raw_snne = raw_snne
        final_samples = snne_samples
        final_s2 = s2
        final_S = S
        final_cp_ok = cp_ok
        final_action = action
        final_issues = issues
        final_needs_fix = needs_fix
        iteration = 0
        context_snips = (
            [p.get("snippet", "") for p in pack_used[:2]] if pack_used else []
        )
        candidate_urls = [p.get("url") for p in pack_used if p.get("url")]

        pcn_replacements: Dict[str, str] = {}

        while (
            final_action == "iterate"
            and final_needs_fix
            and iteration < max_refinements
            and tool_budget_turn > 0
        ):
            iteration += 1
            tool_budget_ref = min(per_ref_budget, tool_budget_turn)
            tools_used: List[str] = []
            approval_pending = False
            iteration_pending: List[str] = []
            fetch_url = None
            fetch_snippet = None
            math_value = None
            math_expr = None
            table_rows: List[Any] | None = None
            table_sql: str | None = None
            table_summary: str | None = None
            table_dag_failing: List[str] = []
            table_numeric: float | None = None
            pcn_placeholders: List[str] = []

            prompt_summary = {
                "iteration": iteration,
                "issues_before": list(final_issues),
                "context_snippet": (context_snips[0] if context_snips else None),
            }

            _emit(
                "trace",
                {
                    "step": iteration,
                    "is_refinement": True,
                    "issues": final_issues,
                    "tools_used": [],
                    "prompt_summary": prompt_summary,
                    "prompt_preview": build_refinement_prompt(
                        question=question,
                        previous_answer=final_answer,
                        issues=final_issues,
                        context_snippets=context_snips,
                    )[:240],
                },
            )
            _emit("gov", {"dag_delta": {"ok": True, "failing": []}})

            def _request_approval(tool_name: str, meta: Dict[str, Any]) -> str | None:
                if approvals_store is None:
                    return None
                appr_id = str(uuid4())
                try:
                    approvals_store.create(appr_id, {"tool": tool_name, **meta})
                except Exception:
                    return None
                iteration_pending.append(appr_id)
                return appr_id

            # WEB_SEARCH as lightweight exploratory tool when budgets allow
            if tool_budget_ref > 0 and tool_budget_turn > 0:
                tool_name = "WEB_SEARCH"
                if not _tool_allowed(tool_name):
                    _emit(
                        "tool",
                        {
                            "name": tool_name,
                            "status": "blocked",
                            "meta": {"reason": "not_allowed"},
                        },
                    )
                elif tool_name in requires_approval and tool_name not in approved_tools:
                    appr_id = _request_approval(
                        tool_name, {"question_preview": question[:120]}
                    )
                    if appr_id:
                        _emit(
                            "tool",
                            {
                                "id": appr_id,
                                "name": tool_name,
                                "status": "waiting_approval",
                                "meta": {"k": 3},
                            },
                        )
                        approval_pending = True
                elif _tool_allowed(tool_name):
                    _emit(
                        "tool",
                        {
                            "name": tool_name,
                            "status": "start",
                            "meta": {
                                "k": 3,
                                "ref_remaining": tool_budget_ref,
                                "turn_remaining": tool_budget_turn,
                            },
                        },
                    )
                    try:
                        _ = web_search(question, k=3)
                        tools_used.append(tool_name)
                        tool_budget_ref -= 1
                        tool_budget_turn -= 1
                        if tool_name in requires_approval:
                            approved_tools.add(tool_name)
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "stop",
                                "meta": {
                                    "k": 3,
                                    "ref_remaining": tool_budget_ref,
                                    "turn_remaining": tool_budget_turn,
                                },
                            },
                        )
                    except Exception as e:  # pragma: no cover
                        prompt_summary["web_fetch_error"] = str(e)
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "error",
                                "meta": {
                                    "reason": str(e),
                                    "ref_remaining": tool_budget_ref,
                                    "turn_remaining": tool_budget_turn,
                                },
                            },
                        )

            # WEB_FETCH for citations using pack-provided URLs when available
            if (
                "missing citations" in final_issues
                and tool_budget_ref > 0
                and tool_budget_turn > 0
                and candidate_urls
            ):
                url_candidate = candidate_urls[
                    min(iteration - 1, len(candidate_urls) - 1)
                ]
                tool_name = "WEB_FETCH"
                if not _tool_allowed(tool_name):
                    _emit(
                        "tool",
                        {
                            "name": tool_name,
                            "status": "blocked",
                            "meta": {"reason": "not_allowed", "url": url_candidate},
                        },
                    )
                elif tool_name in requires_approval and tool_name not in approved_tools:
                    appr_id = _request_approval(tool_name, {"url": url_candidate})
                    if appr_id:
                        _emit(
                            "tool",
                            {
                                "id": appr_id,
                                "name": tool_name,
                                "status": "waiting_approval",
                                "meta": {"url": url_candidate},
                            },
                        )
                        approval_pending = True
                elif _tool_allowed(tool_name):
                    _emit(
                        "tool",
                        {
                            "name": tool_name,
                            "status": "start",
                            "meta": {
                                "url": url_candidate,
                                "ref_remaining": tool_budget_ref,
                                "turn_remaining": tool_budget_turn,
                            },
                        },
                    )
                    try:
                        pol = EgressPolicy(
                            block_private_ip=bool(
                                params.get("egress_block_private_ip", True)
                            ),
                            allow_redirects=int(
                                params.get("egress_allow_redirects", 3)
                            ),
                            max_payload_bytes=int(
                                params.get("egress_max_payload_bytes", 5 * 1024 * 1024)
                            ),
                            enforce_tls=bool(params.get("egress_enforce_tls", True)),
                            allowlist_hosts=tuple(
                                params.get("egress_allowlist_hosts", [])
                            ),
                            denylist_hosts=tuple(
                                params.get("egress_denylist_hosts", [])
                            ),
                        )
                        res = web_fetch(url_candidate, policy=pol)
                        fetch_url = res["url"]
                        fetch_snippet = (res.get("text") or "")[:240]
                        tools_used.append(tool_name)
                        tool_budget_ref -= 1
                        tool_budget_turn -= 1
                        if tool_name in requires_approval:
                            approved_tools.add(tool_name)
                        fetch_meta = dict(res.get("meta", {}))
                        fetch_meta.update(
                            {
                                "ref_remaining": tool_budget_ref,
                                "turn_remaining": tool_budget_turn,
                            }
                        )
                        try:
                            pcn_id = str(uuid4())
                            prov = build_url_provenance(fetch_url)
                            _emit(
                                "pcn",
                                self._pcn.register(
                                    pcn_id, policy={"type": "url"}, provenance=prov
                                ),
                            )
                            _emit("pcn", self._pcn.verify_url(pcn_id, url=fetch_url))
                        except Exception:
                            pass
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "stop",
                                "meta": fetch_meta,
                            },
                        )
                        prompt_summary["web_fetch_meta"] = fetch_meta
                    except PromptInjectionError as pie:
                        meta = pie.to_meta()
                        meta.update(
                            {
                                "ref_remaining": tool_budget_ref,
                                "turn_remaining": tool_budget_turn,
                                "policy_checked": True,
                            }
                        )
                        prompt_summary["web_fetch_blocked"] = meta
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "blocked",
                                "meta": meta,
                            },
                        )
                    except Exception as e:  # pragma: no cover
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "error",
                                "meta": {
                                    "reason": str(e),
                                    "ref_remaining": tool_budget_ref,
                                    "turn_remaining": tool_budget_turn,
                                },
                            },
                        )

            # MATH_EVAL to ground numbers either from fetched snippet or evidence
            if (
                "missing numbers" in final_issues
                and "missing table data" not in final_issues
                and tool_budget_ref > 0
                and tool_budget_turn > 0
            ):
                target_text = fetch_snippet or (
                    context_snips[0] if context_snips else question
                )
                number_candidate = _extract_number(target_text)
                math_expr = (
                    str(number_candidate) if number_candidate is not None else "1+1"
                )
                tool_name = "MATH_EVAL"
                if not _tool_allowed(tool_name):
                    _emit(
                        "tool",
                        {
                            "name": tool_name,
                            "status": "blocked",
                            "meta": {"reason": "not_allowed", "expr": math_expr},
                        },
                    )
                elif tool_name in requires_approval and tool_name not in approved_tools:
                    appr_id = _request_approval(tool_name, {"expr": math_expr})
                    if appr_id:
                        _emit(
                            "tool",
                            {
                                "id": appr_id,
                                "name": tool_name,
                                "status": "waiting_approval",
                                "meta": {"expr": math_expr},
                            },
                        )
                        approval_pending = True
                elif _tool_allowed(tool_name):
                    _emit(
                        "tool",
                        {
                            "name": tool_name,
                            "status": "start",
                            "meta": {
                                "expr": math_expr,
                                "ref_remaining": tool_budget_ref,
                                "turn_remaining": tool_budget_turn,
                            },
                        },
                    )
                    try:
                        pcn_id = str(uuid4())
                        policy = {"tolerance": 0.0}
                        prov = build_math_provenance(math_expr)
                        _emit(
                            "pcn",
                            self._pcn.register(pcn_id, policy=policy, provenance=prov),
                        )
                        math_value = math_eval(math_expr)
                        verify_event = self._pcn.verify_math(
                            pcn_id, expr=math_expr, observed_value=math_value
                        )
                        _emit("pcn", verify_event)
                        pcn_token = f"[PCN:{pcn_id}]"
                        pcn_placeholders.append(pcn_token)
                        verified_value = self._pcn.value_for(pcn_id)
                        pcn_replacements[pcn_token] = (
                            verified_value
                            if verified_value is not None
                            else "[unverified]"
                        )
                        tools_used.append(tool_name)
                        tool_budget_ref -= 1
                        tool_budget_turn -= 1
                        if tool_name in requires_approval:
                            approved_tools.add(tool_name)
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "stop",
                                "meta": {
                                    "expr": math_expr,
                                    "ref_remaining": tool_budget_ref,
                                    "turn_remaining": tool_budget_turn,
                                },
                            },
                        )
                    except Exception as e:  # pragma: no cover
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "error",
                                "meta": {
                                    "reason": str(e),
                                    "ref_remaining": tool_budget_ref,
                                    "turn_remaining": tool_budget_turn,
                                },
                            },
                        )

            # TABLE_QUERY to reconcile governed datasets for numeric answers
            table_sql_override = params.get("table_query_sql")
            table_params = params.get("table_query_params") or []
            if isinstance(table_params, tuple):
                table_params = list(table_params)
            if (
                (
                    "missing table data" in final_issues
                    or (table_sql_override and "missing numbers" in final_issues)
                )
                and tool_budget_ref > 0
                and tool_budget_turn > 0
                and db_path
            ):
                sql_candidate = table_sql_override or _guess_table_sql(
                    question, context_snips
                )
                if sql_candidate:
                    table_sql = sql_candidate
                    tool_name = "TABLE_QUERY"
                    if not _tool_allowed(tool_name):
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "blocked",
                                "meta": {"reason": "not_allowed", "sql": table_sql},
                            },
                        )
                    elif (
                        tool_name in requires_approval
                        and tool_name not in approved_tools
                    ):
                        appr_id = _request_approval(tool_name, {"sql": table_sql})
                        if appr_id:
                            _emit(
                                "tool",
                                {
                                    "id": appr_id,
                                    "name": tool_name,
                                    "status": "waiting_approval",
                                    "meta": {"sql": table_sql},
                                },
                            )
                            approval_pending = True
                    elif _tool_allowed(tool_name):
                        _emit(
                            "tool",
                            {
                                "name": tool_name,
                                "status": "start",
                                "meta": {
                                    "sql": table_sql,
                                    "ref_remaining": tool_budget_ref,
                                    "turn_remaining": tool_budget_turn,
                                },
                            },
                        )
                        try:
                            table_max_rows = int(
                                params.get("table_query_max_rows", 25) or 25
                            )
                            time_limit_ms = int(
                                params.get("table_query_time_limit_ms", 250) or 250
                            )
                            rows = table_query(
                                db_path,
                                table_sql,
                                table_params,
                                max_rows=table_max_rows,
                                time_limit_ms=time_limit_ms,
                            )
                        except Exception as exc:  # pragma: no cover
                            _emit(
                                "tool",
                                {
                                    "name": tool_name,
                                    "status": "error",
                                    "meta": {"reason": str(exc), "sql": table_sql},
                                },
                            )
                        else:
                            table_rows = [tuple(r) for r in rows]
                            table_summary = (
                                "; ".join(
                                    " | ".join(str(v) for v in row)
                                    for row in table_rows[:3]
                                )
                                or "no rows returned"
                            )
                            summary_snip = _summarize(table_summary)
                            context_snips.insert(
                                0, f"SQL[{table_sql}] -> {summary_snip}"
                            )
                            tools_used.append(tool_name)
                            tool_budget_ref -= 1
                            tool_budget_turn -= 1
                            if tool_name in requires_approval:
                                approved_tools.add(tool_name)
                            first_numeric = next(
                                (
                                    val
                                    for row in table_rows
                                    for val in row
                                    if isinstance(val, (int, float))
                                ),
                                None,
                            )
                            pcn_token = None
                            if first_numeric is not None:
                                pcn_id = str(uuid4())
                                prov = build_sql_provenance(table_sql)
                                _emit(
                                    "pcn",
                                    self._pcn.register(
                                        pcn_id,
                                        policy={"tolerance": 0.0},
                                        provenance=prov,
                                    ),
                                )
                                pcn_token = f"[PCN:{pcn_id}]"
                                pcn_placeholders.append(pcn_token)
                                table_numeric = float(first_numeric)
                                verify_event = self._pcn.verify_sql(
                                    pcn_id, value=table_numeric
                                )
                                _emit("pcn", verify_event)
                                verified_value = self._pcn.value_for(pcn_id)
                                pcn_replacements[pcn_token] = (
                                    verified_value
                                    if verified_value is not None
                                    else "[unverified]"
                                )
                                table_summary = (
                                    f"{table_summary} (verified {pcn_token})"
                                )
                            dag_nodes = [
                                {
                                    "id": "sql",
                                    "type": "premise",
                                    "text": f"Executed {table_sql}",
                                    "pcn": pcn_token,
                                },
                                {
                                    "id": "result",
                                    "type": "claim",
                                    "text": f"Returned {len(table_rows)} row(s)",
                                },
                            ]
                            dag_edges = [{"from": "sql", "to": "result"}]
                            gov_ok, failing = evaluate_dag(
                                {"nodes": dag_nodes, "edges": dag_edges},
                                pcn_status=self._pcn.status_for,
                            )
                            ok = gov_ok
                            table_dag_failing = failing if not ok else []
                            _emit("gov", {"dag_delta": {"ok": ok, "failing": failing}})
                            _emit(
                                "tool",
                                {
                                    "name": tool_name,
                                    "status": "stop",
                                    "meta": {"rows": len(table_rows), "sql": table_sql},
                                },
                            )

            if approval_pending:
                trace.append(
                    {
                        "step_index": iteration,
                        "is_refinement": True,
                        "s1_or_snne": final_s1,
                        "s2": final_s2,
                        "final_score": final_S,
                        "cp_accept": False,
                        "issues": final_issues,
                        "tools_used": tools_used,
                        "change_summary": "approval pending",
                        "action": "iterate",
                        "reason": "approval_pending",
                        "latency_ms": 0,
                        "usage": {},
                    }
                )
                return {
                    "final": final_answer + " [approval pending]",
                    "stop_reason": "approval_pending",
                    "uncertainty": {
                        "mode": "snne",
                        "snne": final_s1,
                        "snne_raw": final_raw_snne,
                        "snne_sample_count": len(final_samples)
                        if final_samples
                        else None,
                        "s2": final_s2,
                        "final_score": final_S,
                        "cp_accept": False,
                        "prediction_set_size": None,
                    },
                    "trace": trace,
                    "pack_used": pack_used,
                    "usage": {},
                    "pending_approvals": iteration_pending,
                }

            issues_remaining = list(final_issues)
            if fetch_url and "missing citations" in issues_remaining:
                issues_remaining.remove("missing citations")
            if math_value is not None and "missing numbers" in issues_remaining:
                issues_remaining.remove("missing numbers")
            if table_rows and "missing table data" in issues_remaining:
                issues_remaining.remove("missing table data")
            if table_numeric is not None and "missing numbers" in issues_remaining:
                issues_remaining.remove("missing numbers")

            prompt_summary.update(
                {
                    "fetch_url": fetch_url,
                    "fetch_snippet": fetch_snippet,
                    "math_expr": math_expr,
                    "math_value": math_value,
                    "table_sql": table_sql,
                    "table_summary": table_summary,
                    "table_params": table_params,
                }
            )

            iteration_context = list(context_snips)
            if fetch_snippet:
                iteration_context.insert(0, fetch_snippet)
            if table_summary:
                iteration_context.insert(0, f"TABLE_QUERY: {table_summary}")

            def _pcn_text() -> str | None:
                if not pcn_placeholders:
                    return None
                if len(pcn_placeholders) == 1:
                    return pcn_placeholders[0]
                return ", ".join(pcn_placeholders)

            refined_answer = build_refined_answer(
                question=question,
                previous_answer=final_answer,
                issues_remaining=issues_remaining,
                context_snippets=iteration_context,
                fetch_url=fetch_url,
                math_value=math_value,
                math_text=_pcn_text(),
                table_text=table_summary,
            )
            if fetch_snippet:
                context_snips = iteration_context[:3]
            elif table_summary:
                context_snips = iteration_context[:3]

            gov_failures: List[str] = []
            if (
                fetch_url is None
                and "missing citations" in final_issues
                and "missing citations" in issues_remaining
            ):
                gov_failures.append("missing_citation_provenance")
            if (
                math_value is None
                and "missing numbers" in final_issues
                and "missing numbers" in issues_remaining
            ):
                gov_failures.append("missing_pcn_verification")
            if table_dag_failing:
                gov_failures.extend(table_dag_failing)

            if snne_mode == "snne":
                try:
                    new_samples = generate_answer_variants(
                        refined_answer,
                        question=question,
                        evidence_snippets=context_snips,
                        count=sample_count,
                    )
                    new_raw_snne = snne_score(
                        new_samples, snne_tau, embed=lambda text: embed_text(text)
                    )
                    if snne_calibrator:
                        new_s1 = snne_calibrator.normalize(
                            domain=domain, raw=new_raw_snne
                        )
                    else:
                        new_s1 = snne_normalize(new_raw_snne)
                except Exception:
                    new_s1 = final_s1
                    new_raw_snne = final_raw_snne
                    new_samples = final_samples
            else:
                new_s1 = final_s1
                new_raw_snne = final_raw_snne
                new_samples = final_samples

            _emit(
                "uq",
                {
                    "mode": snne_mode,
                    "raw": new_raw_snne,
                    "normalized": new_s1,
                    "samples": new_samples[: min(len(new_samples), 3)],
                    "iteration": iteration,
                },
            )

            new_s2, new_issues, new_needs_fix = self._verifier.verify(
                question, refined_answer
            )
            new_S = final_score(snne_norm=new_s1, s2=new_s2, cfg=self._cfg)
            new_cp_ok = self._cp.accept(new_S)
            if (
                not new_cp_ok
                and getattr(self._cp, "last_reason", None) == "missing_tau"
            ):
                if "cp_missing_calibration" not in new_issues:
                    new_issues = list(new_issues) + ["cp_missing_calibration"]
            new_action = decide(new_S, self._cfg, new_cp_ok)
            if gov_failures:
                _emit("gov", {"dag_delta": {"ok": False, "failing": gov_failures}})
                for token in pcn_placeholders:
                    if token not in pcn_replacements:
                        pcn_replacements[token] = "[unverified]"
                if "governance" not in new_issues:
                    new_issues = list(new_issues) + ["governance"]
            _emit(
                "score",
                {
                    "s1": new_s1,
                    "s2": new_s2,
                    "final_score": new_S,
                    "cp_accept": new_cp_ok,
                },
            )

            resolved = [i for i in final_issues if i not in new_issues]
            change_parts: List[str] = []
            if resolved:
                change_parts.append(f"resolved: {', '.join(resolved)}")
            if tools_used:
                change_parts.append(f"tools: {', '.join(tools_used)}")
            if fetch_url:
                change_parts.append(f"source: {fetch_url}")
            if math_value is not None:
                change_parts.append(f"calc: {math_value}")
            if table_rows:
                change_parts.append(f"table_rows: {len(table_rows)}")
            if table_numeric is not None:
                change_parts.append(f"table_calc: {table_numeric}")
            change_summary = "; ".join(change_parts) if change_parts else "no changes"

            _emit(
                "trace",
                {
                    "step": iteration,
                    "is_refinement": True,
                    "issues": new_issues,
                    "tools_used": tools_used,
                    "prompt_summary": prompt_summary,
                    "change_summary": change_summary,
                    "scores": {"s1": new_s1, "s2": new_s2, "S": new_S},
                    "evidence": context_snips,
                },
            )

            trace.append(
                {
                    "step_index": iteration,
                    "is_refinement": True,
                    "s1_or_snne": new_s1,
                    "s2": new_s2,
                    "final_score": new_S,
                    "cp_accept": new_cp_ok,
                    "issues": new_issues,
                    "tools_used": tools_used,
                    "change_summary": change_summary,
                    "action": new_action,
                    "reason": "refined iteration",
                    "latency_ms": 0,
                    "usage": {},
                }
            )

            refined = True
            final_answer = refined_answer
            final_s1 = new_s1
            final_raw_snne = new_raw_snne
            final_samples = new_samples
            final_s2 = new_s2
            final_S = new_S
            final_cp_ok = new_cp_ok
            final_action = new_action
            final_issues = new_issues
            final_needs_fix = new_needs_fix
            if not tools_used and not resolved:
                # Avoid infinite loops when no progress can be made
                final_action = "abstain"
                final_cp_ok = False
                break

        final_answer = _resolve_pcn_placeholders(final_answer, pcn_replacements)
        final_text = final_answer + (" [refined]" if refined else "")

        usage_info = {
            "llm_tokens_estimate": len(llm_meta.get("tokens", []))
            if isinstance(llm_meta.get("tokens"), list)
            else None,
            "llm_mode": llm_meta.get("mode"),
            "llm_model": llm_meta.get("model"),
        }
        tokens_meta = llm_meta.get("tokens")
        if isinstance(tokens_meta, (list, tuple)):
            usage_info["llm_tokens"] = [str(tok) for tok in tokens_meta]
        usage_clean = {k: v for k, v in usage_info.items() if v is not None}

        return {
            "final": final_text,
            "stop_reason": "accept" if final_action == "accept" else final_action,
            "uncertainty": {
                "mode": "snne",
                "snne": final_s1,
                "snne_raw": final_raw_snne,
                "snne_sample_count": len(final_samples) if final_samples else None,
                "s2": final_s2,
                "final_score": final_S,
                "cp_accept": final_cp_ok,
                "prediction_set_size": None,
            },
            "trace": trace,
            "pack_used": pack_used,
            "usage": usage_clean,
        }
