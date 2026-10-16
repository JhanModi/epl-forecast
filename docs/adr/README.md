# Architecture decision records

Short records of decisions that shape the system: the context, the alternatives considered, the choice and its
consequences. All were written on 2026-10-10 while building and hardening this version of the project; none
describes an earlier history.

| ADR | Decision |
| --- | --- |
| [0001](0001-dataset-scoping-and-demo-league.md) | Scope all data by dataset; ship a synthetic demo league; keep restricted data local |
| [0002](0002-point-in-time-features.md) | Build features with as-of joins through one code path for training and serving |
| [0003](0003-bounded-simulation-work.md) | Bound simulation work with allowed sizes, coalescing, a rate limit, a gate and cache retention |
| [0004](0004-cache-invalidation-by-data-version.md) | Invalidate in-process caches from persisted data and model versions |
| [0005](0005-persisted-jobs-with-leases.md) | Persist background jobs in the database with leases |
| [0006](0006-goals-model-in-production.md) | Serve a Dixon-Coles goals model; keep classifiers as challengers |
| [0007](0007-single-origin-typed-contract.md) | One container, one origin, and a generated TypeScript contract |
