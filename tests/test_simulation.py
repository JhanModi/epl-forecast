import numpy as np
import pandas as pd

from epl.services import forecasting
from epl.simulation.season import simulate, standings


def certain(home_goals: int, away_goals: int, size: int = 11) -> np.ndarray:
    matrix = np.zeros((size, size))
    matrix[home_goals, away_goals] = 1.0
    return matrix


def test_standings_tie_breakers() -> None:
    played = pd.DataFrame(
        {
            "home_team": ["A", "C", "B"],
            "away_team": ["B", "D", "D"],
            "home_goals": [1, 3, 0],
            "away_goals": [0, 0, 0],
        }
    )
    table = standings(played)

    # A and C both have 3 points; C ranks first on goal difference. B and D drew one each.
    assert table["team"].tolist()[:2] == ["C", "A"]
    assert table.set_index("team").loc["B", "points"] == 1


def test_certain_results_give_certain_table() -> None:
    played = pd.DataFrame({"home_team": ["A"], "away_team": ["B"], "home_goals": [0], "away_goals": [0]})
    remaining = pd.DataFrame({"home_team": ["B", "A"], "away_team": ["A", "B"]})
    matrices = np.stack([certain(2, 0), certain(0, 1)])  # B wins both

    result = simulate(played, remaining, matrices, n_runs=500)
    summary = result.summary().set_index("team")

    assert summary.loc["B", "expected_points"] == 7
    assert summary.loc["B", "p_title"] == 1.0


def test_position_probabilities_are_doubly_stochastic(frame: pd.DataFrame) -> None:
    season = frame[frame["season"] == 2024]
    played, remaining = season.iloc[:10], season.iloc[10:]
    rng = np.random.default_rng(0)
    matrices = rng.dirichlet(np.ones(121), size=len(remaining)).reshape(-1, 11, 11)

    probs = simulate(played, remaining, matrices, n_runs=2_000).position_probabilities

    np.testing.assert_allclose(probs.sum(axis=1), 1.0)  # each team finishes somewhere
    np.testing.assert_allclose(probs.sum(axis=0), 1.0)  # each position is filled once


def test_historical_simulation_reports_the_real_final_table(frame: pd.DataFrame) -> None:
    result = forecasting.season_simulation(frame, 2024, as_of_matchweek=3, n_runs=1_000)

    assert result["matches_played"] + result["matches_remaining"] == (frame["season"] == 2024).sum()
    assert result["actual_final_positions"] is not None
    assert sorted(result["actual_final_positions"].values()) == list(range(1, 7))


def test_simulation_cache_key_changes_with_the_data(frame: pd.DataFrame) -> None:
    key = forecasting.simulation_key(frame, 2024, 3, 1000)
    altered = frame.copy()
    altered.loc[0, "home_goals"] = 7

    assert forecasting.simulation_key(frame, 2024, 3, 1000) == key
    assert forecasting.simulation_key(altered, 2024, 3, 1000) != key
