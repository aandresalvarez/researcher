
**Beyond‑SOTA Roadmap (Pydantic AI only — No LangGraph, No Agents SDK)**

Progress (current branch)
- Claim‑level faithfulness module implemented with metrics and Prometheus export.
- Selective planning (borderline/always) skeleton integrated; SSE planning events; metrics exported.
  - New settings: planning_enabled, planning_mode, planning_budget, planning_when.
  - Tests added: planning event emission and faithfulness scoring basics.
- Planning now supports a simple "beam" mode (heuristic) in addition to one-shot ToT sampling.
- Faithfulness integrated into agent issues: low score adds "unsupported claims" to drive refinement.
- Added small benchmark suites (HPQA-S, STRAT-S, MMLU-S) and a CSV eval report script.
- Guardrails adapter with metrics; MCP adapters with metrics.
- Memory promotion; PDF table extraction; retrieval boosts for tabular questions.
- Formal checks: units (PCN) + SQL checks (min/max/non_negative/monotonic) with metrics.
- GoV assertions (no_cycles, no_pcn_failures, no_dependency_failures, all_claims_supported, max_depth, path_exists, types_allowed) with metrics.
- Flujo PlanningNode.
- Pyproject extras (tables/units/formal) and README updated.

Status: Core roadmap items are complete. Remaining opportunities are documentation polish and optional richer assertions.

This plan upgrades UAMM to a state‑of‑the‑art, production‑ready agent stack while staying within this repo’s architecture and constraints:

- Orchestration remains in `src/uamm/agents/` and `src/uamm/flujo/`.
- Pydantic AI is the first‑class agent interface (no LangGraph, no external Agents SDK).
- Model Context Protocol (MCP) integration is implemented using Pydantic AI’s native MCP support.
- All features are offline‑friendly by default, with LLM‑backed behavior behind graceful fallbacks.


**Principles**
- Tests first, offline by default; optional online paths are dependency‑guarded.
- Keep safety defaults: read‑only SQL, egress/IP guards, approvals.
- Minimal invasive changes; follow existing naming/style; add typed hints everywhere.
- Add metrics and docs for every new feature.


**Milestones**
- Completed: Claim faithfulness, selective planning (ToT/Beam/MCTS), eval pack + metrics, MCP adapters.
- Completed: Guardrails adapter, memory promotion, PDF table extraction.
- Completed: Formal checks (units + SQL checks), assertions in /gov/check, report polish.


**Cross‑Cutting Tasks**
- Completed: pyproject extras, config toggles, observability counters, and README extensions.


**1) Selective Planning/Search (ToT/Beam/MCTS) via Pydantic AI**
- Goal
  - Use compute only when uncertainty is borderline; improve multi‑step accuracy at same CP risk.
- Code
  - Add `src/uamm/planning/strategies.py`
    - `class Planner`: entry point with `run(question, evidence_pack, budget, mode, heuristics)`.
    - Modes: `"off" | "tot" | "beam" | "mcts"`.
    - Uses Pydantic AI agent to sample candidate thoughts (LLM optional), with deterministic heuristic fallback.
    - Node scoring uses existing SNNE (S₁) + Verifier (S₂) signals.
  - Integrate into `src/uamm/agents/main_agent.py`
    - New params: `planning_mode`, `planning_budget`, `planning_when` (e.g., `iterate` only or `(tau_accept - delta) ≤ S < tau_accept`).
    - Emit `planning` SSE events with step summaries and chosen branch.
    - Respect existing tool budgets when planner proposes tool‑calls.
  - Add `src/uamm/flujo/nodes.py`: `PlanningNode` thin wrapper over `Planner`.
- Config
  - `planning_enabled: bool`, `planning_mode: str`, `planning_budget: int`.
  - `planning_when: {"borderline_only": true}`.
- Tests
  - `tests/test_planning_tot.py`: deterministic generator; ensures higher success on synthetic multi‑step tasks vs single‑pass.
  - `tests/test_planning_budget.py`: respects `planning_budget` and tool budgets.
- Metrics
  - Counters: `uamm_planning_runs_total`, `uamm_planning_improvements_total`.
- Acceptance
  - Improves `Refine-F1` and a new `Plan-H1` suite (>X% resolved issues at fixed CP coverage), without exceeding latency budget.


**2) Claim‑Level Faithfulness & Attribution**
- Goal
  - Flag unsupported claims and measure faithfulness beyond numeric PCN.
- Code
  - Add `src/uamm/verification/faithfulness.py`
    - `extract_claims(answer)`: sentence segmentation + simple filtering.
    - `align_claims_to_evidence(claims, evidence_pack)`: lexical/NER overlap baseline.
    - Optional LLM NLI via Pydantic AI: `entailment(claim, evidence)` with graceful fallback.
  - Integrate into `src/uamm/agents/verifier.py`
    - Add `faithfulness_score` and `unsupported_claims` to issues when evidence is missing.
  - Expose endpoint
    - `/metrics`: include `faithfulness` averages and unsupported claim counts.
    - `/metrics/prom`: gauges `uamm_faithfulness_score`, `uamm_unsupported_claims_total`.
- Config
  - `faithfulness_enabled: bool`, `faithfulness_use_llm: bool`.
- Tests
  - `tests/test_faithfulness.py`: verifies unsupported claims are detected and metric increments.
- Acceptance
  - Faithfulness improves ≥X% on doc‑heavy eval subset at similar latency.


**3) MCP Server (Pydantic AI MCP)**
- Goal
  - Expose UAMM tools and agent via MCP using Pydantic AI’s MCP primitives; keep approvals/hardening.
- Code
  - Add `src/uamm/mcp/server.py`
    - Use Pydantic AI MCP registry to expose tools: `WEB_SEARCH`, `WEB_FETCH`, `MATH_EVAL`, `TABLE_QUERY` with safe wrappers.
    - Provide a `uamm.answer` MCP command that mirrors `/agent/answer` behavior and emits progress as MCP events/messages.
    - Bridge approvals: if a tool requires approval, return a structured MCP response with `needs_approval` and `approval_id`; client can poll `/tools/approve` or a corresponding MCP status method.
  - Add CLI `scripts/mcp_server.py` to run the Pydantic AI MCP server.
- Config
  - `mcp_enabled: bool`, `mcp_bind: host`, `mcp_port: int`, `mcp_tools_expose: list[str]`.
- Tests
  - `tests/test_mcp_registry.py`: discovers tools, denies disallowed ones, round‑trip a simple `uamm.answer` over MCP.
- Acceptance
  - Tools discoverable and invocable from Pydantic AI MCP client; approval pause/resume works end‑to‑end.


**4) Guardrails Adapter (Optional, Heuristic Fallback)**
- Goal
  - Complement CP/UQ/PCN with dialog/policy guardrails and jailbreak defenses (no LangGraph).
- Code
  - Add `src/uamm/security/guardrails.py`
    - `pre_guard(input)`, `post_guard(answer)`: run NeMo Guardrails if installed; otherwise heuristic regex/pattern checks.
  - Integrate in `src/uamm/agents/main_agent.py`
    - Apply `pre` before generation/refinement; `post` after compose; add issues on violation; can force `iterate/abstain`.
- Config
  - `guardrails_enabled: bool`, `guardrails_config_path: str | None`.
- Tests
  - `tests/test_guardrails_stub.py`: verifies heuristic guard fires and adds issues; respects toggle.
- Acceptance
  - Measurably reduces unsafe response rate in a small harmful‑prompt suite without harming CP coverage.


**5) Benchmark Harness & Head‑to‑Head Reports**
- Goal
  - Prove superiority vs threshold/react baselines at fixed CP risk.
- Code
  - Extend `src/uamm/evals/`
    - New suites: `evals/hotpotqa_small.json`, `evals/strategyqa_small.json`, `evals/mmlu_small.json` (small offline subsets).
    - Update `src/uamm/evals/suites.py` to add suites with CP enabled.
    - Add metrics: faithfulness, tool‑call counts, token/cost estimates; persist in `eval_runs`.
  - Add `scripts/eval_report.py`
    - Compares UAMM vs baselines: `baseline=react_like` (planning off, CP off) and `baseline=cp_only` (planning off, CP on).
    - Emits CSV/Markdown with EM/accuracy, faithfulness, cost/latency, tool calls.
- Tests
  - `tests/test_eval_metrics_extended.py`: validates new aggregations and persistence.
- Acceptance
  - Report shows improved accuracy and lower tool calls at matched CP coverage.


**6) Typed Memory & Promotion; Table/Chart Grounding**
- Goal
  - Make memory useful over time; add better grounding from PDFs.
- Code
  - Schema
    - Reuse `memory.key` conventions; adopt prefixes: `episodic:`, `semantic:`, `procedural:`.
    - Add index on `memory(key, ts)` already present; no breaking change.
  - Add `src/uamm/memory/promote.py`
    - `promote_episodic_to_semantic(records)`: summarize repeated facts into compact entries (Pydantic AI optional, heuristic fallback).
    - TTL/promotion worker hooked into existing background task.
  - Add `src/uamm/rag/ingest_tables.py`
    - Extract tables from PDFs via `pdfplumber` or `camelot` (optional extra); normalize units; store into `corpus` with `meta={"table":true}`.
  - Retrieval
    - Update `src/uamm/rag/retriever.py` to optionally boost table hits for analytics queries.
- Config
  - `memory_promotion_enabled: bool`, `memory_promotion_min_support: int`.
  - `docs_tables_enabled: bool`.
- Tests
  - `tests/test_memory_promotion.py`: episodic→semantic summarization.
  - `tests/test_pdf_tables.py`: basic table extraction path (skip if deps missing).
- Acceptance
  - Repeated questions show lower latency/tool calls and higher faithfulness on table‑heavy cases.


**7) Formal/Executable Checks (Units, Constraints, SMT Optional)**
- Goal
  - Reduce critical errors with executable validation beyond heuristics.
- Code
  - Units
    - Add `src/uamm/pcn/units.py`: parse and validate units with `pint`; integrate into `PCNVerifier` paths (math/sql/url) and show `[PCN:...] (units ok)`.
  - SQL Property Checks
    - Add `src/uamm/pcn/sql_checks.py`: simple policy‑as‑code assertions for cohort counts, monotonicity, bounds; attach failures to S₂ issues.
  - Assertions DSL (optional SMT)
    - Add `src/uamm/policy/assertions.py`: declare small JSON/YAML constraints; evaluate with Python checks; if `z3-solver` present, add SMT checks for numeric properties.
  - API
    - Extend `/gov/check` to accept an optional `assertions` list and return per‑assertion pass/fail.
- Config
  - `pcn_units_enabled: bool`, `pcn_sql_checks_enabled: bool`.
  - `assertions_enabled: bool`.
- Tests
  - `tests/test_pcn_units.py`, `tests/test_sql_checks.py`, `tests/test_policy_assertions.py` (SMT tests skipped if z3 not installed).
- Acceptance
  - Near‑zero numeric/unit errors on analytics suites; audit trail links to pass/fail.


**8) Approvals & Pause/Resume Over Pydantic AI Tools**
- Goal
  - First‑class pause/resume semantics using existing `ApprovalsStore` without LangGraph.
- Code
  - Wrap tools with a Pydantic AI MCP‑compatible `Tool` adapter that checks `tools_requiring_approval`; if pending, return structured `waiting_approval` status.
  - In streaming path, keep `pending_approval_id` and resume automatically when `/tools/approve` returns approved; already partially implemented in `src/uamm/api/routes.py` — finalize and cover edge cases.
- Tests
  - `tests/test_streaming_pause_resume.py`: simulate approval flows over SSE.
- Acceptance
  - Robust end‑to‑end approvals with resume and correct metrics/alerts.


**Config Keys (Additions)**
- Implemented: Planning, faithfulness, MCP, guardrails, memory promotion, docs_tables_enabled, token_cost_per_1k.


**Metrics (Prometheus)**
- Implemented: Planning, Faithfulness, MCP, Guardrails, Memory promotions, SQL checks, Units checks, GoV assertions.


**Test Plan**
- Keep all new tests offline‑first; skip external deps gracefully.
- Add fixtures in `tests/data/` for faithfulness, tables, and planning.
- Update CI to run `make test && make lint && make typecheck`.


**Deliverables Checklist**
- All code modules added and integrated.
- Settings keys added; defaults safe.
- Metrics exposed; Prometheus and JSON endpoints updated.
- Eval suites/report updated with tools/faithfulness/planning/tokens/cost.
- README covers Planning, Faithfulness, MCP, Guardrails, Formal Checks, Memory Promotion, Tables.
- Tests and type checks present; more can be added as needed.


**Out of Scope**
- LangGraph and multi‑agent debate remain out of scope per request.
- Full formal proof assistants (Lean/Isabelle) — we provide a minimal SMT on‑ramp only.
