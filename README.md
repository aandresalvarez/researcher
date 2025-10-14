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
  - Create venv (Python 3.14): `make venv`
  - Activate venv:
    - macOS/Linux: `source .venv/bin/activate`
    - Windows PowerShell: `.\\.venv\\Scripts\\Activate.ps1`
  - Install: `make install` (optional: `make install-vector` for vectors, `make install-ingest` for PDF/DOCX, `make install-ocr` for OCR, `make install-chunk` for tiktoken)
  - Run server: `make run`
  - Open docs: http://127.0.0.1:8000/docs

Ask your first question
- Non‑streaming: `curl -s -X POST http://127.0.0.1:8000/agent/answer -H 'content-type: application/json' -d '{"question":"What is modular memory?"}' | jq`
- Streaming (SSE):
  `curl -N -X POST http://127.0.0.1:8000/agent/answer/stream -H 'content-type: application/json' -d '{"question":"What is modular memory?"}'`

What you’ll see with streaming
- `ready` → stream is live
- `token` → incremental text
- `score` → uncertainty and verifier scores (SNNE/S₂)
- `tool` → when tools run (with safety metadata)
- `pcn` → numeric verification status (see PCN below)
- `gov` → reasoning DAG checks (Graph‑of‑Verification)
- `final` → full structured result

Configuration (basics)
- App settings live in `config/settings.yaml`. Useful keys:
  - `accept_threshold`, `borderline_delta` — how strict the agent is
  - `tool_budget_per_turn`, `tool_budget_per_refinement` — how many tools it can use
  - `tools_requiring_approval` — e.g. `["WEB_FETCH", "TABLE_QUERY"]`
  - `table_allowed` — tables that SQL queries may read from (read‑only)
- Environment variables override YAML. Put local values in `.env`.

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
- GoV: `POST /gov/check` with `{ "dag": { nodes, edges }, "verified_pcn": ["id1", ...] }` → `{ ok, failures }`

Notes
- Python version for dev is 3.14 (see `.python-version`).
- Optional vector extras (FAISS/LanceDB) can be enabled later with `make install-vector`.

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
