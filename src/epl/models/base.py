"""The interface every forecaster implements.

`fit` receives the *played* history known at the forecast time. `predict_proba`
receives fixtures (results unknown) and returns P(home win), P(draw), P(away win)
in that order — the natural ordering of the outcome, which the ranked
probability score relies on.

The walk-forward harness enforces point-in-time correctness: a model is never
handed a match that kicked off after its cutoff.
"""

from abc import ABC, abstractmethod
from typing import ClassVar, Self

import numpy as np
import pandas as pd

OUTCOMES = ("home", "draw", "away")


def outcome_index(frame: pd.DataFrame) -> np.ndarray:
    """0 = home win, 1 = draw, 2 = away win."""
    home, away = frame["home_goals"].astype(int).to_numpy(), frame["away_goals"].astype(int).to_numpy()
    return np.select([home > away, home == away], [0, 1], 2)


def mask_results(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Hide everything that is only known after kickoff."""
    hidden = fixtures.copy()
    hidden["status"] = "scheduled"
    hidden[["home_goals", "away_goals"]] = pd.NA
    in_match = [
        c for c in hidden.columns if c.startswith(("home_", "away_")) and c not in ("home_team", "away_team")
    ]
    hidden[in_match] = np.nan
    return hidden


class Forecaster(ABC):
    name: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    def fit(self, history: pd.DataFrame) -> Self: ...

    @abstractmethod
    def predict_proba(self, fixtures: pd.DataFrame) -> np.ndarray:
        """(n_fixtures, 3) array of P(home), P(draw), P(away); rows sum to 1."""

    def params(self) -> dict[str, object]:
        """Hyperparameters, recorded in the model registry."""
        return {}


class GoalsForecaster(Forecaster):
    """A forecaster that models the scoreline, not just the outcome."""

    @abstractmethod
    def score_matrix(self, fixtures: pd.DataFrame) -> np.ndarray:
        """(n_fixtures, G, G) array: P(home scores i, away scores j)."""

    @abstractmethod
    def expected_goals(self, fixtures: pd.DataFrame) -> np.ndarray:
        """(n_fixtures, 2) array of expected home and away goals."""

    def predict_proba(self, fixtures: pd.DataFrame) -> np.ndarray:
        return outcome_probabilities(self.score_matrix(fixtures))


def outcome_probabilities(matrix: np.ndarray) -> np.ndarray:
    """Collapse score matrices (n, G, G) into (n, 3) home/draw/away probabilities."""
    home = np.tril(matrix, k=-1).sum(axis=(1, 2))
    draw = np.trace(matrix, axis1=1, axis2=2)
    away = np.triu(matrix, k=1).sum(axis=(1, 2))
    probs = np.stack([home, draw, away], axis=1)
    return probs / probs.sum(axis=1, keepdims=True)
