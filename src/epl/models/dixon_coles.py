"""Dixon-Coles (1997) bivariate Poisson goals model, fitted by penalised maximum likelihood.

    home goals ~ Poisson(λ),  log λ = μ + γ + attack[home] + defence[away] + promoted effects
    away goals ~ Poisson(ν),  log ν = μ     + attack[away] + defence[home] + promoted effects

with the Dixon-Coles correction τ(x, y; ρ) on the four low scores (0-0, 1-0,
0-1, 1-1), which plain independent Poissons get wrong.

Design choices:
- **Time decay**: each match is weighted by exp(-ξ · days ago), so recent form
  counts more without a hard window.
- **Gaussian prior (ridge) on team parameters**: attack/defence ~ N(0, σ²).
  It identifies the model without a sum-to-zero constraint and shrinks teams
  with few matches towards average.
- **Promoted-team prior**: teams that came up from the Championship during
  the data window share an attack/defence offset, learned from earlier
  promoted sides. Their own parameters shrink towards that offset rather than
  towards the league average, so a newly promoted team with zero matches is
  predicted as "a typical promoted team". The offset is a property of the team
  (not of the season), so its strength does not jump when it survives into a
  second season.
- **Analytic gradient**, so L-BFGS converges in milliseconds; warm starts make
  weekly refits in the backtest cheap.
"""

from dataclasses import dataclass
from typing import Self

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from epl.models.base import GoalsForecaster

MAX_GOALS = 10  # score matrices cover 0..10 goals per side

# Teams promoted into the first season of the data, which the data cannot reveal on its own.
KNOWN_PROMOTED = {2023: {"Burnley", "Sheffield United", "Luton Town"}}


Rosters = dict[int, set[str]]


def rosters(frame: pd.DataFrame) -> Rosters:
    """season -> teams playing in it."""
    seasons = frame["season"].astype(int)
    return {
        s: set(frame.loc[seasons == s, "home_team"]) | set(frame.loc[seasons == s, "away_team"])
        for s in seasons.unique().tolist()
    }


def promoted_teams(by_season: Rosters) -> set[str]:
    """Teams promoted into any season of the data."""
    promoted: set[str] = set()
    for season, teams in by_season.items():
        previous = by_season.get(season - 1)
        promoted |= (teams - previous) if previous is not None else teams & KNOWN_PROMOTED.get(season, set())
    return promoted


@dataclass
class _Design:
    home: np.ndarray
    away: np.ndarray
    home_promoted: np.ndarray
    away_promoted: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    weights: np.ndarray


class DixonColes(GoalsForecaster):
    name = "dixon_coles"
    description = "Time-weighted Dixon-Coles bivariate Poisson with a promoted-team prior"

    def __init__(self, xi: float = 0.002, prior_sd: float = 0.35, max_goals: int = MAX_GOALS) -> None:
        self.xi = xi  # decay per day; 0.002 -> a match a year ago has ~half the weight
        self.prior_sd = prior_sd
        self.max_goals = max_goals
        self._warm_start: dict[str, float] = {}

    def params(self) -> dict[str, object]:
        return {"xi": self.xi, "prior_sd": self.prior_sd, "max_goals": self.max_goals}

    # ------------------------------------------------------------------ fitting
    def _design(self, frame: pd.DataFrame, promoted: set[str]) -> _Design:
        # Teams never seen in training map to an extra slot whose parameters are the prior mean (0).
        n = len(self.teams_)
        index = {team: i for i, team in enumerate(self.teams_)}
        days_ago = (self.cutoff_ - frame["kickoff"]).dt.total_seconds().to_numpy() / 86_400
        return _Design(
            home=frame["home_team"].map(index).fillna(n).to_numpy(dtype=int),
            away=frame["away_team"].map(index).fillna(n).to_numpy(dtype=int),
            home_promoted=frame["home_team"].isin(promoted).to_numpy(dtype=float),
            away_promoted=frame["away_team"].isin(promoted).to_numpy(dtype=float),
            # Goals are only used when fitting; hypothetical fixtures may not carry the columns at all.
            home_goals=frame.get("home_goals", pd.Series(0, index=frame.index)).to_numpy(
                dtype=float, na_value=0
            ),
            away_goals=frame.get("away_goals", pd.Series(0, index=frame.index)).to_numpy(
                dtype=float, na_value=0
            ),
            weights=np.exp(-self.xi * np.maximum(days_ago, 0)),
        )

    def _unpack(self, theta: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float, float, float, float]:
        n = len(self.teams_)
        mu, home, rho, promoted_attack, promoted_defence = theta[2 * n :]
        return theta[:n], theta[n : 2 * n], mu, home, rho, promoted_attack, promoted_defence

    def _rates(self, theta: np.ndarray, d: _Design) -> tuple[np.ndarray, np.ndarray]:
        attack, defence, mu, home, _, pa, pd_ = self._unpack(theta)
        attack, defence = np.append(attack, 0.0), np.append(defence, 0.0)  # unseen-team slot
        log_home = mu + home + attack[d.home] + defence[d.away] + pa * d.home_promoted + pd_ * d.away_promoted
        log_away = mu + attack[d.away] + defence[d.home] + pa * d.away_promoted + pd_ * d.home_promoted
        return np.exp(log_home), np.exp(log_away)

    def _objective(self, theta: np.ndarray, d: _Design) -> tuple[float, np.ndarray]:
        """Penalised negative log-likelihood and its exact gradient."""
        n = len(self.teams_)
        attack, defence, _, _, rho, _, _ = self._unpack(theta)
        lam, nu = self._rates(theta, d)
        x, y, w = d.home_goals, d.away_goals, d.weights

        # Poisson part (the log x! terms are constant and dropped).
        loglik = x * np.log(lam) - lam + y * np.log(nu) - nu
        g_home, g_away = x - lam, y - nu  # d loglik / d log-rate

        # Dixon-Coles low-score correction: log τ and its derivatives.
        log_tau = np.zeros_like(lam)
        g_rho = np.zeros_like(lam)
        cases = (
            ((x == 0) & (y == 0), lambda: 1 - lam * nu * rho),
            ((x == 0) & (y == 1), lambda: 1 + lam * rho),
            ((x == 1) & (y == 0), lambda: 1 + nu * rho),
            ((x == 1) & (y == 1), lambda: np.full_like(lam, 1 - rho)),
        )
        for case, (mask, tau_fn) in enumerate(cases):
            if not mask.any():
                continue
            tau = np.maximum(tau_fn()[mask], 1e-10)
            log_tau[mask] = np.log(tau)
            if case == 0:
                g_home[mask] += -lam[mask] * nu[mask] * rho / tau
                g_away[mask] += -lam[mask] * nu[mask] * rho / tau
                g_rho[mask] = -lam[mask] * nu[mask] / tau
            elif case == 1:
                g_home[mask] += lam[mask] * rho / tau
                g_rho[mask] = lam[mask] / tau
            elif case == 2:
                g_away[mask] += nu[mask] * rho / tau
                g_rho[mask] = nu[mask] / tau
            else:
                g_rho[mask] = -1 / tau

        g_home, g_away = w * g_home, w * g_away
        grad = np.empty_like(theta)
        grad[:n] = np.bincount(d.home, g_home, n) + np.bincount(d.away, g_away, n)  # attack
        grad[n : 2 * n] = np.bincount(d.away, g_home, n) + np.bincount(d.home, g_away, n)  # defence
        grad[2 * n] = g_home.sum() + g_away.sum()  # intercept
        grad[2 * n + 1] = g_home.sum()  # home advantage
        grad[2 * n + 2] = (w * g_rho).sum()
        grad[2 * n + 3] = (g_home * d.home_promoted + g_away * d.away_promoted).sum()
        grad[2 * n + 4] = (g_home * d.away_promoted + g_away * d.home_promoted).sum()

        penalty = 1 / (2 * self.prior_sd**2)
        value = -(w * (loglik + log_tau)).sum() + penalty * (attack @ attack + defence @ defence)
        grad = -grad
        grad[:n] += 2 * penalty * attack
        grad[n : 2 * n] += 2 * penalty * defence
        return float(value), grad

    def fit(self, history: pd.DataFrame) -> Self:
        self.teams_ = sorted(set(history["home_team"]) | set(history["away_team"]))
        self.rosters_ = rosters(history)
        self.promoted_ = promoted_teams(self.rosters_)
        self.cutoff_ = history["kickoff"].max()
        design = self._design(history, self.promoted_)

        names = [f"attack:{t}" for t in self.teams_] + [f"defence:{t}" for t in self.teams_]
        names += ["intercept", "home_advantage", "rho", "promoted_attack", "promoted_defence"]
        start = {"intercept": 0.3, "home_advantage": 0.2, "rho": -0.05} | self._warm_start
        theta0 = np.array([start.get(name, 0.0) for name in names])
        bounds: list[tuple[float | None, float | None]] = [(None, None)] * len(names)
        bounds[names.index("rho")] = (-0.25, 0.25)  # keeps every τ positive for realistic scoring rates

        result = minimize(self._objective, theta0, args=(design,), jac=True, method="L-BFGS-B", bounds=bounds)
        if not result.success:
            raise RuntimeError(f"Dixon-Coles did not converge: {result.message}")
        self.theta_ = result.x
        self.param_names_ = names
        self._warm_start = dict(zip(names, result.x, strict=True))
        self.n_matches_ = len(history)
        return self

    # --------------------------------------------------------------- predicting
    def _fixture_design(self, fixtures: pd.DataFrame) -> _Design:
        # Fixture lists are public before a season starts, so a newcomer's promotion is known at
        # forecast time. Teams seen in training keep the status they were fitted with.
        combined = {s: set(t) for s, t in self.rosters_.items()}
        for season, teams in rosters(fixtures).items():
            combined[season] = combined.get(season, set()) | teams
        newcomers = promoted_teams(combined) - set(self.teams_)
        return self._design(fixtures, self.promoted_ | newcomers)

    def expected_goals(self, fixtures: pd.DataFrame) -> np.ndarray:
        lam, nu = self._rates(self.theta_, self._fixture_design(fixtures))
        return np.stack([lam, nu], axis=1)

    def score_matrix(self, fixtures: pd.DataFrame) -> np.ndarray:
        rates = self.expected_goals(fixtures)
        goals = np.arange(self.max_goals + 1)
        home_pmf = poisson.pmf(goals[None, :], rates[:, [0]])
        away_pmf = poisson.pmf(goals[None, :], rates[:, [1]])
        matrix = home_pmf[:, :, None] * away_pmf[:, None, :]

        rho = self.coefficients()["rho"]
        lam, nu = rates[:, 0], rates[:, 1]
        matrix[:, 0, 0] *= 1 - lam * nu * rho
        matrix[:, 0, 1] *= 1 + lam * rho
        matrix[:, 1, 0] *= 1 + nu * rho
        matrix[:, 1, 1] *= 1 - rho
        return matrix / matrix.sum(axis=(1, 2), keepdims=True)

    # ---------------------------------------------------------------- inspecting
    def coefficients(self) -> dict[str, float]:
        return dict(zip(self.param_names_, map(float, self.theta_), strict=True))

    def team_strengths(self) -> pd.DataFrame:
        """Attack and defence on the goal scale.

        `goals_for` / `goals_against` are expected goals against an average side at a neutral venue.
        """
        c = self.coefficients()
        base = c["intercept"] + c["home_advantage"] / 2
        rows = []
        for team in self.teams_:
            promoted = team in self.promoted_
            attack = c[f"attack:{team}"] + promoted * c["promoted_attack"]
            defence = c[f"defence:{team}"] + promoted * c["promoted_defence"]
            rows.append(
                {
                    "team": team,
                    "attack": attack,
                    "defence": defence,
                    "goals_for": np.exp(base + attack),
                    "goals_against": np.exp(base + defence),
                }
            )
        return pd.DataFrame(rows).assign(rating=lambda d: d["goals_for"] - d["goals_against"])
