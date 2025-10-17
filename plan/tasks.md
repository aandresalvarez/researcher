Web Components UI Refactor — Tasks

Legend: [ ] pending, [x] done, [~] in progress

Milestone 0 — Bootstrap (no UI changes)
- [x] Create entry module `src/uamm/api/static/js/app.mjs` (registers custom elements, bootstraps debug flags).
- [x] Add core modules under `src/uamm/api/static/js/core/`:
  - [x] `context.mjs` — get/set workspace+apiKey with localStorage; emit `uamm:context-change`.
  - [x] `api.mjs` — `apiFetch()` adds `X-Workspace`/`Authorization`; `sse()` wrapper.
  - [x] `events.mjs` — event constants and `emit()` helper.
  - [x] `debug.mjs` — toggleable logs, optional event log overlay.
- [x] Load entry in `src/uamm/api/templates/base.html` via `<script type="module" src="/static/js/app.mjs"></script>` (keep legacy scripts temporarily).

Milestone 1 — Shell Components
- [ ] Implement `src/uamm/api/static/js/components/uamm-app.mjs` (root, feature flags).
- [ ] Implement `src/uamm/api/static/js/components/uamm-toast.mjs` (global toast hub; listens for `uamm:toast`).
- [ ] Implement `src/uamm/api/static/js/components/uamm-nav.mjs` (top navbar interactions only).
- [x] Implement `src/uamm/api/static/js/components/uamm-sidebar.mjs` (list workspaces, create test, select workspace; emits `uamm:workspace-change`).
- [x] Implement `src/uamm/api/static/js/components/uamm-context-panel.mjs` (stats + τ badge; syncs domain input when available).
- [x] Implement `src/uamm/api/static/js/components/uamm-wizard.mjs` (workspace wizard modal; key issuance, optional seed).
- [x] Insert `<uamm-sidebar>` and `<uamm-context-panel>` into `src/uamm/api/templates/base.html` in place of current sidebar/context markup (or hydrate existing markup during transition).
- [x] Add compatibility shim `src/uamm/api/static/js/compat/shim.mjs` mapping `window.ctxFetch`, `window.showToast`, `window.sidebarSelect`, `window.sidebarLoadWorkspaces` to events/APIs to keep legacy pages functioning during migration.

Milestone 2 — Playground Page
- [x] Implement `src/uamm/api/static/js/components/uamm-playground.mjs`:
  - [x] Form handling (question, domain, workspace, ref, mem, delta).
  - [x] Streaming via SSE (`/ui/agent/stream`), status badge.
  - [x] First-token and total latency metrics.
  - [x] Render tokens, final JSON; tool/score/pcn/gov event logs.
- [x] Update `src/uamm/api/templates/playground.html` to mount `<uamm-playground>`.
- [x] Remove `<script src="/static/js/playground.js">` reference from the playground template (keep file until Milestone 4 cleanup).

Milestone 3 — Other Pages
- [x] Convert `src/uamm/api/templates/obs.html` → `uamm-obs-page.mjs` (metrics + recent steps + detail drawer).
- [x] Convert `src/uamm/api/templates/rag.html` → `uamm-rag-page.mjs` (ingest/docs mgmt).
- [x] Convert `src/uamm/api/templates/cp.html` → `uamm-cp-page.mjs`.
- [x] Convert `src/uamm/api/templates/evals.html` → `uamm-evals-page.mjs`.
- [x] Convert `src/uamm/api/templates/home.html` → `uamm-home-page.mjs`.
- [x] Convert `src/uamm/api/templates/workspaces.html` → `uamm-workspaces-page.mjs`.

Milestone 4 — Cleanup
- [x] Remove legacy globals from `src/uamm/api/static/js/app.js` that are superseded by components.
- [x] Drop `compat/shim.mjs` after parity is verified across pages.
- [x] Delete `src/uamm/api/static/js/playground.js` once `<uamm-playground>` is stable.

Milestone 5 — Docs & QA
- [x] Add `plan/COMPONENTS.md` documenting component contracts (attributes, events, methods, dependencies).
- [x] Add manual smoke checklist for each page (Playground, RAG, OBS, CP, Evals) under `plan/QA.md`.
- [ ] Update `README.md` with “UI (Web Components)” section and local dev notes (no build step; ES modules only).

Cross‑Cutting Tasks
- [x] Event names are centralized in `core/events.mjs`; all components use `emit()`.
- [x] All API calls go through `core/api.mjs` (no direct `fetch()` in components).
- [x] Context updates only via `core/context.mjs`; components do not share mutable globals.
- [x] Enable optional dev overlay via `localStorage['uamm.devtools']=1` for event inspection.

Acceptance Gates
- [x] Shell parity: sidebar + context banner fully functional via components.
- [x] Playground parity: streaming, metrics, logs, copy helpers.
- [x] No `window.*` references in migrated pages; compatibility shim removed.
- [x] Source is plain JS modules under `/static/js` (no new dependencies, no build tools).
