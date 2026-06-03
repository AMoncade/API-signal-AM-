# Single image for BOTH components; the command selects which one runs.
#   API    -> uvicorn api.main:app           (default CMD)
#   Worker -> python -m worker schedule       (overridden in compose / host config)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv is the project's package/Python manager (matches local dev + uv.lock).
RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) Install dependencies first (cached layer) from the frozen lock - no project yet.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Copy source and install the project (core + worker + api packages).
COPY core ./core
COPY api ./api
COPY worker ./worker
COPY docs ./docs
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Default = the read API. The worker service overrides this (see docker-compose.yml).
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
