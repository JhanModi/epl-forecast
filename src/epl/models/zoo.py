"""The catalogue of forecasters, built from hyperparameters tuned per dataset.

`epl tune` chooses hyperparameters on the dataset's tuning season (2024/25) and
writes them to ``<artifacts_dir>/hyperparameters.json``; they are then frozen,
and the holdout season (2025/26) is only used for the final evaluation.
Without a tuned file the defaults below apply.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from epl.features import EloParams
from epl.models.base import Forecaster
from epl.models.classifiers import EloLogit, Ensemble, GradientBoostingModel, LogisticModel, PriorBaseline
from epl.models.dixon_coles import DixonColes

if TYPE_CHECKING:
    from epl.core.config import Settings

DEFAULTS: dict[str, Any] = {
    "elo": {"k": 20.0, "home_advantage": 55.0, "season_regression": 0.25},
    "dixon_coles": {"xi": 0.002, "prior_sd": 0.35},
    "logistic_regression": {"C": 0.05},
    "gradient_boosting": {"learning_rate": 0.03, "max_iter": 150},
}


def params_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / "hyperparameters.json"


def hyperparameters(path: Path | None = None) -> dict[str, Any]:
    """The tuned values in `path` (if it exists), merged over the defaults."""
    tuned = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.exists() else {}
    return {name: DEFAULTS[name] | tuned.get(name, {}) for name in DEFAULTS}


def tuned(settings: "Settings") -> dict[str, Any]:
    """The configured dataset's hyperparameters (its tuned file if `epl tune` has run, else defaults)."""
    return hyperparameters(params_path(settings.artifacts_dir))


def build(name: str, params: dict[str, Any] | None = None) -> Forecaster:
    p = params or hyperparameters()
    elo = EloParams(**p["elo"])
    factories: dict[str, Callable[[], Forecaster]] = {
        "prior": PriorBaseline,
        "elo_logit": lambda: EloLogit(elo),
        "logistic_regression": lambda: LogisticModel(elo_params=elo, **p["logistic_regression"]),
        "gradient_boosting": lambda: GradientBoostingModel(elo_params=elo, **p["gradient_boosting"]),
        "dixon_coles": lambda: DixonColes(**p["dixon_coles"]),
        "ensemble": lambda: Ensemble(
            [
                DixonColes(**p["dixon_coles"]),
                EloLogit(elo),
                LogisticModel(elo_params=elo, **p["logistic_regression"]),
            ]
        ),
    }
    if name not in factories:
        raise KeyError(f"unknown model {name!r}; choose from {sorted(factories)}")
    return factories[name]()


MODEL_NAMES = ["prior", "elo_logit", "logistic_regression", "gradient_boosting", "dixon_coles", "ensemble"]
