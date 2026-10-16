"""Proper scoring rules for 3-way football forecasts, with uncertainty estimates.

All per-match functions take `probs` as an (n, 3) array ordered home, draw,
away and `outcomes` as indices 0/1/2, and return one score per match so that
bootstrap and paired tests can be built on top.

- **RPS** (ranked probability score) is the standard in football forecasting:
  it respects the ordering home > draw > away, so predicting a draw when the
  home team wins is penalised less than predicting an away win.
- **Log loss** punishes confident mistakes hardest.
- **Brier** is the squared error of the probability vector.
Lower is better for all three.
"""

from dataclasses import dataclass

import numpy as np
from scipy import stats

EPS = 1e-12


def one_hot(outcomes: np.ndarray, k: int = 3) -> np.ndarray:
    return np.eye(k)[outcomes]


def rps(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    cumulative_error = np.cumsum(probs, axis=1) - np.cumsum(one_hot(outcomes), axis=1)
    return (cumulative_error[:, :-1] ** 2).sum(axis=1) / (probs.shape[1] - 1)


def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    return -np.log(np.clip(probs[np.arange(len(outcomes)), outcomes], EPS, 1.0))


def brier(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    return ((probs - one_hot(outcomes)) ** 2).sum(axis=1)


def correct(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    return (probs.argmax(axis=1) == outcomes).astype(float)


SCORES = {"rps": rps, "log_loss": log_loss, "brier": brier, "accuracy": correct}


@dataclass(frozen=True)
class Estimate:
    mean: float
    low: float
    high: float

    def to_dict(self) -> dict[str, float]:
        return {"mean": self.mean, "low": self.low, "high": self.high}


def bootstrap_mean(values: np.ndarray, n_boot: int = 2000, level: float = 0.95, seed: int = 0) -> Estimate:
    """Percentile bootstrap CI of the mean over matches."""
    rng = np.random.default_rng(seed)
    means = values[rng.integers(0, len(values), (n_boot, len(values)))].mean(axis=1)
    alpha = (1 - level) / 2
    return Estimate(
        float(values.mean()), float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha))
    )


@dataclass(frozen=True)
class PairedComparison:
    """Is model A's score really different from model B's on the same matches?"""

    difference: Estimate  # mean(A - B) with a bootstrap CI
    t_statistic: float
    p_value: float  # two-sided paired t-test (Diebold-Mariano with no autocorrelation)

    def to_dict(self) -> dict[str, object]:
        return {
            "difference": self.difference.to_dict(),
            "t_statistic": self.t_statistic,
            "p_value": self.p_value,
        }


def compare(scores_a: np.ndarray, scores_b: np.ndarray, seed: int = 0) -> PairedComparison:
    diff = scores_a - scores_b
    result = stats.ttest_1samp(diff, 0.0)
    return PairedComparison(bootstrap_mean(diff, seed=seed), float(result.statistic), float(result.pvalue))


def calibration(probs: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> dict[str, object]:
    """Reliability-diagram data per outcome, plus expected calibration error (ECE).

    For each outcome, forecasts are grouped into equal-width probability bins;
    a calibrated model's observed frequency matches its mean forecast in every bin.
    """
    hits = one_hot(outcomes)
    edges = np.linspace(0, 1, bins + 1)
    curves, ece = {}, 0.0
    for k, name in enumerate(("home", "draw", "away")):
        which = np.clip(np.digitize(probs[:, k], edges) - 1, 0, bins - 1)
        points = []
        for b in range(bins):
            mask = which == b
            if mask.sum() == 0:
                continue
            forecast, observed = probs[mask, k].mean(), hits[mask, k].mean()
            points.append(
                {"forecast": float(forecast), "observed": float(observed), "count": int(mask.sum())}
            )
            ece += mask.sum() * abs(forecast - observed)
        curves[name] = points
    return {"curves": curves, "ece": float(ece / (3 * len(outcomes)))}


def summarise(probs: np.ndarray, outcomes: np.ndarray) -> dict[str, dict[str, float]]:
    return {name: bootstrap_mean(fn(probs, outcomes)).to_dict() for name, fn in SCORES.items()}
