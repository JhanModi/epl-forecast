"""Outcome classifiers over engineered pre-match features, plus simple baselines."""

from collections.abc import Sequence
from typing import ClassVar, Self

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from epl.features import ALL_FEATURES, ELO_FEATURES, STATS_FEATURES, EloParams
from epl.features.form import cached_features
from epl.models.base import Forecaster, mask_results, outcome_index


class PriorBaseline(Forecaster):
    """Predicts the historical home/draw/away frequencies for every match."""

    name = "prior"
    description = "Baseline: historical home / draw / away frequencies"

    def fit(self, history: pd.DataFrame) -> Self:
        counts = np.bincount(outcome_index(history), minlength=3)
        self.probs_ = counts / counts.sum()
        return self

    def predict_proba(self, fixtures: pd.DataFrame) -> np.ndarray:
        return np.tile(self.probs_, (len(fixtures), 1))


class OrderedLogit(ClassifierMixin, BaseEstimator):
    """Proportional-odds model for an ordered outcome (away win < draw < home win).

    One latent strength s = Xβ; the outcome is decided by where s falls relative
    to two cut-points. With 3 parameters per feature-set it is far more
    parsimonious than a multinomial model, and it respects the ordering.
    """

    def fit(self, X: np.ndarray, y: np.ndarray) -> Self:
        X = np.asarray(X, dtype=float)
        self.mean_, self.scale_ = X.mean(axis=0), X.std(axis=0) + 1e-9
        Z = (X - self.mean_) / self.scale_
        target = 2 - np.asarray(y)  # 0 = away, 1 = draw, 2 = home

        def nll(params: np.ndarray) -> float:
            p = self._probs(Z, params)
            return float(-np.log(np.clip(p[np.arange(len(target)), target], 1e-12, None)).sum())

        start = np.r_[-0.5, 0.0, np.zeros(Z.shape[1])]  # cut-point, log gap, betas
        self.params_ = minimize(nll, start, method="L-BFGS-B").x
        self.classes_ = np.array([0, 1, 2])
        return self

    @staticmethod
    def _probs(Z: np.ndarray, params: np.ndarray) -> np.ndarray:
        """Columns: away, draw, home."""
        c1, c2 = params[0], params[0] + np.exp(params[1])
        s = Z @ params[2:]
        below_1, below_2 = expit(c1 - s), expit(c2 - s)
        return np.stack([below_1, below_2 - below_1, 1 - below_2], axis=1)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Z = (np.asarray(X, dtype=float) - self.mean_) / self.scale_
        return self._probs(Z, self.params_)[:, ::-1]  # -> home, draw, away


class FeatureClassifier(Forecaster):
    """Any scikit-learn classifier over pre-match features from `epl.features`.

    `estimator` is an unfitted template; each `fit` trains a fresh `clone` of it, so
    refits never share state and the whole forecaster pickles cleanly into the registry.
    """

    name: ClassVar[str] = "feature_classifier"
    description: ClassVar[str] = ""

    def __init__(
        self,
        estimator: BaseEstimator,
        features: Sequence[str],
        elo_params: EloParams = EloParams(),
        hyperparameters: dict[str, object] | None = None,
    ) -> None:
        self.estimator = estimator
        self.features = list(features)
        self.elo_params = elo_params
        self.hyperparameters = hyperparameters or {}

    def params(self) -> dict[str, object]:
        used = getattr(self, "features_", self.features)
        return {"features": used, "elo": self.elo_params.to_dict(), **self.hyperparameters}

    def fit(self, history: pd.DataFrame) -> Self:
        history = history.reset_index(drop=True)
        # Datasets without match statistics (e.g. openfootball) cannot feed the stats features: drop them
        # rather than train on constant zeros. The features actually used are recorded in the model card.
        has_stats = "home_shots" in history and bool(history["home_shots"].notna().any())
        self.features_ = [f for f in self.features if has_stats or f not in STATS_FEATURES]
        X = cached_features(history, self.elo_params)[self.features_]
        self.model_ = clone(self.estimator).fit(X.to_numpy(), outcome_index(history))
        self.history_ = history
        return self

    def predict_proba(self, fixtures: pd.DataFrame) -> np.ndarray:
        # Features for fixtures come from the same as-of join as training: history + masked fixtures.
        X = self.features_for(fixtures)[self.features_]
        raw = self.model_.predict_proba(X.to_numpy())
        probs = np.zeros((len(fixtures), 3))
        probs[:, self.model_.classes_] = raw  # guard against a class missing from training
        return probs

    def features_for(self, fixtures: pd.DataFrame) -> pd.DataFrame:
        frame = pd.concat([self.history_, mask_results(fixtures)], ignore_index=True)
        return cached_features(frame, self.elo_params).iloc[len(self.history_) :].set_index(fixtures.index)


class EloLogit(FeatureClassifier):
    name = "elo_logit"
    description = "Elo rating difference -> ordered logit"

    def __init__(self, elo_params: EloParams = EloParams()) -> None:
        super().__init__(OrderedLogit(), ELO_FEATURES, elo_params)


class LogisticModel(FeatureClassifier):
    name = "logistic_regression"
    description = "Multinomial logistic regression on Elo, form and match-stat features"

    def __init__(self, C: float = 0.05, elo_params: EloParams = EloParams()) -> None:
        super().__init__(
            make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=2000)),
            ALL_FEATURES,
            elo_params,
            {"C": C},
        )


class GradientBoostingModel(FeatureClassifier):
    name = "gradient_boosting"
    description = "Histogram gradient-boosted trees on Elo, form and match-stat features"

    def __init__(
        self, learning_rate: float = 0.03, max_iter: int = 150, elo_params: EloParams = EloParams()
    ) -> None:
        super().__init__(
            HistGradientBoostingClassifier(
                learning_rate=learning_rate,
                max_iter=max_iter,
                max_depth=3,
                min_samples_leaf=40,
                l2_regularization=1.0,
                random_state=0,
            ),
            ALL_FEATURES,
            elo_params,
            {"learning_rate": learning_rate, "max_iter": max_iter, "max_depth": 3, "min_samples_leaf": 40},
        )


class Ensemble(Forecaster):
    """Weighted average of member probabilities (a linear opinion pool)."""

    name = "ensemble"
    description = "Average of Dixon-Coles, Elo logit and logistic regression"

    def __init__(self, members: Sequence[Forecaster], weights: Sequence[float] | None = None) -> None:
        self.members = list(members)
        w = np.asarray(weights if weights is not None else [1.0] * len(self.members), dtype=float)
        self.weights = w / w.sum()

    def params(self) -> dict[str, object]:
        return {
            "members": {m.name: float(w) for m, w in zip(self.members, self.weights, strict=True)},
            **{m.name: m.params() for m in self.members},
        }

    def fit(self, history: pd.DataFrame) -> Self:
        for member in self.members:
            member.fit(history)
        return self

    def predict_proba(self, fixtures: pd.DataFrame) -> np.ndarray:
        return sum(w * m.predict_proba(fixtures) for m, w in zip(self.members, self.weights, strict=True))
