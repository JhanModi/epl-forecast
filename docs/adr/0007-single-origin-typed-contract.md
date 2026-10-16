# 0007. One container, one origin, a generated contract

Date: 2026-10-10 · Status: accepted

## Context
The project has a Python API and a React dashboard. They could be deployed separately, which would need CORS,
two URLs and two health checks, and their interface could drift apart silently.

## Decision
- The multi-stage Docker image builds the dashboard and copies it into the Python image. FastAPI serves
  `/api/v1/*` and the static files, with single-page-app fallback; unknown `/api/*` paths stay JSON 404s.
- The Pydantic response models are the source of truth. `epl openapi` writes `docs/openapi.json`, and
  `openapi-typescript` generates `frontend/src/api/schema.d.ts`. CI regenerates both and fails on any diff. The
  frontend's test fixtures are typed against the generated schema.

## Consequences
- One URL, one healthcheck and one artifact to run anywhere containers run. Development keeps Vite's hot
  reload by proxying `/api`.
- Renaming a response field in Python breaks the TypeScript build and the frontend tests rather than users'
  screens.
