# UAMM PRD v1.3 — Implementation Tracker

Purpose: actionable, milestone-aligned checklist to implement PRD.MD (v1.3). Keep this up to date during execution. Tie each item to PRD sections, define owners, status, and dependencies.

Conventions
- Status: [ ] Todo, [x] Done, [~] In progress, [!] Blocked (note reason)
- Fields per item (optional): Owner, ETA, Deps, Notes, PRD Ref
- Update cadence: daily during active sprints; after merges

---

## 0) Foundations & Project Setup

- [x] Repo scaffolding: Python 3.14, FastAPI skeleton, Pydantic v2 models (PRD §18)
  - Owner: TBA | ETA: TBA | Deps: none
- [x] Config system: env + YAML, feature flags (UQ mode, CP gate, FAISS/LanceDB, refinement) (PRD §18)
  - Owner: TBA | ETA: TBA | Deps: scaffolding
- [x] Secrets management via vault; local dev .env excluded from VCS (PRD §11)
  - Owner: TBA | ETA: TBA | Deps: scaffolding

---

## 1) M1 (2–3 wks) — Core Agent + Safety Baseline

- [x] Streaming agent skeleton: stream initial answer; delta after refinement (PRD §7.1, §5)
  - Deps: scaffolding
  - Notes: initial answer now grounded in top pack snippet with SNNE heuristics driving the first-step score.
- [x] UQ: SNNE minimal (n=3–5), embeddings cache, API (PRD §7.2, §9.1)
  - Deps: model client, embedder
  - Notes: main agent now generates heuristic paraphrases, normalizes SNNE with logistic squashing, and emits calibration-ready `uq` traces.
- [x] Verifier S₂: JSON schema-enforced judge with retry on malformed output (PRD §7.3)
  - Deps: model client
- [x] Policy: static `τ_accept` + `δ`; accept/refine/abstain decisions (PRD §7.4, §9.2)
  - Deps: SNNE, S₂
- [x] Extrinsic refinement loop with budgets; issues surfaced into prompt (PRD §7.5, §9.3)
  - Deps: Tools registry
  - Notes: Multi-iteration loop honors per-refinement/turn budgets, runs WEB_SEARCH/FETCH/MATH_EVAL with approval hooks, emits detailed traces/UQ deltas, and composes refined answer summaries.
- [x] Tools registry: WEB_SEARCH, WEB_FETCH, MATH_EVAL, TABLE_QUERY; budgets (PRD §7.6)
  - Deps: Security policies §11.2/§11.3
- [x] Hybrid RAG: BM25 + dense (E5/BGE), filter, pack format (PRD §7.7)
  - Deps: vector index, embedder
  - Notes: Hybrid retriever now merges memory/corpus with sparse+dense scoring, dedupe, and URL provenance surfaced to packs.
- [x] Memory: SQLite schema; pack curation stub; TTL hooks TBD (PRD §8.1, §7.7)
  - Deps: DB
- [x] FAISS adapter (opt-in) parity tests (PRD §5, §18)
  - Deps: vector store
  - Notes: Added optional FAISS adapter with numpy fallback and parity tests (`tests/test_retriever.py::test_retrieve_faiss_parity`, `tests/test_faiss_adapter.py`).
- [x] PHI/PII redaction pre-save & pre-return; ensure `steps.question/answer` are redacted (PRD §11.1, §8.1)
  - Deps: DB, logger
  - Notes: API redaction + logging smoke test (`tests/test_api_redaction.py`) ensures persistence/log streams remain masked.
- [x] Logging: per-step JSON trace (no PHI); correlate with request IDs (PRD §12)
  - Deps: scaffolding
- [x] Basic REST endpoints `/agent/answer`, memory ops (PRD §10.2)
  - Deps: above
- [x] CI smoke: unit + smoke checks (health/metrics) (PRD §13.3, §17)
  - Deps: tests
  - Notes: CI workflow now runs Ruff lint + pytest coverage on PRs, publishes coverage artifacts, executes `UQ-A1`/`Stack-G1` smoke evals on push, and nightly full-suite evals (artifacts uploaded); deploy job gated by `vars.DEPLOY_ENABLED`.

M1 Exit Criteria
- [x] First token ≤700ms P50 on internal bench (PRD §5)
  - Notes: 30-run streaming bench via `TestClient` (`first_token_ms_p50≈0.27s`, metrics `first_token_latency` histogram/count=30) meets target; histogram now exposed on `/metrics`.
- [x] P50 ≤2.5s, P95 ≤6s with `max_refinements=2` (PRD §5)
  - Notes: Same bench observed total latency `P50≈0.27s`, `P95≈0.27s`; metrics `latency` summary updated accordingly.
- [x] Borderline triggers ≤2 refinements; budgets enforced (PRD §7.5)
  - Notes: Regression coverage via `tests/test_refinement_budget.py`.
- [x] Redaction verified; no PHI in DB/logs (PRD §11.1, §17.4)
  - Notes: API integration test validates DB/log redaction (`tests/test_api_redaction.py`).
- [x] Smoke evals green; logs/metrics present (PRD §13.3, §12)
  - Notes: Full pytest suite (51 tests) passing locally; metrics endpoints covered by `tests/test_metrics_json.py`.

---

## 1.5) M1.5 (1–2 wks) — CP Bootstrap

- [~] Collect calibration set with gold labels by domain (PRD §10, §13)
  - Notes: CLI `scripts/import_cp_artifacts.py` ingests CSV/JSON calibration runs; sourcing gold labels still pending.
- [~] Implement CP gate `cp_accept(S)` with bootstrap calibration (PRD §7.4, §7.10)
 - [x] Integrate per-domain (Mondrian) CP wiring (PRD §7.12)
  - [x] Domain-aware τ supplier wired; agent uses `domain` from request; auto-enable CP per domain when τ exists; /cp/stats and per-domain evals
- [x] Dashboards: acceptance, false-accept among accepted, latency (PRD §12)
  - Notes: `/dashboards/summary` aggregates latency, acceptance mix, CP stats, and alert state for UI dashboards.
- [ ] Drift detection hooks for SNNE normalization (PRD §7.2)

M1.5 Exit Criteria
- [ ] False-accept among accepted ≤ target on held-out (PRD §4, §7.10)
- [ ] CP decisions logged; coverage tracked per domain (PRD §12)

---

## 2) M2 (2–3 wks) — Full Evals, Tuner, PCN/GoV

- [ ] Full eval suites: UQ-A1, CP-B1, RAG-C1, PCN-D1, GoV-E1, Refine-F1, Stack-G1; nightly (PRD §13)
- [~] Tuner Agent: propose τ/δ, w1/w2, SNNE params, RAG K, budgets; canary + approvals (PRD §7.11, §14)
-   - Added heuristics-based tuner with suite-driven canary runs, approval store, and config patch application (`/tuner/propose`, `/tuner/apply`). Further tuning for w1/w2 + RAG budgets pending.
- [x] Streaming API (SSE): event schema, idempotency, cancellation, error model (PRD §10.3)
  - Added: ready/token/final/error, score/trace/tool, heartbeats, request_id on all events
- [~] PCN strict gating: pending/verified/failed events; provenance (PRD §7.8)
  - [x] PCN placeholders now resolve to verified values or `[unverified]` in both REST and SSE responses; provenance events retained.
  - [ ] Renderer badges & UI polish still outstanding.
- [~] GoV: DAG verification pipeline with failure propagation to S₂ issues (PRD §7.9)
  - [x] Gov events emitted when refinements fail risk checks; issues flagged with `governance`.
  - [ ] Full DAG validation still pending.
- [x] RAG filter agent; KG feature hooks (PRD §7.7)
- [x] LanceDB plugin (optional) (PRD §18)
  - Configurable backend (`vector_backend=lancedb`), LanceDB adapter with upsert/search, API/ingest hooks keep index warm.

M2 Exit Criteria
- [ ] CP full calibration on adequate sample size; monitored drift (PRD §7.10)
- [ ] PCN verified numbers rendered with badges; fail-closed semantics (PRD §7.8)
- [ ] SSE passes load test at target concurrency; proper error semantics (PRD §10.3)
  - Notes: SSE event schema implemented (ready/token/final/error + score/trace/tool + heartbeats; request_id included)

---

## 3) M3 (2 wks) — Governance & Portability

- [ ] RBAC UI for TABLE_QUERY domains; audit export (PRD §11, §15)
- [x] Flujo nodes for components; Pydantic I/O contracts (PRD §19)
- [ ] Formal state checks (LTL + constrained decoding) on selected flows (PRD §15)

M3 Exit Criteria
- [x] Flujo orchestration parity with SDK; policies in YAML (PRD §19)
- [ ] Governance workflows audited and exportable (PRD §11)

---

## Security & Privacy (Cross-cutting)

- [~] WEB_FETCH/SEARCH egress policy (IP/DNS blocks, TLS, limits) (PRD §11.2)
- [~] SQL AST allowlist; RBAC; rate & time/row limits (PRD §11.3)
  - [x] SELECT-only; per-table allowlist; max_rows/time caps; per-table rate limiting; structured JSON errors with request_id
  - [x] Hardened guard: blocks semicolons/comments/CTEs/UNION; domain-aware per-table allowlist enforced.
- [x] Prompt-injection mitigations for tools; safe errors (PRD §17.4)
- [ ] Data residency: on-prem SQL; no PHI to external APIs (PRD §11)
- [ ] Backups & retention for SQLite/FAISS; secure deletion (PRD §12)

  - [x] Added `scripts/backup_sqlite.py` to snapshot DB and prune `cp_artifacts` (90-day retention).
---

## Observability & Ops

- [~] Metrics: accept/iterate/abstain, false-accept among accepted, SNNE/S₂ histograms, p50/p95, tokens, RAG Recall@K, PCN pass-rate, tool usage (PRD §12)
-   - Added: JSON `/metrics` now includes `rates` and `rates_by_domain`.
-   - Added: CP drift + approvals backlog surfaced via `/metrics` (`alerts`) and Prometheus gauges (`uamm_cp_false_accept_rate`, `uamm_approvals_*`, `uamm_alert_*`); SNNE quantile deltas tracked from `cp_reference` baselines.
- [ ] Structured logs per step; no PHI; request IDs (PRD §12)
- [ ] Alerts: spikes in false-accept, p95, abstentions, incomplete (PRD §12)
-   - CP, latency (p95), abstain, and approvals alerts exposed via `/metrics` + Prometheus; incomplete-action alert still pending.
- [ ] Scaling plan: horizontal workers, embedding cache, pre-embed jobs (PRD §12)
 - [x] Steps report endpoint (recent steps with domain, action, S/CP, change_summary, pack_ids) (PRD §12)
 - [x] Prometheus metrics endpoint `/metrics/prom` with domain labels (PRD §12)

## Observability & Ops (Additions)

- [x] Health endpoint reports DB readiness (PRD §12)

---

## Data Model & Storage

- [x] Apply SQLite schema (memory, steps); mark redacted columns; indices (PRD §8.1)
- [ ] TTL/retention jobs; partition/archive `steps` (PRD §11.1, §12)
  - [x] Background TTL cleaner for steps/memory (configurable days)
- [x] Vector store: sqlite-vec baseline; FAISS adapter; LanceDB plugin (PRD §5, §18)
  - LanceDB index now powers dense recall when enabled; ingestion routes sync embeddings on write, retriever merges LanceDB hits.

## Memory & Retrieval

- [x] Memory add/search API backed by SQLite; FTS5 best-effort acceleration (PRD §7.7, §8.1)
- [x] Memory pack builder returns `MemoryPackItem[]` (PRD §8.2)
- [x] RAG corpus ingestion + BM25/FTS baseline search endpoints (PRD §7.7)
 - [x] Pack merge endpoint combines memory + corpus with dedupe and thresholds (PRD §7.7)
 - [x] Attach pack IDs to steps; honor `memory_budget` in answering (PRD §7.7)

## Streaming & Policy

- [x] Accept/iterate policy loop skeleton (single pass; action computed; trace recorded)
- [~] Tool-driven refinement loop with budgets (PRD §7.5) — WEB_SEARCH/WEB_FETCH/MATH_EVAL; SSE tool events; per-refinement/turn budgets enforced
- [x] CP bootstrap artifacts + Mondrian thresholds (PRD §7.10, §7.12)
-   Notes: `/cp/artifacts` ingests calibration sets, caches τ per domain, and Prometheus now exposes SNNE aggregates for calibration drift tracking.
- [x] Refinement prompt integrates tool outputs and issues to evolve answer (PRD §7.5)
  - [x] Prompt template builder with pack snippets, fetch snippet/url, math value; preview emitted via SSE
  - [x] Trace includes change summary (resolved issues, used tools, source/calc) and top evidence snippets
- [x] Persist richer trace deltas to DB (optional structured JSON field) (PRD §7.1)

## Verifier

- [x] Integrate S₂ light verifier to produce issues and drive refinement attempts (PRD §7.3)

## Tools

- [x] MATH_EVAL sandboxed evaluator (PRD §7.6)
- [x] TABLE_QUERY read-only guard on SQLite (PRD §7.6, §11.3)
  - Notes: Refinement loop now issues governed `TABLE_QUERY` calls with provenance, PCN verification, and GoV tracing when table data is required.
- [x] WEB_FETCH: expand allow/deny lists, add response size/type surfacing to trace (PRD §11.2)

---

## Testing & QA

- [~] Unit: DH-001..007, VR-001, MA-001..005, TL-001..002, SD-001..003, EV-001..002 (PRD §17.1)
- [ ] Expand eval datasets (e.g., CP-B1-EXT) and capture expected outputs for analytics/governance cases (PRD §17.5)
- Added: Redaction masking test; metrics JSON smoke test (baseline).
- [ ] Integration: I-001..011 (PRD §17.2)
- [ ] Load & Perf: L-001..004 (PRD §17.3)
-   - Added: `scripts/load_smoke.py` harness to exercise `/agent/answer` with configurable concurrency and p95 gates.
- [ ] Security: S-001..S-003 (PRD §17.4)
- [ ] Gold acceptance set (30–50 cases) (PRD §17.5)

---

## API Surface & Approvals

- [x] REST `/agent/answer`, memory ops, evals, reports (PRD §10.2)
  - OpenAPI now published at `/docs` and `/redoc` with request/response models, SSE examples, and endpoint docstrings.
- [x] SSE endpoint + event schema (PRD §10.3)
- [x] Tool approvals pause/resume flow (PRD §10.4)
  - Approvals store now drives pause/resume: blocking tools emit approval IDs, API honors `X-Approval-ID`, and SSE waits then resumes once approved.

---

## Performance & Cost Controls

- [ ] Caching: embeddings, RAG results; eviction policy (PRD §5, §12)
- [ ] Degrade modes: disable refinement on overload; reduce SNNE samples; sparse-only RAG (PRD §5, §12)
- [ ] Cost guardrails: K defaults, token budgets, tool budgets (PRD §5, §17.3)

---

## Documentation

- [x] API reference (Python SDK + REST + SSE) (PRD §10)
  - FastAPI Swagger/Redoc enabled with descriptions, examples, and SSE schema notes; README points to the live docs.
- [ ] Security & compliance notes (PRD §11)
- [ ] Operational runbook (alerts, dashboards, backups) (PRD §12)
- [ ] Migration guide for FAISS/LanceDB swaps (PRD §18)
- [x] Flujo integration guide (PRD §19)
 - [x] SSE event schema and examples (README/PRD alignment)
 - [ ] CP per-domain workflow and demo eval instructions

---

## Risk Tracking

- [ ] SNNE cost/latency: cache, n=5 default; fallback n=3 (PRD §16)
- [~] Over-abstain risk: Tuner, realistic targets, UX for missing evidence (PRD §16)
-   - Tuner proposals now adjust `accept_threshold`/`borderline_delta` based on accept/abstain metrics; UX improvements remain.
- [ ] Verifier bias: second judge/rules on high-stakes; monitor disagreements (PRD §16)
- [ ] RAG noise: filter stage, citation spans, KG (PRD §16)
- [ ] Tool latency: budgets, parallel fetch, timeouts (PRD §16)

---

## Backlog / Nice-to-haves

- [ ] NLI entailment on high-stakes UQ route (PRD §7.2)
- [ ] Cross-encoder reranker for RAG (PRD §7.7)
- [ ] Domain-specific PCN policy templates (PRD §7.8)

- [x] LanceDB integration plan: design adapter, parity tests, migration guide (PRD §5/§18)
  - Done: adapter + helper utilities landed; follow-up doc section pending under Documentation backlog.
- [ ] Embedding/cache strategy: shared vector cache, eviction policy, degrade modes (PRD §5/§12)
### PCN & GoV Completion Plan
- [ ] Design PCN verification services including provenance storage, renderer callbacks, and failure handling (PRD §7.8).
  - Owner: AI Safety | ETA: 3d | Deps: tool outputs, renderer contract
  - Notes: implement verification queue, passthrough to renderer, structured logs; update SSE `pcn` events.
- [ ] Implement GoV DAG execution with premise verification and failure propagation (PRD §7.9).
  - Owner: Reasoning | ETA: 3d | Deps: PCN service, retriever evidence
  - Notes: parse DAG, verify nodes, emit `gov` deltas, integrate with refinement issues.
- [ ] Extend refinement loop to generate PCN/GoV artifacts and enforce fail-closed behavior (PRD §7.5/7.8/7.9).
  - Owner: Platform | ETA: 2d | Deps: above modules
  - Notes: ensure numeric claims stream only after verification; add retry policies.

### Production Hybrid RAG & Calibration Plan
- [ ] Integrate production embedding model (E5/BGE) with caching and shard support (PRD §7.7).
  - Owner: Retrieval | ETA: 4d | Deps: vector infra
  - Notes: wire to new service, maintain hashing fallback; update tests.
- [ ] Add external corpus ingestion pipeline (BM25 + dense + KG features) and recall tuning (PRD §7.7).
  - Owner: Retrieval | ETA: 5d | Deps: embedding service, KG data
  - Notes: implement ingestion script, n-gram filters, KG boosts, recall evals.
- [ ] Recalibrate SNNE/CP with updated retrieval + LLM outputs; update `cp_reference` quantiles (PRD §7.2, §7.10).
  - Owner: Eval | ETA: 3d | Deps: new RAG pipeline
  - Notes: run evaluation suites (UQ-A1, CP-B1), publish quantiles, refresh thresholds.
