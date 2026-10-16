"""Hyperparameter search on the tuning season (2024/25) only.

The holdout season (2025/26) is never looked at here, so its backtest is an
honest estimate of out-of-sample performance. Each search minimises mean RPS.
"""

import itertools
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from epl.evaluation import metrics
from epl.evaluation.backtest import walk_forward
from epl.features import EloParams, elo_ratings
from epl.models import zoo
from epl.models.base import outcome_index
from epl.models.classifiers import OrderedLogit

log = logging.getLogger(__name__)

# Grids were widened until the optima on both the Premier League and demo tuning seasons were interior
# (except where noted). Tuning reports any optimum that still sits on an edge.
GRIDS: dict[str, dict[str, list[Any]]] = {
    # The Elo surface is very flat (every setting is within 0.002 RPS), so its exact optimum matters little.
    "elo": {
        "k": [20.0, 30.0, 40.0, 55.0],
        "home_advantage": [25.0, 55.0, 85.0, 115.0, 145.0, 175.0],
        "season_regression": [0.25, 0.4, 0.55, 0.7, 0.85],
    },
    "dixon_coles": {"xi": [0.0005, 0.001, 0.002, 0.003], "prior_sd": [0.1, 0.2, 0.3, 0.45, 0.6]},
    "logistic_regression": {"C": [0.003, 0.01, 0.05, 0.2]},
    # Boosting is best at a small total shrinkage (learning_rate x max_iter ~ 1); more capacity overfits.
    "gradient_boosting": {"learning_rate": [0.005, 0.01, 0.02], "max_iter": [50, 100, 200]},
}


def _combinations(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(grid, values, strict=True)) for values in itertools.product(*grid.values())]


def _elo_rps(frame: pd.DataFrame, season: int, params: EloParams) -> float:
    """One-step-ahead Elo forecasts: the logit link is fitted on earlier seasons only."""
    played = frame[frame["status"] == "finished"]
    ratings = elo_ratings(played, params)
    diff = (ratings["home_elo"] - ratings["away_elo"]).to_numpy()[:, None]
    y = outcome_index(played)
    train, test = (played["season"] < season).to_numpy(), (played["season"] == season).to_numpy()
    probs = OrderedLogit().fit(diff[train], y[train]).predict_proba(diff[test])
    return float(metrics.rps(probs, y[test]).mean())


def _walk_forward_rps(frame: pd.DataFrame, model: str, season: int, params: dict[str, Any]) -> float:
    predictions = walk_forward(frame, model, season, params)
    probs = predictions[["p_home", "p_draw", "p_away"]].to_numpy()
    return float(metrics.rps(probs, predictions["outcome"].to_numpy()).mean())


def tune(frame: pd.DataFrame, season: int, start: dict[str, Any] | None = None) -> dict[str, Any]:
    params = start or zoo.hyperparameters()
    trials: dict[str, list[dict[str, Any]]] = {}
    edges: list[str] = []

    def search(group: str, score: Any) -> None:
        results = []
        for candidate in _combinations(GRIDS[group]):
            value = score(candidate)
            results.append({"params": candidate, "rps": value})
            log.info("tune %-20s %s -> RPS %.5f", group, candidate, value)
        best = min(results, key=lambda r: r["rps"])
        params[group] = params[group] | best["params"]
        trials[group] = results
        for name, value in best["params"].items():  # an optimum on the edge suggests the grid is too narrow
            grid = GRIDS[group][name]
            if len(grid) > 1 and value in (min(grid), max(grid)):
                edges.append(f"{group}.{name}={value}")
                log.warning("tune %s: optimum %s=%s is on the edge of the grid %s", group, name, value, grid)

    # Elo first: the feature models are built on top of it.
    search("elo", lambda c: _elo_rps(frame, season, EloParams(**(params["elo"] | c))))
    for model in ("dixon_coles", "logistic_regression", "gradient_boosting"):
        search(model, lambda c, m=model: _walk_forward_rps(frame, m, season, params | {m: params[m] | c}))

    return {"season": season, "best": params, "trials": trials, "edge_optima": edges}


def save(result: dict[str, Any], path: Path) -> None:
    """Write the chosen values (and the season they were tuned on) to the dataset's hyperparameter file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**result["best"], "_tuned_on_season": result["season"], "_edge_optima": result["edge_optima"]}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def best_trial_table(result: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for group, trials in result["trials"].items():
        values = np.array([t["rps"] for t in trials])
        rows.append(
            {"group": group, "trials": len(trials), "best_rps": values.min(), "worst_rps": values.max()}
        )
    return pd.DataFrame(rows)
