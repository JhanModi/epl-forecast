"""Operational endpoints, protected by a shared admin token (header ``X-Admin-Token``).

They are disabled (403) unless EPL_ADMIN_TOKEN is configured, and the token must
be strong (enforced at startup by `epl.core.config.Settings`).
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from epl.api import schemas
from epl.api.deps import StateDep, require_admin
from epl.api.state import AppState
from epl.data.db import session_scope
from epl.services import jobs, pipeline

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
log = logging.getLogger(__name__)

REFRESH = "refresh"


def _refresh(state: AppState) -> None:
    settings = state.settings
    pipeline.extract(settings, [settings.current_season])
    pipeline.load(settings)
    pipeline.train(settings)
    with session_scope(settings.database_url) as session:
        pipeline.log_predictions(session, settings)
    state.invalidate()  # this process sees the new data at once; others pick it up via the data version


@router.post(
    "/refresh", response_model=schemas.JobAccepted, status_code=202, summary="Pull new results and retrain"
)
def refresh(state: StateDep, background: BackgroundTasks) -> dict[str, str]:
    url = state.settings.database_url
    try:
        job = jobs.start(url, REFRESH)
    except jobs.JobConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    background.add_task(jobs.run, url, job.id, lambda: _refresh(state))
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/{job_id}", response_model=schemas.Job, summary="Status of a background job")
def job(job_id: str, state: StateDep) -> dict[str, Any]:
    found = jobs.get(state.settings.database_url, job_id)
    if found is None:
        raise HTTPException(404, f"No job {job_id}")
    return {
        "job_id": found.id,
        "kind": found.kind,
        "status": found.status,
        "owner": found.owner,
        "created_at": found.created_at,
        "started_at": found.started_at,
        "finished_at": found.finished_at,
        "error": found.error,
    }
