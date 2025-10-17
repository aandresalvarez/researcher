Components Overview (Vanilla Web Components)

Core
- uamm-sidebar
  - Inputs: none
  - Behavior: lists workspaces; loads counts; create test; syncs small-screen offcanvas
  - Events: uamm:workspace-change { workspace }
  - Methods: refresh()
- uamm-context-panel
  - Inputs: data-domain-input (selector, optional)
  - Behavior: shows workspace docs/steps/path; domain suggestions; τ chip; opens Key modal
  - Methods: refresh(), loadTau()
- uamm-wizard
  - Inputs: none (reads its own modal inputs)
  - Behavior: creates workspace; issues editor key (best-effort); optional seed; updates context
- uamm-playground
  - Inputs: data-domain (default domain), URL ?q= and ?autostart=1 supported
  - Behavior: streams answer tokens; shows scores/events; computes FT/TOT latencies; copy helpers
  - Methods: start(), stop(), reset()
- uamm-obs-page
  - Inputs: none
  - Behavior: metrics, alerts, recent steps table with filters; step detail offcanvas; copy selection
- uamm-rag-page
  - Inputs: none
  - Behavior: upload file (multipart), ingest folder, search corpus; supports per-request key/ws overrides
- uamm-cp-page
  - Inputs: none
  - Behavior: fetch CP τ + stats for domain; supports optional admin key override
- uamm-evals-page
  - Inputs: none
  - Behavior: load/run suites; tuner propose/apply; ad-hoc runner; recent runs

Cross-cutting
- core/context.mjs: getContext()/setContext(); emits uamm:context-change
- core/api.mjs: apiFetch() injects headers; sse() adds workspace param
- core/debug.mjs: isDebugEnabled(); installEventLog(); localStorage flags
- compat/shim.mjs: maps legacy globals (ctxFetch, sidebarSelect, etc.) to component APIs

Notes
- All components render in light DOM for Bootstrap styling.
- No globals required; some legacy globals仍 exist for compatibility until cleanup.

