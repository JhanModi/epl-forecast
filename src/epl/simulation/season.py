"""Monte Carlo simulation of the rest of a season.

Each remaining fixture's scoreline is sampled from the goals model's score
matrix; the samples are added to the real table so far and ranked with the
Premier League tie-breakers (points, goal difference, goals scored; any tie
left is broken at random). Fully vectorised over simulations: 10,000 seasons
of 300+ fixtures run in well under a second.

Limitation: team strengths are frozen at the simulation date. Real strengths
drift (injuries, transfers, managers), so the spread of outcomes is slightly
too narrow far from the end of the season.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

TITLE, TOP_FOUR, RELEGATION = 1, 4, 3


@dataclass
class SimulationResult:
    teams: list[str]
    position_probabilities: np.ndarray  # (team, finishing position) -> probability
    points: np.ndarray  # (run, team) final points
    current: pd.DataFrame  # the real table at the simulation date
    n_runs: int

    def summary(self) -> pd.DataFrame:
        positions = np.arange(1, len(self.teams) + 1)
        probs = self.position_probabilities
        out = pd.DataFrame(
            {
                "team": self.teams,
                "expected_points": self.points.mean(axis=0),
                "points_p05": np.percentile(self.points, 5, axis=0),
                "points_p95": np.percentile(self.points, 95, axis=0),
                "expected_position": probs @ positions,
                "p_title": probs[:, :TITLE].sum(axis=1),
                "p_top_four": probs[:, :TOP_FOUR].sum(axis=1),
                "p_relegation": probs[:, -RELEGATION:].sum(axis=1),
            }
        )
        return out.merge(self.current, on="team").sort_values("expected_position", ignore_index=True)

    def to_dict(self) -> dict[str, Any]:
        summary = self.summary()
        order = [self.teams.index(t) for t in summary["team"]]
        return {
            "n_runs": self.n_runs,
            "teams": summary.round(4).to_dict("records"),
            "position_probabilities": self.position_probabilities[order].round(4).tolist(),
        }


def standings(played: pd.DataFrame, teams: list[str] | None = None) -> pd.DataFrame:
    """League table from finished matches, sorted by points, goal difference, goals scored."""
    rows = []
    for side, opp in (("home", "away"), ("away", "home")):
        gf, ga = played[f"{side}_goals"].astype(int), played[f"{opp}_goals"].astype(int)
        rows.append(pd.DataFrame({"team": played[f"{side}_team"], "gf": gf, "ga": ga}))
    long = pd.concat(rows, ignore_index=True)
    long = long.assign(
        won=long["gf"] > long["ga"], drawn=long["gf"] == long["ga"], lost=long["gf"] < long["ga"]
    )
    table = long.groupby("team").agg(
        played=("gf", "size"),
        won=("won", "sum"),
        drawn=("drawn", "sum"),
        lost=("lost", "sum"),
        goals_for=("gf", "sum"),
        goals_against=("ga", "sum"),
    )
    if teams is not None:
        table = table.reindex(teams, fill_value=0)
    table["goal_difference"] = table["goals_for"] - table["goals_against"]
    table["points"] = 3 * table["won"] + table["drawn"]
    table = table.astype(int).sort_values(["points", "goal_difference", "goals_for"], ascending=False)
    table.insert(0, "position", np.arange(1, len(table) + 1))
    return table.reset_index().rename(columns={"index": "team"})


def simulate(
    played: pd.DataFrame,
    remaining: pd.DataFrame,
    score_matrices: np.ndarray,
    n_runs: int = 10_000,
    seed: int = 0,
) -> SimulationResult:
    """Simulate `remaining` on top of `played` (one season); `score_matrices` align with `remaining`."""
    teams = sorted(
        set(played["home_team"])
        | set(played["away_team"])
        | set(remaining["home_team"])
        | set(remaining["away_team"])
    )
    index = {t: i for i, t in enumerate(teams)}
    current = standings(played, teams) if len(played) else standings(played.iloc[:0], teams)
    base = current.set_index("team").loc[teams]

    rng = np.random.default_rng(seed)
    points = np.tile(base["points"].to_numpy(float), (n_runs, 1))
    goal_diff = np.tile(base["goal_difference"].to_numpy(float), (n_runs, 1))
    goals_for = np.tile(base["goals_for"].to_numpy(float), (n_runs, 1))

    size = score_matrices.shape[1]
    cdfs = np.cumsum(score_matrices.reshape(len(remaining), -1), axis=1)
    cdfs[:, -1] = 1.0  # guard against floating-point shortfall
    uniforms = rng.random((n_runs, len(remaining)))
    for f, (home, away) in enumerate(zip(remaining["home_team"], remaining["away_team"], strict=True)):
        outcome = np.searchsorted(cdfs[f], uniforms[:, f], side="right")
        hg, ag = outcome // size, outcome % size
        h, a = index[home], index[away]
        points[:, h] += np.where(hg > ag, 3, np.where(hg == ag, 1, 0))
        points[:, a] += np.where(ag > hg, 3, np.where(hg == ag, 1, 0))
        goal_diff[:, h] += hg - ag
        goal_diff[:, a] += ag - hg
        goals_for[:, h] += hg
        goals_for[:, a] += ag

    # Lexicographic sort key; the uniform jitter breaks any remaining tie at random.
    key = points * 1e7 + (goal_diff + 500) * 1e4 + goals_for * 10 + rng.random(points.shape)
    ranks = np.argsort(np.argsort(-key, axis=1), axis=1)  # 0 = champion
    position_counts = np.zeros((len(teams), len(teams)))
    for t in range(len(teams)):
        position_counts[t] = np.bincount(ranks[:, t], minlength=len(teams))

    return SimulationResult(teams, position_counts / n_runs, points, current, n_runs)
