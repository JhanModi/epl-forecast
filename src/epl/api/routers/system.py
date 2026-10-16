from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from epl import __version__
from epl.api import schemas
from epl.api.deps import SessionDep, StateDep
from epl.services.forecasting import PRODUCTION_MODEL

router = APIRouter(tags=["system"])


@router.get("/health", response_model=schemas.Health, summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready", response_model=schemas.Readiness, summary="Readiness probe: database and model loaded")
def ready(state: StateDep, session: SessionDep) -> schemas.Readiness:
    detail: list[str] = []
    database = model = True
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # report, don't raise: this endpoint must always answer
        database = False
        detail.append(f"database: {exc}")
    try:
        state.model(PRODUCTION_MODEL)
    except FileNotFoundError as exc:
        model = False
        detail.append(str(exc))
    return schemas.Readiness(
        status="ready" if database and model else "degraded",
        database=database,
        model=model,
        detail="; ".join(detail) or None,
    )


@router.get("/meta", response_model=schemas.Meta, summary="What data and model the API is serving")
def meta(state: StateDep) -> schemas.Meta:
    settings, frame = state.settings, state.frame()
    version: str | None
    trained_through: str | None
    try:
        _, card = state.model(PRODUCTION_MODEL)
        version, trained_through = card.version, card.trained_through
    except FileNotFoundError:
        version = trained_through = None

    def counts() -> tuple[list[int], Any, int, int]:
        finished = frame[frame["status"] == "finished"]
        current = frame[frame["season"] == settings.current_season]
        return (
            sorted(int(s) for s in frame["season"].unique()),
            finished["kickoff"].max() if len(finished) else None,
            int((current["status"] == "finished").sum()),
            int((current["status"] != "finished").sum()),
        )

    seasons, last_result, played, scheduled = state.memo(("meta",), counts)
    return schemas.Meta(
        version=__version__,
        dataset=schemas.Dataset(**settings.dataset_info.to_dict()),
        data_version=state.data_version(),
        current_season=settings.current_season,
        seasons=seasons,
        tuning_season=settings.tuning_season,
        holdout_season=settings.holdout_season,
        production_model=PRODUCTION_MODEL,
        model_version=version,
        trained_through=trained_through,
        last_result=last_result,
        matches_played=played,
        matches_scheduled=scheduled,
    )
