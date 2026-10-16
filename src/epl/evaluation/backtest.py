"""Walk-forward (rolling-origin) backtest.

The season is cut into weekly windows. Before each window the model is refitted
on every match played before the window opens, then it forecasts the window's
fixtures with their results hidden — exactly what a weekly production job would
do. No forecast ever uses information from after its cutoff.
"""

import itertools
import logging
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from epl.evaluation import metrics
from epl.models import zoo
from epl.models.base import OUTCOMES, mask_results, outcome_index

log = logging.getLogger(__name__)

STEP_DAYS = 7


def windows(kickoffs: pd.Series, step_days: int = STEP_DAYS) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start = kickoffs.min().normalize()
    edges = pd.date_range(start, kickoffs.max() + pd.Timedelta(days=step_days), freq=f"{step_days}D")
    return list(itertools.pairwise(edges))


def walk_forward(
    frame: pd.DataFrame,
    model_name: str,
    season: int,
    params: dict[str, Any] | None = None,
    *,
    step_days: int = STEP_DAYS,
    progress: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """One row per match of `season`: the forecast made at the start of its week."""
    played = frame[frame["status"] == "finished"].reset_index(drop=True)
    test = played[played["season"] == season]
    model = zoo.build(model_name, params)  # one instance, so Dixon-Coles can warm-start each refit

    rows = []
    steps = windows(test["kickoff"], step_days)
    for i, (start, end) in enumerate(steps, start=1):
        fixtures = test[(test["kickoff"] >= start) & (test["kickoff"] < end)]
        if fixtures.empty:
            continue
        history = played[played["kickoff"] < start]
        probs = model.fit(history).predict_proba(mask_results(fixtures))
        rows.append(
            fixtures[
                [
                    "match_id",
                    "season",
                    "matchweek",
                    "kickoff",
                    "home_team",
                    "away_team",
                    "home_goals",
                    "away_goals",
                ]
            ]
            .assign(cutoff=start, outcome=outcome_index(fixtures))
            .assign(**{f"p_{o}": probs[:, k] for k, o in enumerate(OUTCOMES)})
        )
        if progress:
            progress(i, len(steps))

    predictions = pd.concat(rows, ignore_index=True).assign(model=model_name)
    log.info("%s %d: %d forecasts over %d weekly refits", model_name, season, len(predictions), len(rows))
    return predictions


ORACLE = "oracle"


def oracle_predictions(predictions: pd.DataFrame, truth: pd.DataFrame | None) -> pd.DataFrame | None:
    """The generating probabilities for the same matches, as if they were a model's forecasts."""
    if truth is None or predictions.empty:
        return None
    base = predictions.drop_duplicates("match_id").drop(columns=["p_home", "p_draw", "p_away", "model"])
    merged = base.merge(truth[["match_id", "p_home", "p_draw", "p_away"]], on="match_id", how="inner")
    total = merged[["p_home", "p_draw", "p_away"]].sum(axis=1)
    merged[["p_home", "p_draw", "p_away"]] = merged[["p_home", "p_draw", "p_away"]].div(total, axis=0)
    return merged.assign(model=ORACLE)


def probs_of(predictions: pd.DataFrame) -> np.ndarray:
    return predictions[[f"p_{o}" for o in OUTCOMES]].to_numpy()


def report(predictions: pd.DataFrame, reference: str = "prior") -> dict[str, Any]:
    """Leaderboard with CIs, paired tests vs the baseline and the best model, calibration and per-week RPS."""
    by_model = {str(name): g.sort_values("match_id") for name, g in predictions.groupby("model")}
    models: dict[str, Any] = {}
    for name, preds in by_model.items():
        probs, outcomes = probs_of(preds), preds["outcome"].to_numpy()
        weekly = preds.assign(rps=metrics.rps(probs, outcomes)).groupby("matchweek")["rps"].mean()
        confusion = pd.crosstab(
            pd.Categorical(outcomes, categories=[0, 1, 2]),
            pd.Categorical(probs.argmax(axis=1), categories=[0, 1, 2]),
            dropna=False,
        ).to_numpy()
        models[name] = {
            "n": len(preds),
            "metrics": metrics.summarise(probs, outcomes),
            "calibration": metrics.calibration(probs, outcomes),
            "rps_by_matchweek": {int(str(k)): float(v) for k, v in weekly.items()},
            "confusion_matrix": confusion.tolist(),  # rows = actual, columns = predicted (home, draw, away)
            "mean_probabilities": dict(zip(OUTCOMES, probs.mean(axis=0).round(4).tolist(), strict=True)),
        }

    ranking = sorted(models, key=lambda m: models[m]["metrics"]["rps"]["mean"])
    # The oracle is a ceiling, not a contender: "best" is the best model that could actually be deployed.
    best = next(m for m in ranking if m != ORACLE)
    for name, preds in by_model.items():
        outcomes = preds["outcome"].to_numpy()
        own = metrics.rps(probs_of(preds), outcomes)
        models[name]["vs_reference"] = metrics.compare(
            own, metrics.rps(probs_of(by_model[reference]), outcomes)
        ).to_dict()
        models[name]["vs_best"] = metrics.compare(
            own, metrics.rps(probs_of(by_model[best]), outcomes)
        ).to_dict()

    actual = np.bincount(next(iter(by_model.values()))["outcome"], minlength=3)
    return {
        "ranking": ranking,
        "best": best,
        "reference": reference,
        "ceiling": ORACLE if ORACLE in models else None,
        "n_matches": int(actual.sum()),
        "outcome_rates": dict(zip(OUTCOMES, (actual / actual.sum()).round(4).tolist(), strict=True)),
        "models": models,
    }
