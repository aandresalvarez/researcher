Executive Summary

  - Strengths: Clean FastAPI app factory, good modularity, strong test coverage, thoughtful safety features (egress policy, SQL guard,
    prompt guard), and a working UI. As of 2025-10-17, tests pass locally: 118 passed, 3 skipped in ~2m20s.
  - Reality check: For most “ask a question with some RAG” use cases, this is overbuilt by default. Too many subsystems are on the
    critical path, install base is heavy, config surface is large, and a few implementation choices increase friction.
  - Direction: Keep the advanced capabilities, but ship a Core profile that’s easy to run and reason about. Reduce default deps, narrow
    the request→decision surface, and make workspace policy overlays per-request (not global mutable state).

  Developer Experience

  - Python version friction: pyproject requires-python >= 3.14 (pyproject.toml). That’s uncommon today and blocks many environments.
  - Heavy default install: pypdf, python-docx, pytesseract, pdf2image, pillow are always installed even if users don’t need ingestion/OCR.
    These belong in extras.
  - “uv” dependency: Make targets rely on uv for Python/venv/pip. That’s fine, but a plain python -m venv + pip install -e . path should
    be obvious in README.
  - No API Dockerfile: There’s a Cloud Run job Dockerfile for backups, but no simple app container. That increases first-run friction.
  - Tests: Great. Running PYTHONPATH=src:. .venv/bin/pytest -q passed across 60+ test files. That’s a real asset for refactors.

  Architecture (fits the stated guidelines)

  - App factory is in place: uamm.api.main:create_app (src/uamm/api/main.py:160).
  - Subsystems are well-encapsulated: RAG (retriever/pack), UQ+CP (uq/, policy/), PCN (pcn/), GoV (gov/), UI and metrics.
  - Background tasks: TTL cleaner and docs watcher live in lifespan. Sensible, but default-on doc watcher will surprise some users.

  Where the complexity shows up:

  - MainAgent orchestrates many concerns: RAG, UQ (SNNE), verifier, CP, planning, approvals, PCN placeholders/verification, GoV DAG
    checks, guardrails, table query heuristics. Impressive, but high cognitive load and tuning surface for new users.
  - Settings explosion: Dozens of toggles (src/uamm/config/settings.py) that are sometimes mutated during request handling (see below).

  Correctness / Safety Issues (actionable)

  - Per-request policy overlay mutates global settings on the app instance:
      - In src/uamm/api/routes.py (answer and stream paths), workspace policy overlays call setattr(settings, ...) for accept thresholds,
        tool budgets, retriever weights, and vector config. Because settings is a single app-wide object, concurrent requests for
        different workspaces can clobber each other. This is a real concurrency and multi‑tenant correctness bug.
      - Fix: Treat workspace overlays as per-request “effective settings.” Don’t mutate app.state.settings. Instead:
          - Build a dict of “effective” parameters and pass them down via the params dict to MainAgent.
          - If a setting must be shared, store policy overlays per workspace and read them into request-local variables; never write to
            the shared settings instance.
  - Storing “JSON” as Python repr in DB:
      - Overlays use ast.literal_eval on a json column. Store actual JSON and use json.loads/json.dumps. Reduces ambiguity and keeps
        schema/tooling predictable.

  API Usability & Ergonomics

  - AnswerRequest shape is long. Good defaults exist, but the docs should emphasize the minimum happy path:
      - Required: question.
      - Nice-to-have: domain, use_memory, memory_budget.
      - Everything else should be advanced.
  - Streaming SSE:
      - Event taxonomy is rich (token, score, tool, pcn, gov, trace, heartbeat, final). Great for observability; potentially overwhelming
        for first-time users.
      - Suggest a thin “stream_lite” option that emits only ready/token/final (plus one score event at end).
  - Approvals:
      - Very useful, but default should be opt‑in per tool list. Ensure the README shows the approval flow with a short, copyable
        sequence.

  Dependencies & Footprint

  - Default deps are too heavy for “hello world.” Move these to extras and remove from [project.dependencies]:
      - pypdf, python-docx, pytesseract, pdf2image, pillow
  - Consider making openai optional for both embeddings and LLM. You already gracefully degrade to hash embeddings and a heuristic text
    fallback — advertise that clearly.
  - Vector backends:
      - You support LanceDB and FAISS adapters. Good, but keep them in extras only (which you already do for vector). Ensure the README
        stresses that default vector_backend is “none” and it still works.

  Security / Policy

  - Egress policy + prompt guard + SQL guard are strong and pragmatic (src/uamm/security/*). Good defaults: TLS required, private IP
    blocked, read‑only SQL, simple prompt-injection detection.
  - Rate limiter is process-local and role-aware. Good for dev; call out that it’s not distributed, and point to a reverse proxy or Redis
    quota in prod.
  - Secret manager supports Vault and .env fallback. Good separation. Add a quick “dev only” example showing .env with OPENAI_API_KEY.

  Observability

  - Metrics state is an in-memory dict. Fine for dev. The “dashboard summary” endpoint is handy. For production, a real metrics exporter
    (Prometheus already hinted) should back most counters/histograms; a bridge exists but not the defaults.

  Testing & Quality

  - The breadth of tests is excellent (agents, RAG, planning, CP, PCN, GoV, guardrails, UI endpoints). This makes refactoring safe.
  - Offline-friendly tests with fixtures for web search/fetch are a big plus.

  Over‑Engineering vs. User Value

  - Likely overkill for first-use:
      - PCN, GoV, planning, guardrails, approvals, tuner — all valuable but not required to answer grounded questions with a threshold and
        optional CP.
      - Default-on docs watcher and background TTL cleaner may be surprising in trivial demos.
  - Strong recommendation: Ship a Core profile by default; make the advanced systems discoverable and opt‑in.

  Top Recommendations (prioritized)

  1. Fix per-request settings mutation

  - Stop mutating app.state.settings inside answer and answer_stream. Compute per-request overlays and pass through the params dict. This
    removes cross-tenant leakage and makes behavior predictable.
  - Files: src/uamm/api/routes.py (answer paths), src/uamm/api/main.py (no change required), MainAgent already accepts params.

  2. Introduce a “Core” profile with minimal defaults

  - Profile flag: UAMM_PROFILE=core|minimal|full or settings.profile.
  - Core should disable by default: planning, approvals, PCN, GoV, guardrails, docs_auto_ingest, faithfulness LLM mode, MCP server.
  - Provide a first-run config snippet in README and a single “try it” curl/JS example.

  3. Slim the default install

  - Move ingest/OCR libs to extras only; remove from [project.dependencies].
  - Optional: split extras into ingest, ocr, vector, tables, units, formal, gcp (you already have most).
  - Provide make install-core that installs only the base set.

  4. Reduce Python version requirement

  - Set requires-python to >=3.11 (or 3.12). Nothing in the code appears to require 3.14 semantics.
  - Update .python-version and Makefile default accordingly, and keep vector-venv for 3.11 where you need it.

  5. Add a simple API Dockerfile

  - Minimal FROM python:3.12-slim, copy src/, config/, pyproject.toml, README.md, install .[core] (or base), uvicorn
    uamm.api.main:create_app.
  - Add a note on how to mount data/ for persistence.

  6. Make streaming lightweight by default

  - Add stream_lite=true to emit only ready/token/final (+ one score). Keep advanced events behind a flag.

  7. Use a real tools registry or remove it

  - src/uamm/tools/registry.py exists but MainAgent imports tools directly. Either:
      - Wire the registry and pluggable tools (keeps advanced users happy), or
      - Remove the registry to reduce cognitive load. My vote: wire it but keep the default registry small.

  8. Replace ast.literal_eval with JSON consistently

  - Store overlays and similar config blobs as JSON everywhere, not Python repr.

  9. Default-off docs watcher

  - Set docs_auto_ingest: false by default in config/settings.yaml. Mention how to turn on with OCR system prerequisites.

  10. Tighten README for a 5‑minute success

  - Add a “Quickest path (Core)” section:
      - python -m venv .venv && source .venv/bin/activate
      - pip install -e .
      - uvicorn uamm.api.main:create_app --reload --factory
      - curl -s http://127.0.0.1:8000/agent/answer -H 'content-type: application/json' -d '{"question":"What is modular memory?"}' | jq
  - Move advanced features to “Next Steps.”

  What to keep “advanced” (opt‑in)

  - CP gating (on when τ exists; otherwise off).
  - PCN/GoV pipeline.
  - Planning (ToT/MCTS) and tuner.
  - Guardrails policy packs.
  - Vector backends.
  - GCP backup job.

  If you want me to implement the core changes next

  - Minimal, high‑impact PR (1–2 days):
      - Per-request overlay fix (no global settings mutation).
      - Extras reshuffle + lighter base deps.
      - Python version lowered to 3.11/3.12.
      - Core profile defaults and README quickstart.
      - Add docs_auto_ingest: false by default.
      - JSON storage for workspace policies (with migration that re-writes current rows).
      - Optional: API Dockerfile.
  - Nice-to-have follow‑up (1–2 days):
      - Stream-lite mode.
      - Tool registry integration.
      - Makefile targets: install-core, run-core.

  References to key spots:

  - App factory: src/uamm/api/main.py:160
  - Global settings mutation (fix here): src/uamm/api/routes.py (answer and answer_stream handlers)
  - Settings surface: src/uamm/config/settings.py
  - Tools registry (unused): src/uamm/tools/registry.py
  - Heavy default deps: pyproject.toml
  - Docs watcher default-on: config/settings.yaml