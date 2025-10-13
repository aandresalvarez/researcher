Below is the **combined PRD + FSD** for the *Uncertainty-Aware Agent with Modular Memory (UAMM)*. It is end-to-end, implementation-ready, and reflects all decisions we’ve converged on (tools, SNNE, CP, extrinsic refinement, hybrid RAG, PCN, GoV, evals, tuner, streaming, portability to Flujo).

---

# 0) Document Control

* **Name:** UAMM — Uncertainty-Aware Agent with Modular Memory
* **Version:** 1.3 (Oct 11, 2025)
* **Owner:** Alvaro (PM/Tech Lead)
* **Stakeholders:** Platform Eng, Retrieval/Infra, Data Science, Security/Compliance, DevEx
* **Targets:** Python SDK + FastAPI; **PydanticAI** agents/tools; future **Flujo** nodes
* **Status:** PRD+FSD approved for implementation

---

# 1) Plain-English Summary (for everyone)

**UAMM** is a *seatbelt + co-pilot* for AI: it grounds answers in evidence, measures how sure it is (by meaning, not words), checks numbers and logic, fixes borderline issues with tools (browser, calculator, SQL), and **abstains safely** if confidence is too low. It streams responses fast, shows sources, verifies numbers, and leaves an audit trail.

---

# 2) Problem, Scope, Principles

## 2.1 Problem

LLMs can be eloquent and still wrong. For analytics/biomed/code, we need **evidence, risk control, and transparency**, not just fluent text.

## 2.2 Scope

* Single agent with **defense-in-depth**: Hybrid RAG → UQ (SNNE) → Verifier → **Extrinsic** refinement (tools) → Calibrated **abstention** → Explainability.
* Works with GPT-5 (or any strong model), **streaming** by default. Private data stays private (on-prem RAG/SQL).

## 2.3 Out of scope (MVP)

* Model fine-tuning, full web tool zoo, complex multi-agent debate, full formal verification proofs (we add state checks in M3).

## 2.4 Principles

* **Separation of concerns:** Core logic vs framework shell vs adapters.
* **Single responsibility:** Each module does one thing well (UQ, verify, retrieve, refine).
* **Measured risk:** Calibrated thresholds > vibes.
* **Fail-closed for numbers:** unverified numbers never silently pass.
* **Portability:** Swap backends; wrap as **Flujo** nodes.

---

# 3) Personas & Primary Use Cases

* **Biomedical/DS:** OMOP cohort metrics with citations, verified numbers, snapshot dates.
* **Developer:** Minimal repro & patch; run tests; accept only if tests pass.
* **Analyst/Ops:** Metric shift explanations with queryable evidence; verified figures; abstain when inputs are insufficient.

---

# 4) Goals & Success Criteria

* **Reliability:** “Correct or abstain” with **false-accept among accepted ≤ target (e.g., ≤5%)** via CP.
* **Self-improvement at inference:** Borderline answers refined **with tools** (not self-talk), bounded to ≤2 iterations.
* **Usability:** First token ≤700ms (P50) streaming; one-liner SDK.
* **Typed & governed:** Pydantic models, structured tools, approvals, audit.
* **Observability:** Per-step S₁/S₂/CP decision, tool calls, tokens, latency.
* **Portability:** sqlite-vec → FAISS now; LanceDB later; Flujo-ready.

---

# 5) Non-Functional Requirements

* **Streaming:** First token ≤700ms (P50).
* **Latency:** P50 ≤2.5s; P95 ≤6s with `max_refinements=2`.
* **Per-refinement budget:** ≤1.5s (P95), ≤2 tool calls.
* **Cost:** S₁/SNNE samples default 5→3 where possible; cache aggressively.
* **Security/Privacy:** PHI/PII redaction; read-only SQL; RBAC; secrets in vault.
* **Availability:** 99.9% (service); graceful degradation (disable refinement on overload).
* **DR/Backup:** Daily SQLite snapshot + FAISS index file snapshot.

---

# 6) System Architecture (PRD view)

## 6.1 High-level layers

1. **Hybrid RAG grounding:** BM25 + dense (E5/BGE) + optional KG features.
2. **Uncertainty (UQ):** **SNNE** on short, diverse answer samples (semantic, not lexical).
3. **Structured verifier (S₂):** Schema-enforced correctness/completeness + issue list.
4. **Extrinsic refinement:** If borderline, one/two loops using **tools** (browser, SQL, math) guided by S₂ issues.
5. **Conformal abstention (CP):** Risk-cap gate; **M1.5** bootstrap; **M2** full calibration.
6. **PCN & GoV:** Render-time numeric verification; DAG reasoning checks.
7. **Tuner Agent:** Proposes safer/faster settings via evals + canary.

## 6.2 Component diagram (Mermaid)

```mermaid
flowchart LR
  UI[Client / SDK / REST] --> API[FastAPI (PydanticAI)]
  API --> Main[Main Agent]
  Main -->|Needs context| RAG[Retriever Agent]
  RAG --> Mem[(SQLite metadata)] & Vec[(sqlite-vec/FAISS)]
  Main --> UQ[SNNE Scorer]
  Main --> Vrf[Structured Verifier (S2)]
  Vrf -->|issues| Refine[Refinement Prompt Builder]
  Refine --> Tools[Tool Registry]
  Tools --> Main
  Main --> Policy[CP Gate + Policy]
  Policy --> Out[Answer/Abstain + Trace]
  Out --> PCN[Renderer PCN verify] & GoV[Graph-of-Verification]
  Evals[Evals Runner] -->|artifacts| Tuner[Tuner Agent]
  Tuner -->|proposals+canary| API
```

---

# 7) Detailed Functional Spec (FSD)

## 7.1 Main Agent (PydanticAI)

* **Input:** `question`, `use_memory`, `memory_budget`, `stream`, `max_refinements`, `borderline_delta`, `uq_mode`, `snne_samples`, `cp_target_mis`.

* **Flow:**

  1. **Stream initial answer**.
  2. Compute **SNNE** (S₁) + **Verifier** (S₂).
  3. **CP** gate → accept / borderline refine / abstain.
  4. If refine: build **refinement prompt** (includes S₂ issues), allow tool calls; re-score; stop on accept or budget.
  5. Render with **PCN** badges; optional **GoV** summary.

* **Output:** `AgentResult` (final text, stop_reason, uncertainty block, full trace, pack_used, usage).

## 7.2 UQ Module — SNNE (default), SE fallback, optional logprob

* **Sampling:** `n=5` (config) short re-phrasings; `temperature≈0.6`, `top_p≈0.95`.
* **Similarity:** cosine on sentence embeddings (cached); NLI entailment optional (high-stakes route).
* **SNNE:**
  [
  \mathrm{SNNE}(q) = -\frac{1}{n}\sum_{i}\log\sum_{j}\exp\big(f(a_i,a_j|q)/\tau\big)
  ]
  Normalize into `[0,1]` (lower is better).
* **Interface:** `score_snne(samples, tau) -> snne_norm`.
* **Config:** `UQ_MODE=snne|se|logprob`; `SNNE_SAMPLES`, `SNNE_TAU`.

* **Normalization & drift:**

  * Map SNNE raw to `[0,1]` via per-domain monotonic calibration: quantile transform → isotonic regression with clipping to `[0,1]`.
  * Use Mondrian splits by domain/task (biomed, code, analytics) to avoid distribution mixing; persist transform params per domain with versioning.
  * Drift detection: two-sample test (e.g., KS) on recent `S` vs calibration window; on drift, raise alert and fall back to conservative thresholds and/or reduce `SNNE_SAMPLES` under load.
  * Minimum sample size for a stable mapping: ≥500 labeled items per domain; below this, use static min–max on dev set and widen CP target (more abstention).

## 7.3 Structured Verifier (S₂)

* **Model:** GPT-5 Responses with **response_format** enforcing JSON schema:

```json
{ "score": 0..1, "issues": [string], "needs_fix": boolean }
```

* **Interface:** `verify(question, answer)->(score, issues, needs_fix)`
* **Behavior:** Error-aware scoring; minimal explanations in `issues`.

## 7.4 Policy & CP Gate

* **Final score:** `S = w1*(1 - SNNE_norm) + w2*S2` (defaults `0.55/0.45`).
* **CP (M1.5 bootstrap / M2 full):** After computing `S`, evaluate `CP_accept(S)` from calibration data; if unavailable, use static τ.
* **Decisions:**

  * `CP_accept && S ≥ τ_accept` → **accept**
  * `τ_accept − δ ≤ S < τ_accept` → **refine** (once/twice)
  * else → **abstain** (include what’s missing)

## 7.5 Recursive Refinement (extrinsic only)

* **Trigger:** Borderline band and issues appear **fixable** via tools.
* **Prompt template (default):**

```
Improve your previous answer using these explicit issues:
{issues}

You MAY use tools:
- WEB_SEARCH/WEB_FETCH to find citations/source/date,
- MATH_EVAL for calculations,
- TABLE_QUERY for DB counts.

Question:
{question}

Previous answer:
{previous_answer}

Return a corrected, concise answer with citations if applicable.
```

* **Budget:** `MAX_REFINEMENT_STEPS=2`, `TOOL_BUDGET_PER_REFINEMENT=2`.
* **Stop:** accept / abstain / budget max.

## 7.6 Tools Registry (typed & governed)

* **Common tools:**

  * `WEB_SEARCH(q, k=3)` → search results (title, url, snippet).
  * `WEB_FETCH(url)` → sanitized main text + metadata.
  * `MATH_EVAL(expr)` → numeric result (sympy/numexpr; sandbox; no FS/net).
  * `TABLE_QUERY(sql)` → read-only SELECT with regex allowlist; domain RBAC.

* **Schema validation:** JSON-schema per tool.

* **Approvals:** `requires_approval` for high-risk tools → deferred flow.

* **Budgets:** `TOOL_BUDGET_PER_TURN`, `TOOL_BUDGET_PER_REFINEMENT`.

## 7.7 Hybrid RAG

* **Retriever Agent:**

  * Sparse (BM25) + Dense (E5/BGE); optional **KG features** for entity edges.
  * **Filter stage:** Deduplicate; threshold by relevance; limit tokens.
  * **Outputs:** evidence pack (doc ids, spans/quotes, URLs).

* **Memory Agent:** Internal memory (SQLite + sqlite-vec/FAISS) for local notes, past steps, configs; returns a curated pack `[{id, snippet, why, score}]`.

## 7.8 PCN — Proof-Carrying Numbers (render-time)

* **Model output token (example):**

```json
{"type":"pcn","value":"32.7","source_url":"https://.../report","policy":{"tolerance":0.1,"unit":"per_100k"}}
```

* **Renderer:** fetch source (or in-app DB result), verify per policy; show **Verified** ✓ badge or **Unverified** (fail-closed).
* **Policy options:** exact, rounding, unit alias, tolerance with “approximately” qualifier.

* **Streaming & gating behavior:**

  * Strict fail-closed: numeric tokens originating from PCN-marked values are withheld until verification completes; stream placeholder events instead.
  * Events: `pcn_pending` (id, placeholder), `pcn_verified` (id, value, policy), `pcn_failed` (id, reason). See §10.3 for SSE schema.
  * If verification fails: emit `pcn_failed` and render as “Unverified” with policy hint; never silently commit unverified numbers.
  * Provenance: include snapshot/version identifiers (e.g., dataset version, job ID, SQL hash) in the event payload and the final trace.

## 7.9 GoV — Graph-of-Verification for reasoning chains

* **DAG JSON schema (simplified):**

```json
{
 "nodes":[{"id":"n1","type":"claim","text":"..."},{"id":"n2","type":"premise","text":"..."}],
 "edges":[{"from":"n2","to":"n1"}]
}
```

* **Process:** topological order; verify premises (via RAG/PCN/rules) before dependent claims; report failing node(s) to S₂ issues and refinement prompt.

## 7.10 Conformal Prediction (CP)

* **Goal:** Cap **false-accept among accepted** (e.g., ≤5%).
* **Inputs:** Calibration set `{S, correct/incorrect}` from evals/early usage.
* **Output:** Threshold `τ_CP` so empirical coverage holds; monitor drift; recalibrate periodically.
* **M1.5:** bootstrap with small set; **M2:** full.

## 7.11 Tuner Agent

* **Reads:** eval artifacts (JSON/CSV): acceptance, false-accept, p95, tokens, tool usage, RAG metrics.
* **Proposes:** `τ_accept`, `δ`, `w1/w2`, `SNNE_SAMPLES/TAU`, `CP_TARGET_MIS`, RAG K, tool budgets, backend switch sqlite-vec→FAISS.
* **Validates:** canary eval; checks guardrails; **requires_approval** to apply.

## 7.12 CP–Refinement Coupling

* **Coverage definition:** Calibrate CP on the distribution of final post-refinement scores `S` (including cases with 0, 1, or 2 refinements), not solely on first-pass.
* **Per-step gating:** Evaluate `cp_accept(S_t)` after each step `t`; only accept when both `cp_accept` passes and `S_t ≥ τ_accept`. Borderline band (`τ_accept−δ ≤ S_t < τ_accept`) may trigger a refinement if tool budgets allow and issues are fixable (per §7.5).
* **Mondrian CP:** Maintain domain-specific calibrations (biomed/code/analytics) to preserve coverage under covariate shift; store calibration runs with run IDs and sample sizes.
* **Recalibration triggers:** schedule (e.g., weekly), drift alerts (see §7.2), and acceptance/false-accept deviations. Canary evaluations must precede rollout; Tuner can propose new `τ_accept`, `δ` with guardrails.

---

# 8) Data Models

## 8.1 SQLite schema (metadata & traces)

```sql
CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY,
  ts REAL,
  key TEXT,         -- "fact:"|"trace:"|"summary:"|"tool:"
  text TEXT,
  embedding BLOB,   -- for sqlite-vec (optional; FAISS externalizes vectors)
  domain TEXT,      -- "fact"|"trace"|"summary"|"tool"
  recency REAL,
  tokens INT,
  embedding_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_key_ts ON memory(key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mem_domain ON memory(domain);

CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY,
  ts REAL,
  step INTEGER,
  question TEXT,        -- REDACTED text only
  answer TEXT,          -- REDACTED text only
  s1 REAL,               -- SNNE_norm or SE_norm
  s2 REAL,
  final_score REAL,
  cp_accept INTEGER,     -- 0/1
  action TEXT,           -- "accept"|"iterate"|"abstain"
  reason TEXT,
  is_refinement INTEGER, -- 0/1
  status TEXT,           -- "ok"|"incomplete"|"error"
  latency_ms INTEGER,
  usage TEXT,            -- JSON: tokens, duration
  pack_ids TEXT,         -- JSON list
  issues TEXT,           -- JSON from verifier
  tools_used TEXT,       -- JSON list
  eval_id TEXT,
  dataset_case_id TEXT,
  is_gold INTEGER,
  gold_correct INTEGER
);
```

## 8.2 Pydantic models (key ones)

```python
class MemoryPackItem(BaseModel):
    id: str
    snippet: constr(max_length=240)
    why: str
    score: condecimal(ge=0, le=1)

class StepTraceModel(BaseModel):
    step_index: int
    is_refinement: bool
    s1_or_snne: float
    s2: float
    final_score: float
    cp_accept: bool
    issues: List[str] = []
    tools_used: List[str] = []
    action: Literal["accept","iterate","abstain"]
    reason: str
    latency_ms: int
    usage: Dict[str, Any] = {}

class UncertaintyModel(BaseModel):
    mode: Literal["snne","se","logprob"]
    snne: Optional[float] = None
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
```

## 8.3 CP calibration data

```json
{ "run_id":"2025-10-11", "items":[
  {"S":0.86, "accepted":true,  "correct":true},
  {"S":0.79, "accepted":false, "correct":true},
  {"S":0.81, "accepted":true,  "correct":false}
]}
```

## 8.4 PCN token (renderer)

```json
{"type":"pcn","value":"1284","source_url":"bq://project.dataset.table#job:123","policy":{"tolerance":0}}
```

## 8.5 GoV DAG (example)

```json
{
  "nodes":[
    {"id":"p1","type":"premise","text":"Menopause Q01 spec v1.3 used"},
    {"id":"p2","type":"premise","text":"Rx concept set v2.1 for Upadacitinib"},
    {"id":"c1","type":"claim","text":"1,284 unique patients in Q1-2025"}
  ],
  "edges":[{"from":"p1","to":"c1"},{"from":"p2","to":"c1"}]
}
```

---

# 9) Algorithms (implementation detail)

## 9.1 SNNE (pseudo-code)

```python
def snne(answers: List[str], tau: float, embed) -> float:
    # answers: sampled short paraphrases
    V = [embed(a) for a in answers]  # cache!
    # cosine similarities in matrix
    S = cos_sim_matrix(V, V)         # n x n
    # log-sum-exp per row
    lse = np.log(np.sum(np.exp(S / tau), axis=1))
    snne_raw = - np.mean(lse)
    # normalize to [0,1] (fit min/max on dev set or use z-normalization + sigmoid)
    return normalize(snne_raw)
```

## 9.2 Policy + CP (pseudo-code)

```python
S = w1*(1 - snne_norm) + w2*S2
if cp_accept(S):
    if S >= tau_accept: return ACCEPT
if tau_accept - delta <= S < tau_accept: return REFINE
return ABSTAIN
```

## 9.3 Refinement loop

```python
for r in range(max_refinements):
    issues = verifier.issues
    prompt = build_refine_prompt(question, answer, issues)
    answer = llm.generate(prompt, allow_tools=True, tool_budget=2)
    S1 = snne(sample_short_variants(answer))
    S2, issues = verifier.verify(question, answer)
    S = w1*(1-S1) + w2*S2
    if cp_accept(S) and S >= tau_accept: return accept(answer)
# fallback
return abstain_with_request(issues_or_missing_evidence)
```

## 9.4 RAG score

```
score = α·similarity + β·recency_decay + γ·diversity_bonus + ζ·type_bonus
recency_decay = exp(-Δt / τ)
```

---

# 10) APIs

## 10.1 Python SDK

```python
res = agent.answer(
  question="...",
  use_memory=True,
  memory_budget=8,
  stream=True,
  max_refinements=2,
  borderline_delta=0.05,
  uq_mode="snne",
  snne_samples=5,
  cp_target_mis=0.05
)
print(res.final)
```

## 10.2 REST (FastAPI)

* **POST** `/agent/answer`
  **Body:** `{question, use_memory, memory_budget, stream, max_refinements, borderline_delta, uq_mode, snne_samples, cp_target_mis}`
  **200:** `AgentResultModel`

* **POST** `/memory` → add memory row

* **GET** `/memory/search` → hybrid hits

* **POST** `/memory/pack` → curated pack for a task

* **POST** `/tools/approve` → resume deferred tool

* **POST** `/evals/run` → start eval run

* **GET** `/evals/report/{run_id}` → JSON + Markdown reports

## 10.3 Streaming & Error Model

* **Protocol:** Server-Sent Events (SSE) at `POST /agent/answer/stream` with `Content-Type: text/event-stream`; alternatively `Accept: text/event-stream` on `/agent/answer` upgrades to SSE.
* **Events:**
  * `ready` `{request_id}` — stream initialized.
  * `token` `{text}` — incremental model tokens.
  * `trace` `{step, is_refinement, issues, tools_used}` — incremental trace updates.
  * `score` `{s1, s2, final_score, cp_accept}` — optional score updates.
  * `tool` `{name, status, meta}` — tool start/stop/errors within budgets.
  * `pcn` `{type: pcn_pending|pcn_verified|pcn_failed, id, value?, policy?, reason?}` — see §7.8.
  * `gov` `{dag_delta}` — optional GoV updates.
  * `final` `AgentResultModel` — complete result payload (same shape as non-streaming).
  * `error` `{code, message, request_id}` — terminal error; stream closes after send.
  * `heartbeat` `{t}` — keepalive every 15s to maintain intermediaries.
* **Idempotency & tracing:** Clients may send `X-Idempotency-Key`; server always returns `X-Request-ID`. Retries with same idempotency key resume or no-op if completed.
* **Cancellation:** Client disconnect stops generation and tools; partial trace is persisted in `steps` for audit/resume.
* **Timeouts:** Per-request server timeout (default 30s) and per-tool timeouts; emit `tool` events with `status=timeout` and continue if policy allows.
* **Errors:**
  * Non-streaming: HTTP `4xx/5xx` with JSON `{code, message, request_id}`.
  * Streaming: `error` event followed by stream close; no partial `final`.

## 10.4 Tool Approvals & Resume

* **Deferred tools:** Tools marked `requires_approval=true` yield `tool` event `{id, name, status: "waiting_approval"}` and the API responds `202 Accepted` with `approval_id` when non-streaming.
* **Approve:** `POST /tools/approve` body `{approval_id, approved: bool, reason?}`; on approval, the agent resumes with preserved context and tool budgets.
* **Expiry & replay:** Approvals expire after configurable TTL (default 30 minutes). Replays with the same `approval_id` are idempotent.

---

# 11) Security, Privacy, Compliance

* **PHI/PII filters:** pre-save and pre-return; redact logs.
* **SQL guard:** read-only; strict regex; RBAC by domain/table.
* **Secrets:** vault; never log keys; environment only at runtime.
* **Data residency:** on-prem querying for OMOP; no PHI to external APIs.
* **Audit:** export steps + pack IDs + PCN/GoV results.

## 11.1 PHI/PII Persistence Policy

* Redact before persistence: All text stored in databases, logs, and artifacts must pass PHI/PII redaction first. Raw prompts/answers are kept in-memory only and never persisted.
* Steps table: Fields `question` and `answer` in §8.1 are the redacted variants by definition; if raw is needed transiently for processing, it must not be written to disk or logs.
* Evals/artifacts: Apply the same redaction policy; redact or hash any free-text fields; never export PHI to external storage or APIs.
* Retention & TTL: Configure TTLs for `steps` and `memory` domains; default 30–90 days with secure deletion on expiry.

## 11.2 Tool Egress & SSRF Protections

* Egress policy: `WEB_FETCH`/`WEB_SEARCH` use a controlled HTTP client with:
  * DNS resolution and IP blocking for RFC1918/link-local/metadata ranges; disallow non-HTTP(S) protocols.
  * Domain allowlist/denylist support; optional per-tenant allowlists.
  * TLS enforcement with cert validation; follow limited redirects (≤3) within allowed domains.
  * Max payload size (e.g., 5 MB) and timeouts; decompression bombs detected.
  * Content sanitization: HTML → text (readability), strip scripts/iframes, normalize encodings.
  * Identify as a dedicated UA; respect `robots.txt` when configured.
* Error semantics: return structured errors (`status`, `reason`) without leaking response bodies; never echo fetched content into logs.

## 11.3 SQL RBAC & Query Guardrails

* Parse SQL with an AST and allow only read-only `SELECT`; forbid DDL/DML/CTEs unless explicitly whitelisted.
* Enforce per-domain/table RBAC; parameterize inputs; apply row/byte/time limits and rate limits per request.
* Log structured metadata (duration, rowcount) without query text unless redacted.

---

# 12) Observability & Ops

* **Metrics:** accept/iterate/abstain, **false-accept among accepted**, SNNE & S₂ histograms, p50/p95 latency, token costs, RAG Recall@K, PCN pass-rate, tool usage.
* **Logs:** per-step JSON (no PHI).
* **Alerts:** spikes in false-accept, p95, abstentions, incomplete responses.
* **Scaling:** horizontal FastAPI workers; cache embeddings; background pre-embed memory rows.
* **Backups:** daily SQLite/FAISS; retention 30–90 days.

---

# 13) Evals (PydanticAI Evals)

## 13.1 Datasets

* **Biomed/OMOP**, **Code triage**, **Analytics**; each has gold labels or verifiable sources.

## 13.2 Suites

* **UQ-A1:** SNNE vs SE vs (if available) logprob ⇒ AUROC/PR, cost/latency.
* **CP-B1:** Calibrate **false-accept among accepted** to ≤ target on held-out.
* **RAG-C1:** Recall@{5,10,20}, citation precision.
* **PCN-D1:** % numbers PCN-verified; fail-closed rate.
* **GoV-E1:** step-level P/R.
* **Refine-F1:** S₂ uplift vs added latency/tokens.
* **Stack-G1:** Where queries resolve in pipeline.

## 13.3 CI & Nightly

* CI smoke (UQ-A1 light + PCN-D1); nightly full; Markdown report.

---

# 14) Tuner Agent

* **Input:** eval artifacts + targets (false-accept ≤x, accept ≥y, p95 ≤z, tokens ≤b) + current config.
* **Output:** proposed config deltas + canary summary + rollout plan + `requires_approval=true`.
* **Guardrails:** never break quality/latency/cost targets.

---

# 15) Roadmap & Milestones

**M1 (2–3 wks):** Streaming agent; SNNE (n=3–5); S₂ verifier; extrinsic refinement; sqlite-vec; FAISS adapter; tools (search/fetch/math/sql); evals smoke; PHI filter; logs.

**M1.5 (1–2 wks):** CP bootstrap from early evals; add CP gate with initial calibration; dashboards.

**M2 (2–3 wks):** Full eval suite; Tuner Agent; LanceDB plugin; **CP full calibration**; RAG filter agent; PCN/GoV in production renderer.

**M3 (2 wks):** RBAC UI; audit export; **Flujo nodes**; basic formal state checks (LTL + constrained decoding).

---

# 16) Risks & Mitigations

* **SNNE cost:** keep n=5 default; cache embeddings; use embedding backend (NLI for critical paths only).
* **Over-abstain:** Tuner tunes τ/δ; CP target realistic; show users what is missing to proceed.
* **Verifier bias:** optional second judge/rules for high-stakes outputs; monitor disagreements.
* **RAG noise:** filter stage + citation spans + KG features.
* **Tool latency:** budgets + parallel fetch where safe + timeouts.

---

# 17) Testing & QA

## 17.1 Unit

* **DH-001** SNNE bounds & normalization.
* **DH-002** Canonicalizer/embedding cache hits.
* **DH-003** Policy threshold table (accept/refine/abstain).
* **DH-004** Incomplete LLM response path.
* **DH-005** Refinement terminates ≤max.
* **DH-006** Borderline triggers exactly one refinement.
* **DH-007** Issues appear in refinement prompt; tool budget enforced.
* **VR-001** Verifier schema strictness; clamp [0,1].
* **MA-001..005** RAG filters, recall, rerank, compression, PHI masking.
* **TL-001..002** SQL_SELECT guards; deferred approvals resume.
* **SD-001..003** Stream toggle; env overrides; FAISS swap.
* **EV-001..002** Evals runner artifacts.

## 17.2 Integration

* **I-001** Stream + refine delta; match policy.
* **I-002** Memory/RAG packs; Hit@K ≥ threshold.
* **I-003** Borderline iteration exactly once.
* **I-004** Abstain w/ clear missing evidence.
* **I-005** FAISS p95 improved for >50k vectors; parity on small.
* **I-006** Verifier invalid output → retry path.
* **I-007** Deferred tool approval (pause/resume).
* **I-008** Evals end-to-end; reports.
* **I-009** Tuner canary, approvals needed to apply.
* **I-010** CP: false-accept among accepted ≤ target on test.
* **I-011** Multi-step refinement improves S₂ (monotone non-decrease in harness).

## 17.3 Load & Perf

* **L-001** WAL contention; 100 RPS synthetic; no write-lock failures.
* **L-002** Vector latency scaling; FAISS reduces p95.
* **L-003** Cost guard: K=3 baseline; K=5 only borderline; token budget respected.
* **L-004** Refinement ≤1.5s P95; total ≤6s P95.

## 17.4 Security

* **S-001** PHI/PII masking verified; no PHI in logs.
* **S-002** Tool prompt injection blocked; safe errors logged.
* **S-003** RBAC: unauthorized domain/table access denied.

## 17.5 Acceptance / Golden

* 30–50 gold cases with expected decisions; gate regressions in false-accept and accept rate.

---

# 18) Deployment & CI/CD

* **Runtime:** Python 3.11+, FastAPI, Pydantic v2, OpenAI SDK (Responses).
* **Vector:** sqlite-vec (M1), **FAISS** (M1), **LanceDB** (M2).
* **Infra:** containerized; horizontal workers; Redis optional for caches.
* **CI:** lint + unit + smoke evals; **nightly** full evals; artifacts as GitHub Actions outputs.
* **Config:** env + YAML; feature flags for UQ mode, CP gate, FAISS/LanceDB, refinement on/off.

---

# 19) Portability to Flujo (Design-for-future)

* **Nodes:** `RetrieverNode`, `MemoryNode`, `MainAgentNode`, `VerifierNode`, `PolicyNode`, `RefinementNode`, `ToolNode`, `PCN/GoVNode`.
* **Contracts:** Typed Pydantic I/O; same adapters (LLMClient, VectorIndex).
* **Policy DSL:** Keep thresholds + budgets in YAML for Flujo orchestration.

---

# 20) Appendices

## A) OMOP instance (illustrative SQL fragments)

```sql
-- Upadacitinib codes are pre-materialized in concept_set_upadacitinib_v21
SELECT COUNT(DISTINCT p.person_id) AS patients
FROM cohort_menopause_q01_v13 c
JOIN drug_exposure d ON d.person_id = c.person_id
JOIN concept_set_upadacitinib_v21 x ON x.concept_id = d.drug_concept_id
WHERE d.drug_exposure_start_date BETWEEN DATE '2025-01-01' AND DATE '2025-03-31';
```

*(Hash this SQL for PCN provenance; snapshot from metadata table.)*

## B) PCN policy examples

* **Exact:** `{ "tolerance": 0 }`
* **Rounded:** `{ "tolerance": 0.5, "format": "rounded_1dp" }`
* **Units:** `{ "unit": "per_100k", "tolerance": 0.1 }`

## C) Refinement prompt (final default)

```
Issues to fix:
{issues}

Use tools if needed (WEB_SEARCH/WEB_FETCH/MATH_EVAL/TABLE_QUERY).
Question:
{question}

Previous answer:
{previous_answer}

Return a corrected, concise answer with citations where relevant.
```

## D) Example GoV DAG (reasoning)

(see §8.5)

## E) Config table (selected)

| Key                        | Default | Notes                                  |                      |
| -------------------------- | ------: | -------------------------------------- | -------------------- |
| STREAM_DEFAULT             |    true | Stream initial; refinement delta later |                      |
| UQ_MODE                    |    snne | se                                     | logprob (if exposed) |
| SNNE_SAMPLES               |       5 | 3 on cost-sensitive                    |                      |
| SNNE_TAU                   |     0.3 | tune via Tuner                         |                      |
| ACCEPT_THRESHOLD           |    0.85 | tuned                                  |                      |
| BORDERLINE_DELTA           |    0.05 | triggers refinement                    |                      |
| MAX_REFINEMENT_STEPS       |       2 | total tries = 1+2                      |                      |
| CP_TARGET_MIS              |    0.05 | start M1.5                             |                      |
| TOOL_BUDGET_PER_TURN       |       4 | guardrail                              |                      |
| TOOL_BUDGET_PER_REFINEMENT |       2 | guardrail                              |                      |
