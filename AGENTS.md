# Repository Guidelines

## Project Structure & Module Organization
- `src/uamm/`: application code (FastAPI API in `api/`, agent logic in `agents/`, tools in `tools/`, policies/CP in `policy/`, uncertainty (SNNE) in `uq/`, RAG in `rag/`, numbers/PCN in `pcn/`, GoV in `gov/`, storage in `storage/`, security in `security/`, observability in `obs/`).
- `tests/`: pytest suites and fixtures (see `tests/data/`).
- `scripts/`: utilities (e.g., `check_secrets.py`, demo eval runner).
- `config/`, `docs/`, `data/`: configuration, docs, and local assets.

## Build, Test, and Development Commands
- `make venv` — create `.venv` using uv (Python 3.14).
- `make install` — install project deps into `.venv` (editable).
- `make run` — start API (`uvicorn uamm.api.main:create_app --reload`).
- `make test` — run pytest with local settings.
- `make format` / `make lint` / `make typecheck` — ruff format/check and mypy.
- `make pre-commit` / `make pre-commit-install` — run/install git hooks.

Example local dev: `make venv && make install && make run`

## Coding Style & Naming Conventions
- Python 3.14, 4‑space indentation, type hints required (mypy enforced).
- Format with ruff (`ruff format`), lint with ruff (`ruff check`).
- Naming: packages/modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`, constants `UPPER_SNAKE`.
- Place new modules under `src/uamm/<area>/` and keep functions small and pure where possible.

## Testing Guidelines
- Framework: pytest. Name files `test_*.py`, tests `def test_*`.
- Run locally with `make test`. CI runs coverage and smoke evals.
- Prefer offline tests: use fixtures in `tests/data/`, avoid network/API keys.
- Add tests for new endpoints, tools, and policy paths.

## Commit & Pull Request Guidelines
- Use clear messages (Conventional Commits preferred: `feat:`, `fix:`, `chore:`, `test:`).
- PRs must: describe changes, link issues, include tests for new behavior, and pass CI (`lint`, `typecheck`, `tests`).
- Update `README.md`/docs when adding endpoints, tools, or config flags.

## Security & Configuration Tips
- Do not commit secrets; hooks run `scripts/check_secrets.py` to block keys. Use `.env` locally (see `.env.example`).
- Keep changes reproducible: no hidden state; persist through `src/uamm/storage/` APIs.
- When adding a tool, register it in `src/uamm/tools/registry.py` and add tests under `tests/`.
