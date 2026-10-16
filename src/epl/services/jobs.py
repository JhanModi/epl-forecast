"""Background jobs persisted in the database.

Why the database rather than a dict in the API process:
- **Restarts.** A job's status survives a restart, and a job that was running
  when its process died is detected and marked failed instead of looking
  "running" forever.
- **Several workers.** A partial unique index (`uq_jobs_one_active_per_kind`)
  lets at most one job of each kind be queued or running across all
  processes; a second start fails with `JobConflictError`.

Liveness uses a lease: the process running a job renews `lease_expires_at`
every `renew_every` seconds. Any active job whose lease has lapsed belonged to
a process that stopped, and `expire_stale` marks it failed. A lease (rather
than "mark everything running as failed at startup") is what keeps this
correct when another live worker owns the job.
"""

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from epl.data.db import session_scope
from epl.data.schema import Job

log = logging.getLogger(__name__)

LEASE = timedelta(seconds=120)
RENEW_EVERY = 30.0
ACTIVE = ("queued", "running")
OWNER = f"{socket.gethostname()}:{os.getpid()}"


class JobConflictError(Exception):
    """A job of this kind is already queued or running somewhere."""


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite drops timezone information on read; every stored timestamp is UTC."""
    if moment is None:
        return None
    stamp = pd.Timestamp(moment)
    return (stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)).to_pydatetime()


def _now() -> datetime:
    return datetime.now(UTC)


def expire_stale(url: str, now: datetime | None = None) -> int:
    """Mark active jobs with a lapsed lease as failed. Returns how many were expired."""
    now = now or _now()
    expired = 0
    with session_scope(url) as session:
        for job in session.scalars(select(Job).where(Job.status.in_(ACTIVE))):
            lease = _utc(job.lease_expires_at)
            if lease is None or lease < now:
                job.status, job.finished_at = "failed", now
                job.error = f"interrupted: lease expired (owner {job.owner} stopped without finishing)"
                expired += 1
    if expired:
        log.warning("Marked %d interrupted job(s) as failed", expired)
    return expired


def start(url: str, kind: str, now: datetime | None = None) -> Job:
    """Create a queued job, or raise JobConflictError if one of this kind is already active."""
    now = now or _now()
    expire_stale(url, now)
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, status="queued", owner=OWNER, created_at=now)
    job.lease_expires_at = now + LEASE
    try:
        with session_scope(url) as session:
            session.add(job)
    except IntegrityError as exc:
        raise JobConflictError(f"a {kind} job is already queued or running") from exc
    return job


def get(url: str, job_id: str) -> Job | None:
    with session_scope(url) as session:
        return session.get(Job, job_id)


def _update(url: str, job_id: str, **fields: object) -> None:
    with session_scope(url) as session:
        job = session.get(Job, job_id)
        if job is not None:
            for name, value in fields.items():
                setattr(job, name, value)


@contextmanager
def _lease(url: str, job_id: str, renew_every: float) -> Iterator[None]:
    """Renew the job's lease in a background thread while the body runs."""
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(renew_every):
            _update(url, job_id, lease_expires_at=_now() + LEASE)

    thread = threading.Thread(target=renew, name=f"lease-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def run(url: str, job_id: str, work: Callable[[], None], renew_every: float = RENEW_EVERY) -> None:
    """Execute `work` as job `job_id`, recording running / succeeded / failed."""
    _update(url, job_id, status="running", started_at=_now(), lease_expires_at=_now() + LEASE)
    try:
        with _lease(url, job_id, renew_every):
            work()
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        _update(url, job_id, status="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}")
    else:
        _update(url, job_id, status="succeeded", finished_at=_now())
