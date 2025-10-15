# UI PRD — UAMM Web UI (FastAPI + HTMX + Bootstrap)

## Summary
Build a server‑rendered UI under `/ui` to test and iterate the agent on the same FastAPI server. Use Jinja2 templates, HTMX (with SSE extension), and Bootstrap. Priorities: fast iteration, streaming answers with rich events, and light admin views for RAG, steps/metrics, workspaces, and CP thresholds.

## Goals
- Single process/server (no SPA build).
- Playground that streams tokens and shows tool, score, PCN, and GoV events.
- RAG upload/ingest/search for quick data exercises.
- Steps and metrics visibility for debugging.
- Minimal workspace/key and CP threshold views.

## Non‑Goals
- Public multi‑tenant portal or complex auth flows (OAuth, RBAC UI).
- Client‑side routing/SPA or build pipelines.

## Information Architecture
- Playground: `/ui` — form inputs (question, domain, workspace key, toggles for planning/guardrails/CP); live stream output; final JSON; copy cURL.
- RAG: `/ui/rag` — upload file, ingest folder path, search query; list results/snippets.
- Steps & Metrics: `/ui/obs` — recent steps table + detail drawer; quick metrics summary.
- Workspaces: `/ui/workspaces` — create/list workspaces; list/issue keys (if API exposed) or CLI guidance.
- CP: `/ui/cp` — view CP thresholds and basic stats by domain.

## Technical Approach
- Router: `src/uamm/api/ui/` with `APIRouter(prefix="/ui")`; mount in `create_app`.
- Templates: `src/uamm/api/templates/` (`base.html`, page templates, `partials/`).
- Static: Bootstrap CDN; HTMX + htmx‑sse via CDN; optional local CSS under `static/`.
- SSE wrapper: `GET /ui/agent/stream` adapts internal streaming events into HTML fragments with `hx-swap-oob`, targeting `#answer`, `#tools-log`, `#scores`, `#pcn`, `#gov`.
- Forms use HTMX (`hx-get` to `/ui/agent/stream`) with query/headers (Authorization) captured from inputs; no secrets in logs.

## Security & Privacy
- Accept API key via form; pass as `Authorization: Bearer …` through HTMX headers; optionally store in a signed cookie (opt‑in). Mask keys in rendered pages.

## Observability & Testing
- Reuse existing metrics; minimal UI logging. Offline tests via `TestClient`: template renders, SSE wrapper yields expected fragments, RAG actions mocked.

## Rollout Plan (Milestones)
1) Skeleton + Playground (router, templates, SSE wrapper, basic streaming).
2) RAG UI (upload/ingest/search).
3) Steps & Metrics (recent steps, metrics summary).
4) Workspaces & CP (create/list, thresholds viewer) + polish.

## Success Criteria
- Local run: `make run` serves `/ui` with streaming that reflects token/tool/score events.
- RAG upload/search works against local data.
- Steps list renders and links to details; CP threshold page loads.
- Lint, typecheck, and tests pass.

## Risks & Mitigations
- SSE + HTMX OOB fragility → provide simple append fallback and keep fragments small.
- Large responses → chunking and truncation safeguards.
- Key handling → never echo raw tokens; allow header input/cookie opt‑in.
