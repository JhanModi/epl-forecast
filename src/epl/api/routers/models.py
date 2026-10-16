import json
from typing import Any

from fastapi import APIRouter, HTTPException

from epl.api import schemas
from epl.api.deps import StateDep
from epl.models.registry import ModelCard
from epl.services import pipeline

router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[schemas.ModelCardOut], summary="Registered models and their cards")
def models(state: StateDep) -> list[ModelCard]:
    return state.registry.latest_cards()


@router.get(
    "/evaluation/backtest",
    response_model=schemas.BacktestReport,
    summary="Walk-forward backtest report",
    description=(
        "Per season: a leaderboard with bootstrap 95% CIs for RPS, log loss, Brier and accuracy; "
        "paired tests "
        "against the baseline and the best model; calibration curves; RPS by matchweek; confusion matrices."
    ),
)
def backtest(state: StateDep) -> dict[str, Any]:
    path = pipeline.report_path(state.settings)
    if not path.exists():
        raise HTTPException(404, "No backtest report yet; run `epl backtest`")
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return report
