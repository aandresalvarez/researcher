PY_VERSION ?= 3.14
VENV ?= .venv

.PHONY: help venv install install-vector vector-venv run dev clean

help:
	@echo "Targets:"
	@echo "  make venv            # Create .venv with Python $(PY_VERSION)"
	@echo "  make install         # Install deps into $(VENV)"
	@echo "  make install-vector  # Install deps + vector extras"
	@echo "  make dev-tools       # Install dev tools (ruff, mypy, pre-commit) into $(VENV)"
	@echo "  make format          # Format code with ruff"
	@echo "  make lint            # Lint code with ruff"
	@echo "  make typecheck       # Type-check with mypy"
	@echo "  make pre-commit      # Run pre-commit on all files"
	@echo "  make pre-commit-install # Install pre-commit hooks"
	@echo "  make vector-venv     # Create dedicated .venv-vector (Python 3.11) and install vector extras"
	@echo "  make run             # Run API with uvicorn (reload)"
	@echo "  make dev             # venv + install + run"
	@echo "  make clean           # Remove venv"

venv:
	uv python install $(PY_VERSION)
	uv venv -p $(PY_VERSION) $(VENV)
	@echo "Activate: source $(VENV)/bin/activate (Unix) or .\\$(VENV)\\Scripts\\activate.ps1 (Windows PowerShell)"

install:
	uv pip install -p $(VENV) -e .

install-vector:
	uv pip install -p $(VENV) -e .[vector]

vector-venv:
	uv python install 3.11
	uv venv -p 3.11 .venv-vector
	uv pip install -p .venv-vector -e .[vector]
	@echo "Vector environment ready. Activate with: source .venv-vector/bin/activate"

run:
	PYTHONPATH=src $(VENV)/bin/uvicorn uamm.api.main:create_app --reload --factory

dev: venv install run

test:
	PYTHONPATH=src:. $(VENV)/bin/pytest -q

dev-tools:
	uv pip install -p $(VENV) ruff mypy pre-commit

format: dev-tools
	$(VENV)/bin/ruff format src tests scripts

lint: dev-tools
	$(VENV)/bin/ruff check src tests scripts

typecheck: dev-tools
	$(VENV)/bin/mypy --config-file mypy.ini src

pre-commit: dev-tools
	$(VENV)/bin/pre-commit run --all-files --show-diff-on-failure

pre-commit-install: dev-tools
	$(VENV)/bin/pre-commit install

eval-demo:
	PYTHONPATH=src $(VENV)/bin/python scripts/run_demo_evals.py evals/demo.json demo-$(shell date +%s)

clean:
	rm -rf $(VENV)
