"""Batch jobs: extract -> load -> backtest -> train -> log predictions.

Each step is idempotent, so the weekly refresh can simply run them all again.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from epl.core.config import Settings
from epl.core.timezones import UK
from epl.data import migrate
from epl.data.db import session_scope, upsert
from epl.data.load import LoadReport, load_seasons
from epl.data.repository import match_frame, played
from epl.data.schema import Match, Prediction
from epl.data.snapshots import ExtractReport, truth_path
from epl.data.sources import extract as extract_dataset
from epl.evaluation import backtest as bt
from epl.evaluation import metrics
from epl.models import zoo
from epl.models.base import GoalsForecaster, outcome_index
from epl.models.registry import ModelCard, ModelRegistry
from epl.services.forecasting import PRODUCTION_MODEL

log = logging.getLogger(__name__)


def report_path(settings: Settings) -> Any:
    return settings.artifacts_dir / "backtest" / "report.json"


def extract(settings: Settings, seasons: list[int], with_stats: bool = True) -> list[ExtractReport]:
    return extract_dataset(settings, seasons, with_stats)


def load(settings: Settings, seasons: list[int] | None = None) -> LoadReport:
    migrate.upgrade(settings.database_url)
    with session_scope(settings.database_url) as session:
        return load_seasons(session, settings, seasons or settings.seasons)


def run_backtest(settings: Settings, seasons: list[int], models: list[str] | None = None) -> dict[str, Any]:
    with session_scope(settings.database_url) as session:
        frame = match_frame(session)
    params = zoo.tuned(settings)
    predictions = pd.concat(
        [bt.walk_forward(frame, m, s, params) for s in seasons for m in (models or zoo.MODEL_NAMES)],
        ignore_index=True,
    )
    oracle = bt.oracle_predictions(predictions, truth(settings, seasons))
    if oracle is not None:  # synthetic data: how a forecaster that knew the true probabilities scores
        predictions = pd.concat([predictions, oracle], ignore_index=True)
    result = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tuning_season": settings.tuning_season,
        "holdout_season": settings.holdout_season,
        "step_days": bt.STEP_DAYS,
        "dataset": settings.dataset_info.to_dict(),
        "hyperparameters": params,
        "seasons": {str(s): bt.report(predictions[predictions["season"] == s]) for s in seasons},
    }
    out = report_path(settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    predictions.to_csv(out.with_name("predictions.csv"), index=False, lineterminator="\n")
    log.info("Backtest report written to %s", out)
    return result


def truth(settings: Settings, seasons: list[int]) -> pd.DataFrame | None:
    """True outcome probabilities, available only for synthetic datasets."""
    paths = [truth_path(settings, s) for s in seasons if truth_path(settings, s).exists()]
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True) if paths else None


def holdout_metrics(settings: Settings, model: str) -> dict[str, Any]:
    path = report_path(settings)
    if not path.exists():
        return {}
    season = json.loads(path.read_text(encoding="utf-8"))["seasons"].get(str(settings.holdout_season), {})
    entry = season.get("models", {}).get(model)
    return {"season": settings.holdout_season, **entry["metrics"]} if entry else {}


def train(settings: Settings, models: list[str] | None = None) -> list[ModelCard]:
    """Fit every model on all finished matches and register it."""
    with session_scope(settings.database_url) as session:
        history = played(match_frame(session))
    registry = ModelRegistry(settings.artifacts_dir)
    params = zoo.tuned(settings)
    cards = []
    for name in models or zoo.MODEL_NAMES:
        model = zoo.build(name, params).fit(history)
        cards.append(registry.save(model, history, holdout_metrics(settings, name)))
        log.info("Registered %s %s", name, cards[-1].version)
    return cards


def log_predictions(session: Session, settings: Settings, days: int = 7, now: datetime | None = None) -> int:
    """Record the production model's forecasts for fixtures in the next `days` days.

    One row per (match, model version): re-running with the same model is a no-op,
    and a retrained model adds a fresh forecast that supersedes the old one.
    `now` (timezone-aware) is injectable so the time window can be tested.
    """
    model, card = ModelRegistry(settings.artifacts_dir).load(PRODUCTION_MODEL)
    assert isinstance(model, GoalsForecaster)
    created_at = now or datetime.now(UTC)
    local_now = pd.Timestamp(created_at).tz_convert(UK).tz_localize(None)  # kickoffs are UK local time
    frame = match_frame(session, [settings.current_season])
    upcoming = frame[
        (frame["status"] == "scheduled")
        & frame["kickoff"].between(local_now, local_now + timedelta(days=days))
    ]
    if upcoming.empty:
        return 0
    probs, xg = model.predict_proba(upcoming), model.expected_goals(upcoming)
    rows = [
        {
            "match_id": int(mid),
            "model_name": card.name,
            "model_version": card.version,
            "p_home": float(p[0]),
            "p_draw": float(p[1]),
            "p_away": float(p[2]),
            "exp_home_goals": float(g[0]),
            "exp_away_goals": float(g[1]),
            "created_at": created_at,
        }
        for mid, p, g in zip(upcoming["match_id"], probs, xg, strict=True)
    ]
    existing = set(
        session.execute(select(Prediction.match_id).where(Prediction.model_version == card.version)).scalars()
    )
    new = [r for r in rows if r["match_id"] not in existing]
    upsert(session, Prediction.__table__, new, key=["match_id", "model_version"])  # type: ignore[arg-type]
    return len(new)


def _as_utc(moment: datetime) -> pd.Timestamp:
    """SQLite drops timezone information on read; every stored timestamp is UTC."""
    stamp = pd.Timestamp(moment)
    return stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)


def live_performance(session: Session, season: int) -> dict[str, Any]:
    """Score the latest pre-kickoff forecast of every finished match against its result."""
    rows = session.execute(
        select(Prediction, Match.kickoff, Match.home_score, Match.away_score, Match.status)
        .join(Match, Prediction.match_id == Match.id)
        .where(Match.season == season)
    ).all()
    if not rows:
        return {"n": 0, "pending": 0}
    log_df = pd.DataFrame(
        [
            {
                "match_id": p.match_id,
                "prediction_id": p.id,
                "model_version": p.model_version,
                "created_at": _as_utc(p.created_at),
                "kickoff": pd.Timestamp(kickoff).tz_localize(UK),
                "p_home": p.p_home,
                "p_draw": p.p_draw,
                "p_away": p.p_away,
                "home_goals": hs,
                "away_goals": as_,
                "status": status,
            }
            for p, kickoff, hs, as_, status in rows
        ]
    )
    pre_kickoff = log_df[log_df["created_at"] < log_df["kickoff"]]
    # Equal timestamps go to the later row, as in `track_record` (a bare sort is not stable).
    latest = pre_kickoff.sort_values(["created_at", "prediction_id"]).groupby("match_id").tail(1)
    scored = latest[latest["status"] == "finished"]
    if scored.empty:
        return {"n": 0, "pending": len(latest)}
    probs = scored[["p_home", "p_draw", "p_away"]].to_numpy()
    outcomes = outcome_index(scored)
    return {
        "n": len(scored),
        "pending": int((latest["status"] != "finished").sum()),
        "metrics": metrics.summarise(probs, outcomes),
    }
