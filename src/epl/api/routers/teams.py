from typing import Any

from fastapi import APIRouter, HTTPException

from epl.api import schemas
from epl.api.deps import SessionDep, StateDep
from epl.data import repository
from epl.data.schema import Team
from epl.features import EloParams
from epl.models import zoo
from epl.models.dixon_coles import DixonColes
from epl.services import forecasting

router = APIRouter(tags=["teams"])


@router.get("/teams", response_model=list[schemas.Team], summary="Every team in the database")
def teams(session: SessionDep) -> list[Team]:
    return repository.teams(session)


@router.get(
    "/ratings",
    response_model=list[schemas.TeamRating],
    summary="Current strength of every team in the current season",
)
def ratings(state: StateDep) -> list[dict[str, Any]]:
    model, card = state.model(forecasting.PRODUCTION_MODEL)
    assert isinstance(model, DixonColes)
    elo = EloParams(**zoo.tuned(state.settings)["elo"])
    key = ("ratings", card.version, elo)  # EloParams is frozen, so it can be part of the key
    return state.memo(key, lambda: forecasting.team_ratings(state.frame(), model, elo))


@router.get(
    "/teams/{team_id}/elo", response_model=list[schemas.EloPoint], summary="A team's Elo rating over time"
)
def elo(team_id: int, state: StateDep, session: SessionDep) -> list[dict[str, Any]]:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(404, f"No team with id {team_id}")
    elo = EloParams(**zoo.tuned(state.settings)["elo"])
    return state.memo(("elo", team.name, elo), lambda: forecasting.elo_history(state.frame(), team.name, elo))
