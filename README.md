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
  - Install: `make install` (or `make install-vector` to enable optional vector backends)
  - Run server: `make run`
  - Open docs: http://127.0.0.1:8000/docs

Ask your first question
- Non‑streaming: `curl -s -X POST http://127.0.0.1:8000/agent/answer -H 'content-type: application/json' -d '{"question":"What is modular memory?"}' | jq`
- Streaming (SSE):
  `curl -N -X POST http://127.0.0.1:8000/agent/answer/stream -H 'content-type: application/json' -d '{"question":"What is modular memory?"}'`

What you’ll see with streaming
- `ready` → stream is live
- `token` → incremental text
- `score` → uncertainty and verifier scores
- `tool` → when tools run (with safety metadata)
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

Notes
- Python version for dev is 3.14 (see `.python-version`).
- Optional vector extras (FAISS/LanceDB) can be enabled later with `make install-vector`.
