# Convenience targets (the underlying commands work the same on Windows without make).
.PHONY: install build lint test serve dev docker clean

install:            ## Python + frontend dependencies
	uv sync --all-extras
	cd frontend && npm ci

build:              ## database + backtest + models from the committed snapshots (no network)
	uv run epl build

lint:               ## ruff, formatting, mypy, TypeScript
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy
	cd frontend && npm run typecheck

test:               ## backend and frontend tests
	uv run pytest --cov
	cd frontend && npm test

serve:              ## API (+ built dashboard) on :8000
	uv run epl serve

dev:                ## dashboard with hot reload on :5173 (run `make serve` alongside)
	cd frontend && npm run dev

docker:             ## production image backed by PostgreSQL
	docker compose up --build

clean:
	rm -rf data/epl.db* data/artifacts/models frontend/dist .pytest_cache .mypy_cache .ruff_cache
