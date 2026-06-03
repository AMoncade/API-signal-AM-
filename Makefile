# Signals API — dev tasks.
#
# NOTE: `make` is not installed by default on Windows. If you don't have it,
# run the underlying `uv ...` commands shown in the README directly.

.PHONY: install run-api run-worker test

# Install deps (incl. dev) into the project venv using the pinned Python 3.11.
install:
	uv sync --extra dev

# Run the read-only API and serve /health at http://localhost:8000/health
run-api:
	uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Run the scheduled-ingestion worker (Phase 0: scaffold only, no jobs yet).
run-worker:
	uv run python -m worker

# Run the test suite.
test:
	uv run pytest
