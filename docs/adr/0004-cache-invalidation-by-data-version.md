# 0004. Invalidate caches from persisted versions

Date: 2026-10-10 · Status: accepted

## Context
The API cached the match frame and unpickled models in memory until an in-process refresh called `invalidate()`.
A test reproduced the problem: data loaded by another process (`epl load`) never reached a running API. A second
worker would have the same blind spot.

## Options
1. A cache time-to-live: simple, but it serves stale data for the whole TTL and reloads needlessly when nothing
   has changed.
2. Inter-process notification (Postgres `LISTEN/NOTIFY`, Redis pub/sub): more infrastructure, and SQLite has no
   equivalent.
3. **Version tags on shared persisted state (chosen).**

## Decision
Every load inserts a `data_versions` row in the same transaction as the data, but only when the content
fingerprint changed. Every training run atomically swaps the registry's `latest.json`. `AppState` tags each
cached value with the version it was built from, and re-reads the versions at most once per
`EPL_CACHE_CHECK_INTERVAL_SECONDS` (default 1 s). That costs one indexed `SELECT max(id)` and one small file read.

## Consequences
- Data or models changed by any process become visible within about a second, with no extra infrastructure. This
  works the same on SQLite and PostgreSQL.
- Re-running an identical load creates no new version, so caches are not discarded needlessly.
- Values derived from the data (team ratings, Elo history) are memoised per data version, which makes them both
  fast and correct.
