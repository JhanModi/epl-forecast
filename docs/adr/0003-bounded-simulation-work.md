# 0003. Bound simulation work

Date: 2026-10-10 · Status: accepted

## Context
`GET /seasons/{season}/simulation` originally accepted any `runs` value from 1,000 to 50,000. One request at
50,000 runs took about 1.7 s of CPU and 171 MiB of peak memory, and every distinct value created a new cache row.
Unauthenticated clients could therefore force unbounded CPU, memory and storage use.

## Options
- Allowed sizes only. This bounds the number of distinct keys, but repeated requests for *different* matchweeks
  or seasons are still expensive.
- Precompute everything at build time. Every (season, matchweek, size) cell would be computed whether or not
  anyone asks for it, and the cache would still need invalidation.
- A job queue with workers. This is correct but heavy for a single-instance project.
- **Layered in-process controls (chosen).**

## Decision
Uncached work is admitted through four controls, implemented in `epl.api.limits` and
`epl.services.simulation_cache`:
1. `runs` must be one of `EPL_SIMULATION_RUN_SIZES` (default 1,000 or 10,000); anything else returns 422.
2. **Single-flight:** concurrent requests for the same key share one computation.
3. **Per-client token bucket:** at most `EPL_SIMULATION_RATE_PER_MINUTE` new computations per client per minute;
   excess requests get 429 with `Retry-After`. Cache hits never consume tokens.
4. **Concurrency gate:** at most `EPL_SIMULATION_MAX_CONCURRENCY` computations at once. A request that cannot
   start within `EPL_SIMULATION_QUEUE_TIMEOUT_SECONDS` gets 503 with `Retry-After`.

Storage is bounded separately: `simulation_runs` is trimmed to the newest `EPL_SIMULATION_CACHE_MAX_ROWS` rows
after every insert.

## Consequences
- With a single process (the documented operating model), peak memory from simulations is bounded by the
  concurrency limit times about 35 MiB (one 10,000-run simulation). Storage is bounded by the row cap
  (about 10 KiB per row).
- The limits are per process and the client key is the socket address. With several workers or behind a proxy,
  the bounds multiply and a proxy-level rate limit becomes the shared control. That setup is out of scope for
  this deployment and is noted in the README.
