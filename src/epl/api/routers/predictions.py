from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from epl.api import schemas
from epl.api.deps import SessionDep, StateDep
from epl.models.base import GoalsForecaster
from epl.services import forecasting, pipeline, track_record

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _production(state: StateDep) -> tuple[GoalsForecaster, str]:
    model, card = state.model(forecasting.PRODUCTION_MODEL)
    assert isinstance(model, GoalsForecaster)
    return model, card.version


@router.get(
    "/upcoming", response_model=schemas.UpcomingPredictions, summary="Forecasts for the next fixtures"
)
def upcoming(
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=380)] = 10,
    matchweek: Annotated[int | None, Query(ge=1, le=38, description="Only this matchweek")] = None,
) -> dict[str, Any]:
    model, version = _production(state)
    season = state.settings.current_season

    def compute() -> list[dict[str, Any]]:
        frame = state.frame()
        fixtures = frame[(frame["season"] == season) & (frame["status"] == "scheduled")]
        if matchweek is not None:
            fixtures = fixtures[fixtures["matchweek"] == matchweek]
        return forecasting.predict_fixtures(model, fixtures.sort_values("kickoff").head(limit))

    predictions = state.memo(("upcoming", version, limit, matchweek), compute)
    return {"model": forecasting.PRODUCTION_MODEL, "model_version": version, "predictions": predictions}


@router.get("/matchup", response_model=schemas.Matchup, summary="Forecast any home/away pairing")
def matchup(
    state: StateDep,
    home: Annotated[str, Query(min_length=2, examples=["Ashbury Rovers"])],
    away: Annotated[str, Query(min_length=2, examples=["Calder Vale"])],
) -> dict[str, Any]:
    if home == away:
        raise HTTPException(422, "home and away must be different teams")
    frame = state.frame()
    known = set(frame["home_team"])
    for team in (home, away):
        if team not in known:
            raise HTTPException(404, f"Unknown team {team!r}")
    model, version = _production(state)
    season = state.settings.current_season
    # Forecast "as if played today"; one cached result per pairing, model and day.
    kickoff = pd.Timestamp.now().normalize()
    return state.memo(
        ("matchup", version, home, away, kickoff.date()),
        lambda: forecasting.matchup(model, home, away, season, kickoff),
    )


@router.get(
    "/live",
    response_model=schemas.LivePerformance,
    summary="How the logged pre-kickoff forecasts have scored this season",
)
def live(state: StateDep, session: SessionDep) -> dict[str, Any]:
    season = state.settings.current_season
    result = pipeline.live_performance(session, season)
    return {"season": season, **result}


@router.get(
    "/log",
    response_model=schemas.PredictionLog,
    summary="Track record: each match's latest pre-kickoff forecast and its score",
    description=(
        "One row per match: the latest forecast logged strictly before kickoff (the rule /predictions/live "
        "scores), newest kickoff first. Paged in the database. A season with no logged forecasts returns an "
        "empty page. Offset pages can shift between requests if forecasts are logged in the meantime."
    ),
)
def log(
    state: StateDep,
    session: SessionDep,
    *,
    season: Annotated[
        int | None, Query(ge=2000, le=2100, description="Season start year; defaults to the current season")
    ] = None,
    status: Annotated[
        track_record.Status | None, Query(description="finished, or pending (anything not yet finished)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> dict[str, Any]:
    chosen = state.settings.current_season if season is None else season
    return track_record.prediction_log(session, chosen, status=status, limit=limit, offset=offset)
