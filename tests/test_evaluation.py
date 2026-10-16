from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from epl.evaluation import backtest, metrics
from epl.models.base import Forecaster

PERFECT_HOME = np.array([[1.0, 0.0, 0.0]])


def test_rps_known_values() -> None:
    home_win = np.array([0])
    assert metrics.rps(PERFECT_HOME, home_win)[0] == 0
    # Predicting a home win with certainty when the away side wins is the worst case.
    assert metrics.rps(PERFECT_HOME, np.array([2]))[0] == 1
    # ...and is penalised more than the same certainty when the match is drawn.
    assert metrics.rps(PERFECT_HOME, np.array([1]))[0] == 0.5
    # Worked example: (0.5, 0.3, 0.2) with a home win -> ((0.5-1)^2 + (0.8-1)^2) / 2.
    assert metrics.rps(np.array([[0.5, 0.3, 0.2]]), home_win)[0] == pytest.approx(0.145)


def test_log_loss_and_brier() -> None:
    probs = np.array([[0.5, 0.3, 0.2]])
    assert metrics.log_loss(probs, np.array([1]))[0] == pytest.approx(-np.log(0.3))
    assert metrics.brier(probs, np.array([0]))[0] == pytest.approx(0.25 + 0.09 + 0.04)
    assert np.isfinite(metrics.log_loss(np.array([[1.0, 0.0, 0.0]]), np.array([2]))[0])  # clipped, not inf


def test_bootstrap_interval_brackets_the_mean() -> None:
    values = np.random.default_rng(0).normal(1.0, 0.5, 400)
    est = metrics.bootstrap_mean(values)

    assert est.low < est.mean < est.high
    assert est.high - est.low == pytest.approx(2 * 1.96 * 0.5 / 20, rel=0.25)


def test_paired_comparison_detects_a_real_difference() -> None:
    rng = np.random.default_rng(0)
    base = rng.uniform(0.1, 0.3, 500)

    better = metrics.compare(base - 0.02, base + rng.normal(0, 0.01, 500))

    assert better.difference.high < 0
    assert better.p_value < 0.001


def test_calibration_of_a_calibrated_forecaster() -> None:
    rng = np.random.default_rng(0)
    probs = rng.dirichlet([3, 2, 2], size=20_000)
    outcomes = np.array([rng.choice(3, p=p) for p in probs])

    result = metrics.calibration(probs, outcomes)

    assert result["ece"] < 0.02
    assert set(result["curves"]) == {"home", "draw", "away"}


class SpyModel(Forecaster):
    """Records what the harness shows it, to prove there is no look-ahead."""

    name = "spy"
    description = "test double"
    calls: ClassVar[list[tuple[pd.Timestamp, pd.Timestamp, bool]]] = []

    def fit(self, history: pd.DataFrame) -> "SpyModel":
        self.last_seen = history["kickoff"].max()
        return self

    def predict_proba(self, fixtures: pd.DataFrame) -> np.ndarray:
        results_hidden = fixtures["home_goals"].isna().all() and (fixtures["status"] == "scheduled").all()
        SpyModel.calls.append((self.last_seen, fixtures["kickoff"].min(), results_hidden))
        return np.full((len(fixtures), 3), 1 / 3)


def test_walk_forward_never_looks_ahead(frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest.zoo, "build", lambda *_: SpyModel())
    SpyModel.calls.clear()

    predictions = backtest.walk_forward(frame, "spy", 2024)

    assert len(predictions) == (frame["season"] == 2024).sum()
    assert SpyModel.calls, "the model was never called"
    for last_seen, first_fixture, hidden in SpyModel.calls:
        assert last_seen < first_fixture
        assert hidden


def test_report_ranks_models_and_compares_to_baseline(frame: pd.DataFrame) -> None:
    preds = backtest.walk_forward(frame, "prior", 2024)
    sharp = preds.assign(model="sharp")
    sharp[["p_home", "p_draw", "p_away"]] = np.eye(3)[sharp["outcome"]] * 0.9 + 0.1 / 3

    report = backtest.report(pd.concat([preds, sharp]))

    assert report["ranking"] == ["sharp", "prior"]
    assert report["models"]["sharp"]["vs_reference"]["difference"]["mean"] < 0
    assert np.array(report["models"]["sharp"]["confusion_matrix"]).sum() == len(preds)
