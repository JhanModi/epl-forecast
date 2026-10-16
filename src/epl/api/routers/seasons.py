from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Path, Query, Request

from epl.api import schemas
from epl.api.deps import StateDep
from epl.api.limits import BusyError, RateLimitedError
from epl.data.db import session_scope
from epl.models import zoo
from epl.services import forecasting, simulation_cache
from epl.services.forecasting import records

router = APIRouter(prefix="/seasons/{season}", tags=["seasons"])

SeasonPath = Annotated[int, Path(ge=2000, le=2100, description="Season start year, e.g. 2026 for 2026/27")]
AsOf = Annotated[int | None, Query(ge=1, le=38, description="Use only results before this matchweek")]


def _season_frame(state: StateDep, season: int) -> pd.DataFrame:
    frame = state.frame()
    if not (frame["season"] == season).any():
        raise HTTPException(404, f"No data for season {season}")
    return frame


@router.get("/fixtures", response_model=list[schemas.Fixture], summary="Fixtures and results")
def fixtures(
    state: StateDep,
    season: SeasonPath,
    status: Annotated[Literal["finished", "scheduled"] | None, Query()] = None,
    matchweek: Annotated[int | None, Query(ge=1, le=38)] = None,
) -> list[dict[str, Any]]:
    frame = _season_frame(state, season)
    rows = frame[frame["season"] == season]
    if status == "finished":
        rows = rows[rows["status"] == "finished"]
    elif status == "scheduled":
        rows = rows[rows["status"] != "finished"]
    if matchweek is not None:
        rows = rows[rows["matchweek"] == matchweek]
    columns = [
        "match_id",
        "season",
        "matchweek",
        "kickoff",
        "status",
        "home_team",
        "away_team",
        "home_goals",
        "away_goals",
    ]
    return records(rows[columns].astype(object).where(rows[columns].notna(), None))


@router.get("/standings", response_model=list[schemas.StandingRow], summary="League table")
def standings(state: StateDep, season: SeasonPath, as_of_matchweek: AsOf = None) -> list[dict[str, Any]]:
    frame = _season_frame(state, season)
    try:
        return state.memo(
            ("standings", season, as_of_matchweek),
            lambda: records(forecasting.table(frame, season, as_of_matchweek)),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get(
    "/simulation",
    response_model=schemas.Simulation,
    summary="Monte Carlo simulation of the rest of the season",
    description=(
        "Refits the goals model on everything known at the cutoff and simulates the remaining fixtures. "
        "Results are cached in the database, keyed by the data and model version. Uncached requests are "
        "rate limited per client (429) and share a bounded pool of workers (503 when saturated); "
        "`runs` must be one of the configured sizes (default 1,000 or 10,000)."
    ),
)
def simulation(
    *,
    request: Request,
    state: StateDep,
    season: SeasonPath,
    as_of_matchweek: AsOf = None,
    runs: Annotated[int, Query(description="Number of simulated seasons; an allowed size")] = 10_000,
) -> dict[str, Any]:
    settings = state.settings
    if runs not in settings.simulation_run_sizes:
        allowed = ", ".join(f"{n:,}" for n in settings.simulation_run_sizes)
        raise HTTPException(422, f"runs must be one of: {allowed}")
    frame = _season_frame(state, season)
    try:
        params = zoo.tuned(settings)
        key = forecasting.simulation_key(frame, season, as_of_matchweek, runs, params)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    # Short-lived sessions only: a request must never hold a pooled connection while it queues for a
    # simulation slot, or queued requests starve the slot holders of connections (see test_security.py).
    with session_scope(settings.database_url) as session:
        hit = simulation_cache.lookup(session, key)
    if hit is not None:
        return hit | {"cached": True}

    client = request.client.host if request.client else "unknown"
    try:
        state.simulation_limiter.take(client)
    except RateLimitedError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": f"{exc.retry_after:.0f}"}) from exc

    def compute() -> dict[str, Any]:
        result = forecasting.season_simulation(frame, season, as_of_matchweek, runs, params=params)
        with session_scope(settings.database_url) as writer:
            simulation_cache.store(writer, key, result, settings.simulation_cache_max_rows)
        return result

    try:
        result: dict[str, Any] = state.simulation_gate.run(key, compute)
    except BusyError as exc:
        raise HTTPException(
            503,
            "Simulation capacity is saturated; retry shortly",
            headers={"Retry-After": f"{exc.retry_after:.0f}"},
        ) from exc
    return result | {"cached": False}
