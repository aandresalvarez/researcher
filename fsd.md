# FSD — Per‑Folder Workspaces and Per‑Workspace Tool Allowlists (UAMM)

This document specifies the design and implementation to support:
- Per‑folder workspaces (each with its own storage root: DB, docs, vectors).
- Per‑workspace tool allowlists, enforced by the agent and HTTP routes.

The design aims to preserve backwards compatibility (single‑DB mode) and reuse the existing policy overlay framework and RBAC.

## Summary

- Add `root` (absolute/normalized path) to `workspaces` table.
- Resolve per‑request workspace paths (`db_path`, `docs_dir`, `lancedb_uri`) from the workspace `root`.
- Introduce `tools_allowed` policy key; enforce in agent orchestration and in tool‑exposing routes (defense‑in‑depth).
- Default remains single‑DB/single docs root; multi‑workspace is opt‑in by supplying roots.

## Goals

- Independence: each workspace uses its own SQLite DB and docs folder.
- Encapsulation: tool availability and guardrails configurable per workspace.
- Backwards compatible: current flows and tests continue to work.
- Minimal operational overhead: easy creation via API/CLI.

## Non‑Goals

- Cross‑workspace sharing or joins across per‑workspace DBs (future).
- Multi‑tenant auth beyond current API key RBAC.

## Glossary

- Index DB: the existing database at `settings.db_path` holding `workspaces`, `workspace_keys`, and `workspace_policies`.
- Workspace DB: a new DB per workspace under `<root>/uamm.sqlite` holding `memory`, `corpus`, `steps`, etc.

## Architecture Overview

1) Workspace metadata and policies live in the Index DB (unchanged), with new `workspaces.root` field.
2) For a request, after role/workspace resolution, derive:
   - `request.state.db_path` → `<root>/uamm.sqlite` (else falls back to `settings.db_path`).
   - `request.state.docs_dir` → `<root>/docs` (else `settings.docs_dir`).
   - `request.state.lancedb_uri` → `<root>/vectors` (else `settings.lancedb_uri`).
3) Agent/handlers use `request.state.*` paths for IO.
4) A `tools_allowed` list (policy overlay) controls tool usage; the agent and routes refuse disallowed tools.

## Data Model Changes

- Table: `workspaces`
  - Add column `root TEXT` (nullable). When null, workspace uses single‑DB defaults.

Migration:
- `src/uamm/storage/db.py:ensure_migrations` adds `ALTER TABLE workspaces ADD COLUMN root TEXT` if missing.

## Configuration Changes

Add to `src/uamm/config/settings.py`:
- `workspace_mode: str` — `"single"|"multi"` (default: `"single"`).
- `workspace_base_dirs: list[str]` — optional allowed base directories; empty → allow any (dev), non‑empty → restrict roots.
- `workspace_restrict_to_bases: bool` — default `True` in non‑dev envs.

Usage:
- In dev, `workspace_base_dirs` may be empty. In prod, set to a parent dir (e.g., `data/workspaces`).

## New Helper Module

Add `src/uamm/storage/workspaces.py`:
- `def normalize_root(path: str) -> str` — resolve symlinks, return absolute string.
- `def ensure_allowed_root(path: str, base_dirs: tuple[str, ...], restrict: bool) -> None` — raise on traversal/outside allowed bases when `restrict`.
- `def ensure_workspace_fs(root: str, schema_path: str) -> str` — create `<root>`, `<root>/docs`, `<root>/vectors` (directories), initialize `<root>/uamm.sqlite` with schema; return path to workspace DB.
- `def get_workspace_record(index_db: str, slug: str) -> dict | None` — read `workspaces` row, including `root`.
- `def resolve_paths(index_db: str, slug: str, settings: Settings) -> dict` — returns `{"db_path": ..., "docs_dir": ..., "lancedb_uri": ...}` with sensible fallbacks when `root` missing.

## Middleware & Request Path Resolution

Add a lightweight resolver in `src/uamm/api/main.py` after `AuthMiddleware` and `WorkspaceMiddleware`:
- For each request:
  - Read `slug = request.state.workspace`.
  - Lookup workspace in Index DB to get `root`.
  - Call `resolve_paths(...)` and set `request.state.db_path`, `request.state.docs_dir`, `request.state.lancedb_uri`.
  - If lookup fails or `root` missing → set state using `settings.*` (single mode).

Notes:
- Keep `settings.db_path` as Index DB; do not mutate `settings` per request.
- Routes/agent must read from `request.state` first, then fallback to `settings`.

## Policy: tools_allowed

- Extend policy overlay to accept `tools_allowed: list[str]`.
- Update `src/uamm/config/policy_packs.py` allowed keys to include `"tools_allowed"`.
- Store the list in `workspace_policies.json` as part of normal overlay.

### Policy Application Sites

- `src/uamm/api/routes.py` (both `/agent/answer` and `/agent/answer/stream`): when applying overlay, extract `tools_allowed` and pass into agent params as `tools_allowed`.
- `src/uamm/api/routes.py:/table/query` and any route that directly executes a tool capability must enforce allowlist (see Enforcement).

## Enforcement

Layered checks to avoid accidental bypass:

1) Agent Orchestration — `src/uamm/agents/main_agent.py`
   - New helper: `def _tool_allowed(name: str, allowed: set[str] | None) -> bool`.
   - At every tool invocation decision (WEB_SEARCH, WEB_FETCH, MATH_EVAL, TABLE_QUERY), check `tools_allowed` (from params). If not allowed:
     - Emit `event: tool` with `{status: "blocked", name, reason: "not_allowed"}`.
     - Do not decrement budgets; continue to other refinements as applicable.
   - Ensure `tools_used` only tracks executed tools; blocked tools are not included.

2) HTTP Routes:
   - `/table/query` — before SQL validation, check allowlist: if `"TABLE_QUERY"` not allowed → return 403.
   - Additional tool‑like endpoints (future) should adopt the same guard.

3) Flujo ToolNode (defensive):
   - Allow injecting `allowed_tools` into node execution path; error on unknown/disallowed tools when used outside the agent pipeline.

## API Changes

1) Create Workspace
- Endpoint: `POST /workspaces` (admin)
- Request: `{ slug: str, name?: str, root?: str }`
- Behavior:
  - Validate `root` (if provided): normalize, check against allowed base dirs when `workspace_restrict_to_bases`.
  - Insert/UPSERT into `workspaces(slug, name, root, created)`.
  - If `root` is provided, call `ensure_workspace_fs(root, schema.sql)` to initialize workspace DB/dirs.
  - Response: `{ workspace: { id, slug, name, created, root? } }`

2) Table Query
- No shape change; adds allowlist enforcement: 403 with `{code: "tool_forbidden"}` when `TABLE_QUERY` disallowed.

3) Agent Answer/Stream
- No shape change; params to agent now include `tools_allowed` and `db_path` derived from request state.

4) Upload/Ingest (RAG)
- No shape change; use `request.state.docs_dir` and `request.state.db_path`.

## CLI Changes

`scripts/workspace_keys.py`
- `create` subcommand gains `--root PATH`. On set, perform the same validation and init as API.
- Echo: `{ "created": slug, "root": "..." }`.

## Settings & Defaults

- `workspace_mode` — default `"single"`. When set to `"multi"`, API logs a startup note.
- `workspace_base_dirs` — default `[]` in dev; strongly recommended to set in prod.
- `workspace_restrict_to_bases` — default `True` when `env != dev`.

## Files Touched (planned)

- `src/uamm/memory/schema.sql`
  - Add `root` column to `workspaces` (schema kept compatible; migration adds when missing).

- `src/uamm/storage/db.py`
  - `ensure_migrations`: add `workspaces.root` column if missing.

- `src/uamm/security/auth.py`
  - `create_workspace(conn, slug, name=None, root=None)`; persist `root`.

- `scripts/workspace_keys.py`
  - `create` accepts `--root` and initializes FS when provided.

- `src/uamm/storage/workspaces.py` (new)
  - Helpers for path normalization/validation, FS initialization, and path resolution.

- `src/uamm/api/main.py`
  - Add path‑resolver step to set `request.state.db_path/docs_dir/lancedb_uri`.

- `src/uamm/api/routes.py`
  - Workspace create: accept `root`, validate + initialize.
  - Read `request.state.db_path`/`docs_dir` in agent, rag, and table endpoints.
  - Pass `tools_allowed` to the agent; early 403 for `/table/query` when disallowed.

- `src/uamm/config/policy_packs.py`
  - Allow `tools_allowed`.

- `src/uamm/agents/main_agent.py`
  - Enforce allowlist per tool; emit `tool` blocked events.

## Security Considerations

- Path Validation:
  - Use `Path(root).resolve()` to normalize and prevent traversal.
  - If `workspace_restrict_to_bases`, ensure `root` is within one of `workspace_base_dirs` (resolved paths); reject otherwise.
  - Disallow roots pointing to files; ensure directories exist with `0o750` permissions (configurable later).

- Privilege Boundaries:
  - Keep Index DB authoritative for workspaces/keys/policies; per‑workspace DBs never store keys.
  - Preserve existing egress policy and SQL guard; tool allowlist is additive.

- Observability:
  - Count blocked tools per workspace: `metrics["tools_blocked_total"]` incremented by tool name.
  - SSE emits `tool` events with `status: "blocked"` for transparency.

## Backwards Compatibility

- No change when no `root` is present (single‑DB mode).
- Existing policies remain valid; `tools_allowed` is optional. If not set, current behavior continues (all tools available subject to budgets/approvals/guards).
- Tests relying on default `settings.db_path` continue working.

## Testing Plan

Add new tests:

- `tests/test_workspace_roots.py`
  - Create workspace with `root` under a tmp dir; assert Index DB stores `root`.
  - Verify `<root>/uamm.sqlite` exists after creation; tables present.
  - POST `/rag/docs` with `X-Workspace` uses the workspace DB (verify `corpus` rows go into workspace DB, not Index DB).

- `tests/test_tools_allowlist.py`
  - Apply `tools_allowed` policy without `TABLE_QUERY`; POST `/table/query` → 403.
  - Apply `tools_allowed` excluding `WEB_FETCH`; `/agent/answer` with a question that triggers fetch → `tool` blocked in SSE; result completes without fetch.

- `tests/test_path_validation.py`
  - Attempt to create workspace with `root` outside allowed base → 400/403 as applicable.

Update existing tests as needed to read from `request.state.db_path` (implementation detail only; API shape unchanged).

## Observability

- Extend in‑memory metrics (`app.state.metrics`):
  - `tools_blocked_total` (dict of `{tool: int}`), optionally `by_workspace` split.
- Expose in `GET /metrics` JSON and Prometheus exporter (if trivial).

## Rollout Plan

Phase 1 (low risk):
- Add `tools_allowed` policy key and enforcement in agent + `/table/query`.

Phase 2 (opt‑in roots):
- Schema/migration for `workspaces.root`.
- Add resolver middleware and use `request.state.db_path/docs_dir/lancedb_uri`.
- Workspace creation with `root` + FS initialization.

Phase 3 (polish):
- Optional: background docs watcher iterates all workspaces; TTL cleaner across per‑workspace DBs.
- Metrics for blocked tools and path resolution errors.

## Implementation Steps (Map to Code)

1) Schema & Migrations
- Update `src/uamm/memory/schema.sql` (`workspaces` table comment) and `ensure_migrations` to add `root`.

2) Policy Packs
- `src/uamm/config/policy_packs.py`: add `"tools_allowed"` to the allowed key set.

3) Security/Auth utils
- `src/uamm/security/auth.py`: extend `create_workspace(..., root=None)`; include `root` column on insert.

4) Workspace FS module
- Add `src/uamm/storage/workspaces.py` with helpers described above.

5) API Middleware
- In `src/uamm/api/main.py`, add a resolver after `WorkspaceMiddleware` to set `request.state.db_path/docs_dir/lancedb_uri`.

6) Routes
- `POST /workspaces`: accept `root`, validate/init FS, persist `root` field.
- RAG endpoints: switch to `request.state.*` paths.
- `/agent/answer` and `/agent/answer/stream`: pass `db_path` and `tools_allowed` into agent params (prefer state to settings).
- `/table/query`: 403 when disallowed; use resolved DB path.

7) Agent
- `src/uamm/agents/main_agent.py`: accept `tools_allowed` param and block disallowed tool uses with `tool` events (`status: blocked`).

8) CLI
- `scripts/workspace_keys.py create --root PATH` support; validate/init FS similarly to API.

9) Tests & Docs
- Add tests above; update `README.md` examples for new workspace `root` and `tools_allowed` usage.

## API/CLI Examples

Create workspace with root (API):
```
POST /workspaces
{ "slug": "team1", "name": "Team 1", "root": "data/workspaces/team1" }
```

Apply policy with limited tools (API):
```
POST /workspaces/team1/policies/apply
{ "name": "custom", "overlay": { "tools_allowed": ["MATH_EVAL", "TABLE_QUERY"] } }
```

CLI creation with root:
```
PYTHONPATH=src .venv/bin/python scripts/workspace_keys.py create team1 --root data/workspaces/team1
```

## Risks & Mitigations

- Misconfiguration of roots → path traversal. Mitigate with strict normalization and optional base dir restriction.
- Mixed single/multi mode complexity. Keep fallbacks simple and centralized in resolver.
- Agent/tool code drift. Add tests for blocked events and explicit 403s on routes.

## Acceptance Criteria

- Creating a workspace with `root` initializes `<root>/uamm.sqlite` and `<root>/docs`.
- RAG doc ingestion/search/read/write for the workspace use the workspace DB/dirs (not Index DB).
- `tools_allowed` blocks tools in agent execution (SSE shows `status: blocked`) and in `/table/query` (returns 403).
- All existing tests continue to pass in single mode; new tests pass in multi mode.

## Open Questions

- Should ingestion endpoints themselves be gated by a `TOOLS_INGEST` capability? For now, rely on role (editor/admin) and workspace policy; can add later.
- Background watchers across many workspaces: opt‑in via a `workspace_watch_all` setting to avoid surprises in prod.

