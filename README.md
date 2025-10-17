# UAMM — Uncertainty‑Aware Agent with Modular Memory

UAMM is a small web service that answers questions with safeguards. It:
- Retrieves and cites evidence (so answers are grounded, not guesswork).
- Streams tokens as it writes the answer.
- Estimates uncertainty and uses a verifier to flag issues.
- Can use tools (web search/fetch, math, read‑only SQL) to improve answers.
- Blocks risky actions unless approved.
- Exposes metrics and a simple dashboard.

If you want an “LLM agent with seatbelts,” this is it.

Quick start
- Install uv (package manager): https://docs.astral.sh/uv/
- Copy `.env.example` to `.env` and fill in values if needed.
- Create a virtual environment and run the API:
  - Create venv (Python 3.12): `make venv`
  - Activate venv:
    - macOS/Linux: `source .venv/bin/activate`
    - Windows PowerShell: `.\\.venv\\Scripts\\Activate.ps1`
  - Install: `make install` (base only)
    - Optional extras: `make install-vector` (vector backends), `make install-chunk` (tiktoken), `make install-gcp` (GCS tools), `make install-ingest` (PDF/DOCX), `make install-ocr` (OCR).
  - Run server: `make run`
  - Open docs: http://127.0.0.1:8000/docs

Runtime lifecycle
- The API uses FastAPI lifespan handlers for startup/shutdown (no deprecated `on_event`).
  - Startup initializes config/secrets, schema/migrations, in‑memory stores, background tasks (TTL cleaner, docs watcher).
  - Shutdown gracefully cancels background tasks.

Ask your first question
- Non‑streaming: `curl -s -X POST http://127.0.0.1:8000/agent/answer -H 'content-type: application/json' -d '{"question":"What is modular memory?"}' | jq`
- Streaming (SSE):
  `curl -N -X POST http://127.0.0.1:8000/agent/answer/stream -H 'content-type: application/json' -d '{"question":"What is modular memory?"}'`

Quickest path (Core profile)
- Core disables advanced/background features by default (planning, guardrails, MCP, auto‑ingest).
- Run: `UAMM_PROFILE=core uvicorn uamm.api.main:create_app --reload --factory`
- Ask: `curl -s -X POST http://127.0.0.1:8000/agent/answer -H 'content-type: application/json' -d '{"question":"What is modular memory?"}' | jq`

What you’ll see with streaming
- `ready` → stream is live
- `token` → incremental text
- `score` → uncertainty and verifier scores (SNNE/S₂)
- `tool` → when tools run (with safety metadata)
- `pcn` → numeric verification status (see PCN below)
- `gov` → reasoning DAG checks (Graph‑of‑Verification)
- `final` → full structured result

Streaming lite
- Set `stream_lite=true` in the POST body to suppress `score/tool/trace/pcn/gov` events and keep only `ready/token/final`.

Health/ready endpoints
- `GET /healthz` → `{ "status": "ok" }`
- `GET /readyz` → `{ "status": "ready" }` when DB is reachable, else 503 with `{ "status": "not_ready" }`.

Pluggable tools (ToolRegistry)
- List tools: `GET /tools` → `{ tools: ["WEB_SEARCH", "WEB_FETCH", ...] }`
- Register a tool (admin when auth enabled):
  - `POST /tools/register` with `{ "name": "MY_TOOL", "path": "pkg.module:callable", "overwrite": false }`
  - The callable will be available to the agent via its registry under `name`.
- Unregister a tool (admin): `DELETE /tools/{name}`
- Expected call signatures for built-ins the agent uses:
  - `WEB_SEARCH(q: str, k: int = 3) -> list`
  - `WEB_FETCH(url: str, policy: EgressPolicy) -> dict`
  - `MATH_EVAL(expr: str) -> float`
  - `TABLE_QUERY(db_path: str, sql: str, params: list, max_rows: int | None, time_limit_ms: int | None) -> list`

Policy JSON migration
- Prior versions stored workspace policy overlays as Python repr strings. To normalize to JSON:
  - `PYTHONPATH=src python scripts/migrate_policies_to_json.py --db data/uamm.sqlite`
  - The script is idempotent and only rewrites convertible rows.

Configuration (basics)
- App settings live in `config/settings.yaml`. Useful keys:
  - `accept_threshold`, `borderline_delta` — how strict the agent is
  - `tool_budget_per_turn`, `tool_budget_per_refinement` — how many tools it can use
  - `tools_requiring_approval` — e.g. `["WEB_FETCH", "TABLE_QUERY"]`
  - `table_allowed` — tables that SQL queries may read from (read‑only)
- Environment variables override YAML. Put local values in `.env`.

UI (Web Components)
- The built‑in UI under `/ui/*` is implemented with native Web Components (no build tools):
  - Components live in `src/uamm/api/static/js/components/` and are loaded as ES modules.
  - Core helpers (`apiFetch`, `sse`, `getContext/setContext`, debug) live in `src/uamm/api/static/js/core/`.
  - Pages are composed as islands in Jinja templates (e.g., `<uamm-playground>`, `<uamm-obs-page>`, `<uamm-rag-page>`, `<uamm-cp-page>`, `<uamm-evals-page>`, `<uamm-home-page>`, `<uamm-workspaces-page>`).
  - Styling is Bootstrap 5; components render in light DOM to inherit styles.
- Dev notes:
  - No bundler required. Modules are served statically from `/static/js`.
  - Debug logging: set `localStorage['uamm.debug']=1`; optional event overlay: `localStorage['uamm.devtools']=1`.
  - Context (workspace, key) persists in localStorage and is exposed via `getContext()`.
  - To develop only the UI, run the API and edit modules under `static/js`; the browser loads ES modules directly.
  - Minimal globals remain for the context modal: `window.ctxSave`, `window.ctxListWorkspaces`, `window.showToast`.

Secrets (simple)
- For local use, set env vars (e.g., `OPENAI_API_KEY`) in `.env`.
- In shared environments, the built‑in Secret Manager can read from Vault. This is optional.

Observability
- Dashboard JSON: `GET /dashboards/summary`
- Metrics (Prometheus): `GET /metrics/prom`
- Quick JSON metrics: `GET /metrics`

UQ, CP, PCN, GoV, Memory
- Uncertainty (UQ): Uses SNNE (Semantic Nearest‑Neighbor Entropy) with calibration.
  - Streaming `score` events include: `mode`, `s1` (SNNE normalized), `s2` (verifier), `final_score`, `cp_accept`.
  - Prometheus exposes SNNE metrics: averages, samples, and per‑domain stats.
- Conformal Prediction (CP): The service can gate accept/abstain using per‑domain τ.
  - `GET /cp/threshold?domain=default` returns τ (bootstrap from eval artifacts).
  - CP is auto‑enabled when a τ is available (config: `cp_enabled`, `cp_auto_enable`).
  - The Decision Head uses CP + threshold: accept only when `cp_accept` is true and the final score ≥ accept threshold.
- Proof‑Carrying Numbers (PCN): Numeric facts are marked and verified, then rendered safely.
  - During refinement, numeric values become tokens like `[PCN:...]`; SSE `pcn` events report `pending|verified|failed` and provenance.
  - On streaming, `[PCN:...]` is replaced by the verified number (or `[unverified]`).
- Graph‑of‑Verification (GoV): Reasoning steps are checked as a small DAG.
  - SSE `gov` events carry `{dag_delta: {ok, failing}}` when a step introduces a new premise/claim.
  - You can validate a compact DAG via `POST /gov/check` (see “More endpoints”).
- Persistent memory: A small SQLite “modular memory” stores facts, traces, summaries.
  - TTL cleanup runs in the background (config: `memory_ttl_days`, `steps_ttl_days`).
  - Optional vector search (FAISS/LanceDB) can enrich retrieval; disabled by default.

Planning (selective compute)
- Enable via env or request params: `UAMM_PLANNING_ENABLED=1` (modes: `tot|beam|mcts`, `UAMM_PLANNING_MODE`).
- Runs a budgeted search when answers are borderline (or `planning_when=always`); emits `planning` SSE events.
- Prometheus exports planning counters.

Faithfulness (claim-level)
- Extracts sentence-level claims and aligns to retrieved evidence.
- Low faithfulness adds `unsupported claims` to issues for refinement.
- Metrics: `/metrics` faithfulness summaries; Prometheus averages and counts.

Guardrails (heuristic)
- Optional pre/post checks for risky patterns.
- Enable: `UAMM_GUARDRAILS_ENABLED=1`; optional `UAMM_GUARDRAILS_CONFIG_PATH` (YAML).
- SSE `guardrails` events; Prometheus counters (pre/post, by domain).

SQL Checks & Assertions
- `/table/query` applies per-table checks from policy packs (e.g., min/max/non_negative/monotonic per column) and returns `checks` with `violations`.
- `/gov/check` accepts `assertions` (e.g., `no_cycles`, `no_pcn_failures`, `max_depth`, `path_exists`, `types_allowed`) and returns pass/fail.

Units for PCN
- Attach `policy: { units: "ms|kg|%|..." }` to PCN tokens. With `pint` installed (`uv pip install -e .[units]`), values are validated and Prometheus exports units-check counters.

Memory Promotion & Tables
- Enable promotions: `UAMM_MEMORY_PROMOTION_ENABLED=1`, `UAMM_MEMORY_PROMOTION_MIN_SUPPORT=3`.
- PDF table extraction (install `uv pip install -e .[tables]`) with `UAMM_DOCS_TABLES_ENABLED=1`; retrieval boosts table docs for tabular queries.

MCP (Pydantic AI)
- Exposes tools and `UAMM_ANSWER` via Pydantic AI MCP when runtime is installed.
- CLI: `PYTHONPATH=src .venv/bin/python scripts/mcp_server.py --host 127.0.0.1 --port 8765`.
- Metrics: `/metrics` includes `mcp` stats; Prometheus exports totals and per-tool counters.

Safety defaults
- Web fetch is protected (TLS required, private IPs blocked, optional allow/deny lists).
- SQL is read‑only and table‑scoped by config.
- Prompt‑injection patterns in fetched pages are blocked.
- High‑risk tools can require approval; requests pause until approved.

Common commands
- See all targets: `make help`
- Run tests: `make test`
- Format code: `make format`
- Lint: `make lint`
- Type check: `make typecheck`
- Install git hooks (format/lint/type/mis‑secrets): `make pre-commit-install`
- Run hooks now: `make pre-commit`

Workspaces & Auth
- Use `Authorization: Bearer <wk_...>` to bind workspace and role; or headers: `X-Workspace: my-team`, `X-User: alice`.
- Roles: admin (manage), editor (write/search), viewer (search-only).
- CLI:
  - `make ws-cli` to view usage
  - Create workspace (default rootless/single-DB): `PYTHONPATH=src .venv/bin/python scripts/workspace_keys.py create my-team`
  - Create workspace with filesystem root: `PYTHONPATH=src .venv/bin/python scripts/workspace_keys.py create my-team --root data/workspaces/my-team`
  - Issue key: `PYTHONPATH=src .venv/bin/python scripts/workspace_keys.py issue my-team editor editor-key`
  - List keys: `PYTHONPATH=src .venv/bin/python scripts/workspace_keys.py list-keys my-team`

Per-folder workspaces (multi-root)
- Each workspace can have its own root folder containing its DB and docs:
  - DB: `<root>/uamm.sqlite`
  - Docs: `<root>/docs`
  - Vectors (optional LanceDB): `<root>/vectors`
- Create via API (admin): `POST /workspaces` with `{ "slug": "my-team", "name": "My Team", "root": "data/workspaces/my-team" }`.
- Server resolves `db_path`, `docs_dir`, and `lancedb_uri` from the workspace root automatically per request.
  - If no root is set, falls back to global `settings.db_path` and `settings.docs_dir`.

Document ingestion
- Text: `POST /rag/docs` with `{ "title": "Report", "text": "..." }`.
- Folder: `POST /rag/ingest-folder` with `{ "path": "data/docs/<workspace>" }`.
- Upload: `POST /rag/upload-file` (multipart) with `file` and `filename`. Example:
  `curl -H "Authorization: Bearer $KEY" -F file=@doc.pdf -F filename=doc.pdf http://127.0.0.1:8000/rag/upload-file`
- Search: `GET /rag/search?q=...`.
Notes for multi-root: when a workspace has a `root`, all RAG endpoints transparently read/write under `<root>/docs` and store state in `<root>/uamm.sqlite`.

Troubleshooting
- “ModuleNotFoundError: scripts”: ensure `PYTHONPATH=src:.` (already handled in `make test`).
- No output on streaming: use `-N` flag in curl and keep the terminal open.
- 403 on TABLE_QUERY: the table is not in `table_allowed`.
- 400 on SQL: only `SELECT` is allowed; no comments/UNION/PRAGMA.

Advanced (optional)
- Evals & calibration demo (writes local SQLite):
  1) `PYTHONPATH=src python scripts/run_demo_evals.py --suite UQ-A1`
  2) Inspect thresholds: `curl -s 'http://127.0.0.1:8000/cp/threshold?domain=default'`
- SSE event examples (shape only):
  - ready: `{ "request_id": "..." }`
  - token: `{ "text": "partial" }`
  - score: `{ "s1": 0.8, "s2": 0.4, "final_score": 0.49, "cp_accept": false }`
  - tool: `{ "name": "WEB_FETCH", "status": "start|blocked|error", "meta": { ... } }`
  - final: full JSON result

Egress policy (WEB_FETCH)
- Keys in `config/settings.yaml`:
  - `egress_block_private_ip` (bool): block private IPs (default: true)
  - `egress_enforce_tls` (bool): require HTTPS (default: true)
  - `egress_allow_redirects` (int): max redirects (default: 3)
  - `egress_max_payload_bytes` (int): max response size (default: 5MB)
  - `egress_allowlist_hosts` (list): only allow these hosts (empty = allow all)
  - `egress_denylist_hosts` (list): never allow these hosts

More endpoints
- Recent steps: `GET /steps/recent?limit=50[&domain=...][&action=...][&include_trace=true]`
- Approvals API: `POST /tools/approve` → `{ approval_id, approved, reason }`
- Tuner (optional): propose/apply safer settings via `/tuner/propose` and `/tuner/apply`
- CP: `GET /cp/threshold?domain=...` and `GET /cp/stats`
- GoV: `POST /gov/check` with `{ "dag": { nodes, edges }, "verified_pcn": ["id1", ...], "assertions": [...] }` → `{ ok, failures, assertions }`

Notes
- Python version for dev is 3.14 (see `.python-version`).
- Optional extras
  - Vectors: `make install-vector`
  - Chunking: `make install-chunk`
  - Tables (pdfplumber): `uv pip install -e .[tables]`
  - Units (pint): `uv pip install -e .[units]`
  - Formal (pint + z3): `uv pip install -e .[formal]`

OCR system requirements
- macOS: `brew install poppler tesseract`
- Ubuntu/Debian: `sudo apt-get install poppler-utils tesseract-ocr`

These binaries are needed for converting PDF pages to images (poppler) and running OCR (tesseract). The Python packages are installed by default.

FAQ
- Which UQ method? SNNE with quantile‑based calibration and logistic fallback.
- Is CP actually gating? Yes. If τ exists, CP gates accept/abstain and is passed into the decision head; otherwise a static threshold is used.
- Are PCN/GoV implemented? Yes. PCN verifies numeric tokens and GoV checks premise→claim DAGs; both stream as SSE events and persist in step traces.
- Is memory persistent? Yes (SQLite). It has TTL cleanup and can be augmented with vector backends.
Backups on GCP (Cloud Run + GCS)
- Install GCP deps: `make install-gcp`
- Export a workspace and upload to GCS:
  - `PYTHONPATH=src .venv/bin/python scripts/gcs_backup.py my-team --bucket YOUR_BUCKET --prefix backups --api-key $ADMIN_KEY`
  - Optional KMS encryption: add `--kms-key projects/..../cryptoKeys/YOUR_KEY` (creates `.enc.json` envelope)
- Restore from GCS:
  - Latest backup under a prefix: `PYTHONPATH=src .venv/bin/python scripts/gcs_restore.py my-team gs://YOUR_BUCKET/backups/ --latest --replace --reindex --api-key $ADMIN_KEY`
  - Specific object: `PYTHONPATH=src .venv/bin/python scripts/gcs_restore.py my-team gs://YOUR_BUCKET/backups/workspace_my-team_...zip.enc.json --replace --api-key $ADMIN_KEY --reindex`
- Cloud Run Job image:
  - Build: `gcloud builds submit --tag gcr.io/PROJECT/uamm-gcs-backup -f jobs/cloudrun-backup.Dockerfile .`
  - Create job with args (workspace, bucket, etc.) and schedule via Cloud Scheduler.
  - Consider retention flags: `--retention-count 10` and/or `--retention-days 30` to prune older backups.

Bundle integrity & signing
- Workspace bundles include a `manifest.json` with per-file SHA-256, counts, and created_at.
- Optional HMAC signing/verification:
  - Set `UAMM_BACKUP_SIGN_KEY` on exporter and importer; import fails if signature mismatch.

Full environment bundles
- Export: `GET /config/bundle?include_db=true&workspaces=team1,team2` (admin)
- YAML variants: `GET /config/export_yaml`, `POST /config/import_yaml`

Policy packs & overlays
- List & view: `GET /policies`, `GET /policies/{name}`
- Apply to a workspace: `POST /workspaces/{slug}/policies/apply {"name": "clinical"}`
- Preview diff: `GET /workspaces/{slug}/policies/preview/{name}`
- Export/import packs: `GET /policies/export`, `POST /policies/import`
- Overlays used by agent answers and SQL guard: thresholds, budgets, approvals, retriever weights, vectors, table allowlists/policies.
 - Tool allowlist: add `tools_allowed` to restrict tools per workspace, e.g. `{ "tools_allowed": ["MATH_EVAL", "TABLE_QUERY"] }`.
   - Disallowed tools are blocked by the agent (emits `tool: blocked`) and by endpoints (e.g., `/table/query` returns 403).
  - Example pack: see `config/policies/tools_limited.yaml`.
    - Apply: `curl -X POST -H "Authorization: Bearer $ADMIN_KEY" -H 'content-type: application/json' -d '{"name":"tools_limited"}' http://127.0.0.1:8000/workspaces/my-team/policies/apply`
 - SQL checks & GoV assertions pack: see `config/policies/example_checks_assertions.yaml`.
   - SQL checks are applied automatically by `/table/query` when tables match; the endpoint returns `checks` with `violations`.
   - To run GoV assertions, fetch the pack and pass its `gov_assertions` under the `assertions` field to `/gov/check`.
