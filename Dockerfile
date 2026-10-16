# syntax=docker/dockerfile:1.7
# One image serves the API and the dashboard from a single origin.

# ---- 1. Build the React dashboard --------------------------------------------
FROM node:22-alpine AS dashboard
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Resolve Python dependencies (cached layer) ---------------------------
FROM python:3.12-slim AS python-deps
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --extra postgres

# ---- 3. Runtime --------------------------------------------------------------
FROM python:3.12-slim AS runtime
# The image ships only redistributable data (.dockerignore allow-list, docs/adr/0001): the demo league by default.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    EPL_LOG_JSON=true \
    EPL_DATASET=demo \
    EPL_DATA_ROOT=/app/data \
    EPL_FRONTEND_DIST=/app/frontend/dist
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY --from=python-deps /app/src /app/src
COPY --from=dashboard /app/frontend/dist /app/frontend/dist
COPY data/ /app/data/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Bake a ready-to-serve database and trained models into the image (offline, ~10 s), then fail the build
# if a restricted dataset somehow reached it.
# A CRLF checkout (Windows) would break the shebang ("/bin/sh\r"), so line endings are normalised here too.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh \
    && epl load && epl train \
    && test ! -e /app/data/premier-league && test ! -e /app/data/artifacts/premier-league \
    && chown -R app:app /app/data

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status != 200)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
