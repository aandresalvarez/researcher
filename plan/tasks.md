
# UI Tasks — UAMM Web UI (FastAPI + HTMX + Bootstrap)

This tracker mirrors the PRD in `plan/PRD.MD`. Update statuses as work progresses.

Status legend: [ ] todo, [wip] in progress, [x] done, [b] blocked

## Milestone 1 — Skeleton + Playground
- [x] M1-T1: Create UI router `src/uamm/api/ui/` with `APIRouter(prefix="/ui")`.
- [x] M1-T2: Mount UI router in `src/uamm/api/main.py:create_app`.
- [x] M1-T3: Add templates folder `src/uamm/api/templates/` with `base.html`, navbar, and minimal layout.
- [x] M1-T4: Implement `/ui` playground page with form inputs (question, domain, API key, toggles for planning/guardrails/CP).
- [x] M1-T5: Implement `GET /ui/agent/stream` SSE wrapper; initial UI consumes with a minimal EventSource JS to update `#answer`, `#tools-log`, `#scores`, `#pcn`, `#gov`.
- [x] M1-T6: Wire client stream (vanilla EventSource now; HTMX OOB optional later).
- [x] M1-T7: Tests — render `/ui` (offline).

Acceptance (M1)
- `/ui` renders; submitting a question streams tokens and logs; no secrets echoed.

## Milestone 2 — RAG UI
- [x] M2-T1: Page `/ui/rag` with tabs for Upload, Ingest Folder, Search.
- [x] M2-T2: Upload form → POST to existing API `/rag/upload-file`; show result toast.
- [x] M2-T3: Ingest folder form → POST to `/rag/ingest-folder`; show progress/result.
- [x] M2-T4: Search form → GET `/rag/search?q=...`; render top‑k with snippets, metadata.
- [x] M2-T5: Tests — render page, mock responses, offline.

Acceptance (M2)
- Can upload, ingest a folder path, and search; results visible.

## Milestone 3 — Steps & Metrics
- [x] M3-T1: Page `/ui/obs` rendering recent steps via `GET /steps/recent`.
- [x] M3-T2: Step detail drawer (fetch by id; reuse existing API) with trace JSON.
- [x] M3-T3: Metrics summary using `GET /metrics` (basic stats table).
- [x] M3-T4: Tests — render page offline.

Acceptance (M3)
- Recent steps and a simple metrics view are usable for debugging.

## Milestone 4 — Workspaces & CP + Polish
- [x] M4-T1: Page `/ui/workspaces` listing workspaces (from DB) and create form (`POST /workspaces`).
- [x] M4-T2: Keys: list/issue/deactivate UI (admin-only when auth enabled).
- [x] M4-T3: Page `/ui/cp` with domain input → show `GET /cp/threshold?domain=...` result and basic stats.
- [x] M4-T4: Copy cURL button on Playground; mask API key in UI; Bootstrap toasts for success/errors.
- [x] M4-T5: Tests — render pages, basic flows offline.

Acceptance (M4)
- Workspaces can be created/viewed; CP thresholds visible; playground polished.

## Milestone 5 — Evals & Tuning
- [x] M5-T1: Page `/ui/evals` with suites list (`GET /evals/suites`).
- [x] M5-T2: Run suites (`POST /evals/run`); show per‑suite metrics and run_id.
- [x] M5-T3: Ad‑hoc items runner; show metrics and per-record table.
- [x] M5-T4: Tuner: `POST /tuner/propose` and `POST /tuner/apply`; show patch and canary summary.
- [x] M5-T5: Recent runs (`GET /evals/runs`, `GET /evals/report/{run_id}`) with view in UI.
- [x] M5-T6: Tests — render page offline.

Acceptance (M5)
- Evals can run from the UI; tuning proposals can be generated and applied; recent runs browsable.

## Milestone 6 — Workspace UX (Phase 2 & 3)
- [x] M6-T1: Global context modal (workspace + API key) stored in localStorage.
- [x] M6-T2: `ctxFetch()` wrapper to auto-attach `X-Workspace` and `Authorization` on UI calls.
- [x] M6-T3: SSE wrapper accepts `?workspace=` and Playground passes active workspace.
- [x] M6-T4: Status ribbon with counts (docs/steps), paths, and CP τ chip with domain input + refresh.
- [x] M6-T5: Workspaces dashboard card (last activity, latest doc, quick links to Playground/Obs/RAG/CP).
- [x] M6-T6: Create Test Workspace (one click) and guided Create Wizard (slug/name, root choice, editor key, optional seed).
- [x] M6-T7: Admin delete workspace (metadata-only) with confirmation; guarded server route.
- [x] M6-T8: `GET /workspaces/{slug}/stats` endpoint to power ribbon + dashboard.

Acceptance (M6)
- Setting context once scopes the entire UI; τ and stats visible; creating a test workspace yields an immediately usable environment.

## Cross‑Cutting
- [x] CC-T1: Security — centralized `ctxFetch` for Authorization; masked key inputs; avoid logging raw keys.
- [x] CC-T2: Types/lint — `make test` suite passes; formatting/linting clean per project tools.
- [x] CC-T3: Docs — in-app UI guide at `/ui/docs` covering pages, concepts, workflows.
