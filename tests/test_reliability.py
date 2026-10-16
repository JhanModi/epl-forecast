"""Cache freshness across processes, persisted jobs across restarts and workers, idempotent versions."""

import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from epl.api.main import create_app
from epl.api.state import AppState
from epl.core.config import Settings
from epl.data.db import get_engine, session_scope
from epl.data.load import MissingSnapshotsError
from epl.data.schema import DataVersion, Job
from epl.services import jobs, pipeline
from tests import factories
from tests.conftest import ADMIN_TOKEN

API = "/api/v1"
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}


def versions(settings: Settings) -> int:
    with session_scope(settings.database_url) as session:
        return int(session.scalar(select(func.count()).select_from(DataVersion)) or 0)


# --------------------------------------------------------------------------- cache freshness
def test_data_loaded_by_another_process_is_served(seeded: Settings) -> None:
    """Regression: `epl load` while the API runs used to leave the API serving the old data."""
    with TestClient(create_app(seeded)) as client:
        before = client.get(f"{API}/meta").json()
        factories.write_snapshots(factories.match_frame(), seeded)  # every 2024 fixture now played
        pipeline.load(seeded)  # the CLI's code path, outside this app's AppState
        after = client.get(f"{API}/meta").json()

    assert after["data_version"] > before["data_version"]
    assert after["matches_played"] > before["matches_played"]


def test_model_trained_by_another_process_is_served(seeded: Settings) -> None:
    with TestClient(create_app(seeded)) as client:
        before = client.get(f"{API}/meta").json()["model_version"]
        time.sleep(1.1)  # registry versions are timestamped to the second
        pipeline.train(seeded, models=["dixon_coles"])
        after = client.get(f"{API}/meta").json()["model_version"]

    assert after != before


def test_staleness_is_bounded_by_the_check_interval(seeded: Settings) -> None:
    state = AppState(seeded, check_interval=60)
    first = state.data_version()
    factories.write_snapshots(factories.match_frame(), seeded)
    pipeline.load(seeded)

    assert state.data_version() == first  # within the interval the cached version is reused...
    state._checked_at -= 61
    assert state.data_version() > first  # ...and re-read once it has passed


def test_identical_reload_does_not_create_a_new_version(seeded: Settings) -> None:
    count = versions(seeded)
    pipeline.load(seeded)

    assert versions(seeded) == count


def test_missing_snapshots_fail_clearly(settings: Settings) -> None:
    with pytest.raises(MissingSnapshotsError, match="Run `epl extract` first"):
        pipeline.load(settings)


# --------------------------------------------------------------------------- persisted jobs
def wait_for(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(50):
        job = client.get(f"{API}/admin/jobs/{job_id}", headers=ADMIN).json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_job_history_survives_a_restart(seeded: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "extract", lambda *a, **k: [])
    with TestClient(create_app(seeded)) as first:
        job_id = first.post(f"{API}/admin/refresh", headers=ADMIN).json()["job_id"]
        assert wait_for(first, job_id)["status"] == "succeeded"

    with TestClient(create_app(seeded)) as restarted:  # a new process with the same database
        job = restarted.get(f"{API}/admin/jobs/{job_id}", headers=ADMIN).json()

    assert job["status"] == "succeeded"
    assert job["kind"] == "refresh"


def test_only_one_refresh_can_be_active_across_workers(seeded: Settings) -> None:
    jobs.start(seeded.database_url, "refresh")  # active job owned by "another worker" with a live lease

    with TestClient(create_app(seeded)) as worker:
        response = worker.post(f"{API}/admin/refresh", headers=ADMIN)

    assert response.status_code == 409


def test_a_job_whose_process_died_is_marked_failed(seeded: Settings) -> None:
    stale = jobs.start(seeded.database_url, "refresh", now=datetime.now(UTC) - timedelta(hours=1))

    with TestClient(create_app(seeded)) as restarted:  # startup recovery runs here
        job = restarted.get(f"{API}/admin/jobs/{stale.id}", headers=ADMIN).json()
        retry = restarted.post(f"{API}/admin/refresh", headers=ADMIN)

    assert job["status"] == "failed"
    assert "interrupted" in str(job["error"])
    assert retry.status_code == 202  # the dead job no longer blocks new ones


def test_failed_work_is_recorded_with_its_error(seeded: Settings) -> None:
    job = jobs.start(seeded.database_url, "refresh")

    def broken() -> None:
        raise ConnectionError("API unreachable")

    jobs.run(seeded.database_url, job.id, broken)
    stored = jobs.get(seeded.database_url, job.id)

    assert stored is not None and stored.status == "failed"
    assert stored.error == "ConnectionError: API unreachable"


def test_running_jobs_renew_their_lease(seeded: Settings) -> None:
    job = jobs.start(seeded.database_url, "refresh")
    leases: list[datetime | None] = []

    def work() -> None:
        for _ in range(3):
            time.sleep(0.12)
            with session_scope(seeded.database_url) as session:
                found = session.get(Job, job.id)
                assert found is not None
                leases.append(found.lease_expires_at)

    jobs.run(seeded.database_url, job.id, work, renew_every=0.05)

    assert len({lease for lease in leases if lease is not None}) > 1  # the lease moved while it ran


@pytest.mark.postgres
@pytest.mark.skipif("EPL_TEST_DATABASE_URL" not in os.environ, reason="needs a PostgreSQL URL")
def test_job_exclusivity_and_versions_on_postgres(settings: Settings) -> None:
    """The partial unique index and data versions behave the same on PostgreSQL as on SQLite."""
    url = os.environ["EPL_TEST_DATABASE_URL"]
    with get_engine(url).begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    pg = settings.model_copy(update={"database_url": url})
    factories.write_snapshots(factories.match_frame(), pg)
    pipeline.load(pg)
    pipeline.load(pg)  # identical reload: no new version
    assert versions(pg) == 1

    first = jobs.start(url, "refresh")
    with pytest.raises(jobs.JobConflictError):
        jobs.start(url, "refresh")
    jobs.run(url, first.id, lambda: None)
    assert jobs.start(url, "refresh").status == "queued"  # finished jobs do not block new ones
