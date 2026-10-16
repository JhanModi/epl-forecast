# Scope, users and acceptance criteria

## Problem
Football fans and analysts can easily find opinions about who will win a match, but rarely find **calibrated
probabilities** whose track record has been checked. This project produces pre-kickoff forecasts for a
league (match result, scorelines and season outcomes) and shows, with reproducible evidence, how good those
forecasts are and how the system behaves when things go wrong.

## Intended users
| User | Need | What the project gives them |
| --- | --- | --- |
| Analyst or fan | Probabilities for upcoming matches and season outcomes | The dashboard's Overview, Match lab and Season simulator; the REST API |
| Technical reviewer | Evidence that the forecasts and the software can be trusted | Walk-forward evaluation, tests, benchmarks, ADRs and a reproducible build |
| Maintainer | A weekly refresh that cannot corrupt data or double-run | Idempotent, validated loads; persisted jobs; versioned models |

There are **no real users**. Nothing here claims adoption, traffic or production operation.

## Completed capabilities (this milestone)
- Data from three sources behind one snapshot format: a synthetic demo league (default, shareable),
  openfootball (public domain, results only) and the Premier League site (restricted, local only).
- Validation and idempotent, transactional loading into SQLite or PostgreSQL, with Alembic migrations and data
  versions.
- Six forecasters (a baseline, an Elo logit, logistic regression, gradient boosting, Dixon-Coles and an ensemble)
  under a weekly walk-forward backtest with proper scoring rules, bootstrap CIs, paired tests and calibration. On
  synthetic data the backtest also includes an oracle ceiling.
- Monte Carlo season simulation, a model registry, and a pre-kickoff prediction log with a per-match track
  record (API and dashboard).
- A FastAPI service with a typed OpenAPI contract, bounded expensive work, admin jobs and health probes.
- A React/TypeScript dashboard with table views, keyboard-operable controls, and light and dark themes.

## Acceptance criteria and how each is verified
| # | Criterion | Verified by |
| --- | --- | --- |
| AC1 | A clean checkout builds, tests and serves **offline**, using only redistributable data | `tests/test_datasets.py::test_demo_build_needs_no_network`; `scripts/check_distribution.py --docker` in CI |
| AC2 | No forecast uses information from after its kickoff | `tests/test_features.py` (leakage tests), `tests/test_evaluation.py::test_walk_forward_never_looks_ahead`, `tests/test_models.py::test_feature_models_ignore_fixture_results` |
| AC3 | The production model beats the base-rate baseline on the holdout season (paired test on RPS, p < 0.05) | `data/artifacts/demo/backtest/report.json` (demo); `data/artifacts/openfootball/backtest/report.json` (real results, p = 0.006); the author's local Premier League report (not distributed) |
| AC4 | Forecast probabilities are valid, and holdout calibration error (ECE) is below 0.06 | `tests/test_models.py::test_every_model_returns_valid_probabilities`; ECE in the backtest report |
| AC5 | Every endpoint has a typed contract; invalid input gets a 4xx RFC 9457 problem, never a 500 | `tests/test_api.py`, `tests/test_security.py`; `docs/openapi.json` checked against code in CI |
| AC6 | Expensive work is bounded: unsupported sizes return 422, a client over its rate gets 429, saturation returns 503, and cache storage stays at or below its cap | `tests/test_security.py` |
| AC7 | Admin endpoints are disabled unless a strong token is configured; the token never appears in logs or settings dumps | `tests/test_security.py` |
| AC8 | Data or models refreshed by another process are served within the check interval (1 s), without a restart | `tests/test_reliability.py` |
| AC9 | Job state survives restarts; at most one refresh can be active across processes; crashed jobs are marked failed | `tests/test_reliability.py` |
| AC10 | The dashboard's central flows work, including loading and error states and keyboard-operable view toggles | `frontend/src/test/flows.test.tsx` |
| AC11 | The track record shows, per match, the latest forecast logged strictly before kickoff (compared in UTC); its pages neither overlap nor skip; each RPS equals `metrics.rps`, and the average matches `/predictions/live` | `tests/test_track_record.py` |

Performance is **measured, not asserted**. `scripts/benchmark.py` reports latency, throughput, rejections and
resource use for a stated workload and machine (see `docs/benchmarks/`). No pass/fail threshold was set in
advance, so none is claimed.

## Explicitly out of scope
- Betting advice, odds comparison and any real-money use. A bookmaker benchmark is planned but not built.
- In-play updates, line-ups, injuries and player-level models.
- End-user accounts and authentication. The only privileged function is the admin refresh, behind a shared
  token.
- High availability and multi-instance deployment. The documented operating model is one API process; the
  resource limits are per process (`docs/adr/0003-bounded-simulation-work.md`).
- Publishing Premier League-derived data or results artifacts (`docs/adr/0001-dataset-scoping-and-demo-league.md`).
- Cursor or snapshot pagination for the track record. Offset pages can shift if a forecast is logged between
  two requests; each response is internally consistent (one SQL statement).
