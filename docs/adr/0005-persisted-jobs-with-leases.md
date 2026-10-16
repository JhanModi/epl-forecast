# 0005. Persist background jobs with leases

Date: 2026-10-10 · Status: accepted

## Context
Refresh jobs were tracked in a dictionary inside the API process. Their status was lost on restart, a crash left
no record, and a second worker could start a second refresh at the same time.

## Options
1. Document a single-process limit and keep the dictionary. Restarts still lose history.
2. An external queue (Celery, RQ). Too much infrastructure for one weekly job.
3. **Jobs table plus a lease (chosen).**

## Decision
A `jobs` table holds each job's state. A partial unique index (`uq_jobs_one_active_per_kind`, which SQLite and
PostgreSQL both support) allows at most one queued or running job of each kind across all processes; a second
start returns 409. The process running a job renews `lease_expires_at` from a background thread. An active job
whose lease has lapsed is marked failed, as interrupted, at startup and before any new job starts.

## Consequences
- Job history survives restarts, and a job that died with its process is recorded as failed instead of looking
  "running" forever. Tests cover restart, conflict, crash recovery, error recording and lease renewal.
- The lease, rather than "fail every running job at startup", is what keeps recovery correct while another live
  worker still owns a job.
- The work itself still runs inside the API process (FastAPI background tasks). A long refresh competes with
  requests for CPU. That is acceptable for a weekly job, and it would move to a separate worker at larger scale.
