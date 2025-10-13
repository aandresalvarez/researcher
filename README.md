# UAMM — Uncertainty-Aware Agent with Modular Memory

Skeleton project structure aligned with PRD v1.3. This repo contains a FastAPI service, agent modules, tools, RAG, uncertainty, policy, and security components.

Quick start (uv + venv, Python 3.14)
- Create and populate a `.env` from `.env.example`.
- Install `uv` (see https://docs.astral.sh/uv/).
- Create a virtual environment and run the API:
  - Create venv (Python 3.14): `make venv`
  - Activate venv:
    - macOS/Linux (bash/zsh): `source .venv/bin/activate`
    - Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
  - Install deps: `make install` (or `make install-vector` for FAISS extras)
  - Run API (reload): `make run`
  - Deactivate when done: `deactivate`

Notes
- Python is pinned for development via `.python-version` to `3.14` and `pyproject.toml` `requires-python>=3.14`.
- Optional vector extras can be added later (`faiss-cpu`).
- Secrets are loaded through the `SecretManager`; populate `.env` for local dev and configure Vault (or a stub JSON file) for shared environments. See **Secret management** below for details.

-- This is a scaffold. Core logic is stubbed behind clear interfaces per PRD.

Secret management
- The FastAPI app boots a `SecretManager` (`src/uamm/security/secrets.py`) that prefers Hashicorp Vault when `vault_enabled=true`.
- Each secret is referenced by alias in `config/settings.yaml` under the `secrets` block; for local dev you can set `OPENAI_API_KEY` or `UAMM_SECRET_<ALIAS>` env vars.
- To emulate Vault locally, point `UAMM_VAULT_STUB_FILE` (or `vault_stub_file` in YAML) at a JSON file shaped like `{ "uamm/path": { "key": "value" } }`.
- In non-dev environments missing required secrets abort startup; in `dev`/`test` a warning is logged so you can iterate without Vault.

Calibration artifacts
- Seed datasets live under `tests/data/cp_seed.json`. Import them into your local DB via `python -m scripts.import_cp_artifacts --input tests/data/cp_seed.json --domain analytics --run-id seed-demo`.
- Use `tests/data/cp_seed_full.json` for a larger sample that satisfies a 10% target miscoverage.
- The importer accepts CSV or JSON (with an `items` array); it updates `cp_artifacts` and recomputes τ thresholds used by `/cp/threshold`.
- Demo pipeline:
  1. Generate eval outputs: `PYTHONPATH=src python scripts/run_demo_evals.py tests/data/demo_eval_dataset.json demo-run`
  2. Convert eval records into CP rows: `PYTHONPATH=src python scripts/evals_to_cp.py --input demo-run.json --output demo-calibration.json` (sample rows in `tests/data/demo_calibration.json`).
  3. Import calibration rows: `PYTHONPATH=src python scripts/import_cp_artifacts.py --input tests/data/demo_calibration.json --domain-field domain --run-id demo-calib`
  4. Inspect thresholds: `curl -s 'http://127.0.0.1:8000/cp/threshold?domain=analytics'`

Dashboard summary
- Fetch aggregated latency/acceptance/CP/governance metrics with `curl http://127.0.0.1:8000/dashboards/summary`.
- The response includes `governance.events`/`governance.failures`, which track Graph-of-Verification incidents emitted during refinements.

Makefile targets
- `make help` — list common targets
- `make venv` — create `.venv` with Python 3.14
- `make install` — install project into the venv
- `make install-vector` — install with vector extras
- `make run` — start FastAPI via uvicorn (reload)
- `make dev` — venv + install + run
- `make clean` — remove `.venv`

API docs
- Start the server (`make run`) and open `http://127.0.0.1:8000/docs` for the interactive Swagger UI.
- Redoc reference lives at `http://127.0.0.1:8000/redoc`; both surfaces include SSE examples for `/agent/answer/stream`.

Streaming (SSE)
- Start server: `make run`
- Stream answer: `curl -N -X POST http://127.0.0.1:8000/agent/answer/stream -H 'content-type: application/json' -d '{"question":"hello"}'`
- You should see `ready`, multiple `token` events, and a `final` event.
- Idempotency: add `-H 'X-Idempotency-Key: <key>'` to replay a completed response (ready+final only).
- Heartbeats: periodic `heartbeat` events every ~15s keep the stream alive.
- All events include `request_id` for correlation.

SSE event schema (examples)
- ready: `{ "request_id": "..." }`
- token: `{ "text": "partial" }`
- score: `{ "s1": 0.8, "s2": 0.4, "final_score": 0.49, "cp_accept": false, "cp_tau": 0.85 }`
- trace: `{ "step": 1, "is_refinement": true, "issues": ["missing citations"], "tools_used": ["WEB_FETCH"], "prompt_preview": "..." }`
- tool: `{ "name": "WEB_FETCH", "status": "start|stop|blocked|error", "meta": { "url": "https://...", "requested_url": "...", "status": 200, "content_type": "text/html", "bytes": 1024, "policy_result": "allowed", "injection_blocked": false }}`
- pcn: `{ "id": "PCN-123", "type": "pcn_verified", "value": "42", "policy": {...}, "provenance": {...} }`
- gov: `{ "dag_delta": { "ok": false, "failing": ["math_sum_balances"] } }`
- error: `{ "code": "server_error", "message": "Traceback..." }`
- heartbeat: `{ "t": 1728690000 }`
- final: `AgentResultModel` (full JSON payload)
Approvals over SSE
- Configure approvals in `config/settings.yaml`, e.g. `tools_requiring_approval: ["WEB_FETCH"]`.
- When a listed tool is requested, the stream emits `tool` with `status="waiting_approval"` and an `id`.
- Approve via `POST /tools/approve {"approval_id":"...","approved":true}`; the stream resumes and continues emitting events.

Health check
- `curl http://127.0.0.1:8000/health` → `{ "status": "ok", "db": {"steps": true} }`

Calibration reference
- analytics: `τ_accept = 0.86`, SNNE quantiles `{0.10: 6.22e-04, 0.25: 1.55e-03, 0.50: 3.11e-03, 0.75: 4.66e-03, 0.90: 5.59e-03}`
- biomed: `τ_accept = 0.86`, SNNE quantiles `{0.10: 0.3690, 0.25: 0.3825, 0.50: 0.4050, 0.75: 0.4300, 0.90: 0.4450}`
- Refresh by running `python - <<'PY' ... run_suite("CP-B1") ...` and verifying `cp_reference` before deployment.

RAG corpus (BM25/FTS baseline)
- Add a document:
  - `curl -s -X POST http://127.0.0.1:8000/rag/docs -H 'content-type: application/json' -d '{"title":"Upadacitinib Q1 Report","url":"https://ex","text":"1,284 unique patients in Q1-2025 per cohort"}'`
- Search corpus:
  - `curl -s 'http://127.0.0.1:8000/rag/search?q=unique%20patients%20Q1-2025'`
- Note: Uses SQLite FTS5 when available; falls back to simple term overlap.

Vector backends (FAISS/LanceDB)
- Vector extras target Python <3.13 (per PyArrow wheels). On 3.14+, create a dedicated 3.11 virtualenv with `make vector-venv`.
- Install vector extras inside an existing env with `uv pip install -p .venv -e .[vector]` (Python 3.12 or 3.11).
- Enable LanceDB by setting `vector_backend: "lancedb"` in `config/settings.yaml`; tweak `lancedb_uri`, `lancedb_table`, and `lancedb_k` as needed.
- `/rag/docs` ingestion and `scripts/ingest_corpus.py` automatically upsert normalized embeddings into LanceDB, and hybrid retrieval merges LanceDB scores with sparse/dense weighting.
- Revert to FAISS (in-memory) by setting `vector_backend: "faiss"` or disable vectors entirely with `vector_backend: "none"`.

Flujo integration
- Typed node wrappers (retriever, main agent, verifier, policy, refinement, tools, PCN, GoV) live under `uamm.flujo.nodes`; use them to embed the agent inside Flujo graphs.
- A YAML loader (`uamm.flujo.dsl.load_pipeline_from_yaml`) converts declarative definitions into runnable pipelines that retain context between nodes.
- See `docs/flujo.md` for examples and extension guidance.
Demo evals and CP thresholds
- Run demo evals to populate `cp_artifacts` and compute per-domain τ:
  - `make eval-demo`
- Inspect thresholds and stats via API:
  - `curl -s 'http://127.0.0.1:8000/cp/threshold?domain=biomed'`
  - `curl -s 'http://127.0.0.1:8000/cp/stats'`
- Demo script also upserts CP reference baselines (`cp_reference`) so `/metrics` and `/metrics/prom` expose drift deltas and recent SNNE quantiles per domain.
- Run full suites and persist results:
  - `python scripts/run_demo_evals.py --all-suites --run-id nightly-$(date +%Y%m%d)`
- Extended CP eval (analytics/governance coverage):
  - `python - <<'PY' ... run_suite("CP-B1") ...` (baseline)
  - `python - <<'PY' ... run_suite("CP-B1-EXT") ...` (additional analytics/governance cases)
- Fetch stored reports:
  - `curl -s 'http://127.0.0.1:8000/evals/report/nightly-20250101'`

Database backups
- Run `python scripts/backup_sqlite.py --db data/uamm.sqlite --backup-dir backup --vacuum` to snapshot the database and prune `cp_artifacts` older than 90 days.
Load/perf smoke
- With the API running, execute `python scripts/load_smoke.py --requests 50 --concurrency 5 --p95-threshold 6`.
- The harness fires `/agent/answer` bursts, reports mean/p50/p95/p99, and exits non-zero if errors exceed `--max-errors` or p95 crosses the supplied threshold.
WEB_FETCH egress configuration
- Configure outbound fetch policy in `config/settings.yaml`:
  - `egress_block_private_ip` (bool): block RFC1918, loopback, link-local (default: true)
  - `egress_enforce_tls` (bool): require HTTPS (default: true)
  - `egress_allow_redirects` (int): max redirects to follow (default: 3)
  - `egress_max_payload_bytes` (int): max response size (default: 5MB)
  - `egress_allowlist_hosts` (list): explicit host allow-list (empty means allow all, subject to other checks)
  - `egress_denylist_hosts` (list): explicit host deny-list
- The agent uses these settings to construct an egress policy for WEB_FETCH. `tool` SSE events now surface the original `requested_url`, policy outcome, payload metrics, and whether prompt-injection enforcement blocked the fetch.
- Override per-request by passing the same keys in the answer body (advanced).

Steps and metrics endpoints
- Recent steps: `GET /steps/recent?limit=50[&domain=...][&action=...][&include_trace=true]`
  - Returns id, ts, domain, action, s1, s2, final_score, cp_accept, change_summary, pack_ids; optional full `trace` when requested.
- Metrics (Prometheus): `GET /metrics/prom` and JSON: `GET /metrics`
  - Exposes global and per-domain counters, answer latency histograms, approval backlog gauges (pending/approved/denied, average/max pending age), plus `latency`/`latency_by_domain` summaries (avg/p95) and `abstain_rate`.
  - JSON response adds `cp_stats`, `alerts.cp` when false-accept exceeds `cp_target_mis + cp_alert_tolerance`, `alerts.latency` when p95 breaks `latency_p95_alert_seconds`, and `alerts.approvals`/`alerts.abstain` when backlog or abstention rate cross thresholds.
  - Prometheus mirrors the gauges (`uamm_latency_p95_seconds`, `uamm_abstain_rate`, `uamm_cp_false_accept_rate`, etc.) and exposes alert flags (`uamm_alert_latency`, `uamm_alert_abstain`, `uamm_alert_cp`, `uamm_alert_approvals`).
  - Tuning knobs (config or env overrides):
    - `cp_alert_tolerance` (default `0.02`) — buffer above `cp_target_mis` before raising CP drift alerts.
    - `approvals_pending_alert_threshold` (default `5`) — pending approvals count threshold.
    - `approvals_pending_age_threshold_seconds` (default `300`) — maximum pending age before alerting.
    - `snne_drift_quantile_tolerance` (default `0.08`) — max allowed shift between baseline and recent SNNE quantiles before alerting.
    - `snne_drift_min_samples` (default `50`) and `snne_drift_window` (default `200`) — minimum sample size and rolling window for drift checks.
    - `latency_p95_alert_seconds` (default `6.0`) and `latency_alert_min_requests` (default `20`) — p95 latency budget and minimum sample size for alerts.
    - `abstain_alert_rate` (default `0.3`) and `abstain_alert_min_answers` (default `20`) — abstention rate threshold and minimum answer count before alerting.

Tuner agent
- Propose tuned settings + canary suites: `POST /tuner/propose` (body allows `suite_ids`, custom `targets`, and optional `metrics`). Returns a `proposal_id`, config patch, suite metrics, and always sets `requires_approval=true`.
- Apply or reject a proposal: `POST /tuner/apply` with `{ "proposal_id": "...", "approved": true/false, "reason": "optional" }`. Approved proposals patch the live settings (`accept_threshold`, `borderline_delta`, `snne_*`, `max_refinement_steps`, `cp_target_mis`).

Approvals (stub)
- Endpoint: `POST /tools/approve` with body `{ "approval_id": "...", "approved": true, "reason": "..." }`
- The server keeps an in-memory approvals store with TTL (default 30 minutes). This is a stub for PRD §10.4; full pause/resume integration for high-risk tools will be added next.
- Configure which tools require approval via `config/settings.yaml`:
  - Example: `tools_requiring_approval: ["WEB_FETCH", "TABLE_QUERY"]`
  - When a listed tool is encountered, the stream emits a `tool` event with `status="waiting_approval"` and an `id` you can pass to `/tools/approve`.
Non-streaming approvals
- `POST /agent/answer` responds `202 Accepted` with `{approval_id, status: "waiting_approval"}` when a high-risk tool is requested. Approve and retry request with the same parameters to proceed (idempotency supported via `X-Idempotency-Key`).

PCN events (stub)
- The stream emits `pcn` events around numeric verification in refinement:
  - `pcn_pending` then `pcn_verified|pcn_failed` with a shared `id` and `policy`.
- Current implementation emits events for `MATH_EVAL`-derived values. Responses (REST and SSE) replace placeholders with the verified value or `[unverified]` if verification fails; provenance metadata is logged in the `pcn` event payload.

GoV events (stub)
- A simple `gov` event with `{ dag_delta: { ok: true, failing: [] } }` is emitted during refinement to scaffold the integration.
