# Repository Guidelines

## Project Structure & Module Organization
- `src/uamm/`: app code (FastAPI API in `api/`; agents in `agents/`; tools in `tools/`; policy/CP in `policy/`; UQ (SNNE) in `uq/`; RAG in `rag/`; PCN in `pcn/`; GoV in `gov/`; storage in `storage/`; security in `security/`; observability in `obs/`).
- `tests/`: pytest suites and fixtures (`tests/data/`).
- `scripts/`: utilities (e.g., `check_secrets.py`, eval runners).
- `config/`, `docs/`, `data/`: configuration, docs, and local assets.

## Architecture Overview
- App factory `uamm.api.main:create_app` (FastAPI) with streaming and metrics.
- Core subsystems: RAG (`uamm/rag`), UQ+CP (`uamm/uq`, `uamm/policy`), PCN (`uamm/pcn`), GoV (`uamm/gov`), dashboards/metrics (`uamm/obs`).

## Build, Test, and Development Commands
- `make venv` — create `.venv` via uv (Python 3.14).
- `make install` — install deps (editable). Extras: `make install-vector`, `make install-ingest`.
- `make run` — start API (`uvicorn uamm.api.main:create_app --reload --factory`).
- `make test` — run pytest locally.
- `make format` / `make lint` / `make typecheck` — ruff format/check and mypy types.
- `make pre-commit` / `make pre-commit-install` — run/install hooks.
- Example: `make venv && make install && make run`.

## Coding Style & Naming Conventions
- Python 3.14, 4-space indentation, required type hints (mypy).
- Format with `ruff format`; lint with `ruff check`.
- Names: packages/modules `snake_case`; classes `PascalCase`; functions/vars `snake_case`; constants `UPPER_SNAKE`.
- Keep functions small/pure; place new modules under `src/uamm/<area>/`.

## Testing Guidelines
- Pytest; files `test_*.py`, functions `def test_*`.
- Prefer offline tests; use fixtures under `tests/data/`; avoid network/API keys.
- Add tests for new endpoints, tools, and policy paths.
- Run locally with `make test`.

## Commit & Pull Request Guidelines
- Conventional Commits preferred (`feat:`, `fix:`, `chore:`, `test:`).
- PRs: clear description, linked issues, tests for new behavior, and pass CI (`lint`, `typecheck`, `tests`).
- Update `README.md`/docs when adding endpoints, tools, or config flags.

## Security & Configuration Tips
- Never commit secrets; hooks run `scripts/check_secrets.py`. Use `.env` locally (see `.env.example`).
- Keep changes reproducible: no hidden state; persist via `src/uamm/storage/` APIs.
- Tools: implement under `src/uamm/tools/`, register in `src/uamm/tools/registry.py`, and add tests.
