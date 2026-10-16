"""Forecasting use-cases shared by the API and the CLI.

Functions here take plain inputs (a match frame, a fitted model) and return
plain data, so they are unit-testable without a database or a web server.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from epl.data.repository import played
from epl.features import EloParams, rating_timeline
from epl.models import zoo
from epl.models.base import GoalsForecaster, mask_results, outcome_probabilities
from epl.models.dixon_coles import DixonColes
from epl.simulation.season import simulate, standings

PRODUCTION_MODEL = "dixon_coles"  # chosen on the tuning season; it also models scorelines
DISPLAY_GOALS = 6  # score grids shown to users cover 0..6 goals


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """`DataFrame.to_dict("records")` with string keys, as JSON responses need."""
    return [{str(k): v for k, v in row.items()} for row in frame.to_dict("records")]


def markets(matrix: np.ndarray) -> dict[str, Any]:
    """Derived probabilities from one (G, G) score matrix."""
    goals = np.arange(matrix.shape[0])
    total = goals[:, None] + goals[None, :]
    top = np.argsort(matrix, axis=None)[::-1][:5]
    return {
        "over_2_5": float(matrix[total > 2.5].sum()),
        "both_teams_score": float(matrix[1:, 1:].sum()),
        "home_clean_sheet": float(matrix[:, 0].sum()),
        "away_clean_sheet": float(matrix[0, :].sum()),
        "top_scorelines": [
            {"home": int(i), "away": int(j), "probability": float(matrix[i, j])}
            for i, j in (np.unravel_index(k, matrix.shape) for k in top)
        ],
    }


def predict_fixtures(model: GoalsForecaster, fixtures: pd.DataFrame) -> list[dict[str, Any]]:
    if fixtures.empty:
        return []
    matrices = model.score_matrix(fixtures)
    probs = outcome_probabilities(matrices)
    xg = model.expected_goals(fixtures)
    out = []
    for k, row in enumerate(fixtures.to_dict("records")):
        out.append(
            {
                "match_id": int(row["match_id"]),
                "season": int(row["season"]),
                "matchweek": int(row["matchweek"]),
                "kickoff": row["kickoff"].isoformat(),
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "p_home": float(probs[k, 0]),
                "p_draw": float(probs[k, 1]),
                "p_away": float(probs[k, 2]),
                "expected_home_goals": float(xg[k, 0]),
                "expected_away_goals": float(xg[k, 1]),
                **markets(matrices[k]),
            }
        )
    return out


def matchup(
    model: GoalsForecaster, home: str, away: str, season: int, kickoff: pd.Timestamp
) -> dict[str, Any]:
    """Forecast for any pairing, e.g. a what-if fixture from the dashboard."""
    fixture = pd.DataFrame(
        [
            {
                "match_id": 0,
                "season": season,
                "matchweek": 0,
                "kickoff": kickoff,
                "home_team": home,
                "away_team": away,
            }
        ]
    )
    result = predict_fixtures(model, fixture)[0]
    matrix = model.score_matrix(fixture)[0][: DISPLAY_GOALS + 1, : DISPLAY_GOALS + 1]
    return result | {"score_matrix": matrix.round(5).tolist()}


# ----------------------------------------------------------------------------- season state
def season_cutoff(frame: pd.DataFrame, season: int, as_of_matchweek: int | None) -> pd.Timestamp | None:
    """Start of the given matchweek (its first kickoff); None means 'now'."""
    if as_of_matchweek is None:
        return None
    games = frame[(frame["season"] == season) & (frame["matchweek"] == as_of_matchweek)]
    if games.empty:
        raise ValueError(f"season {season} has no matchweek {as_of_matchweek}")
    return pd.Timestamp(games["kickoff"].min())


def split_season(
    frame: pd.DataFrame, season: int, cutoff: pd.Timestamp | None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(history for fitting, season matches already played, season fixtures still to play) at `cutoff`."""
    finished = frame["status"] == "finished"
    before = frame["kickoff"] < cutoff if cutoff is not None else pd.Series(True, index=frame.index)
    in_season = frame["season"] == season
    history = frame[finished & before]
    done = frame[in_season & finished & before]
    todo = frame[in_season & ~(finished & before)]
    return history, done, mask_results(todo)


def table(frame: pd.DataFrame, season: int, as_of_matchweek: int | None = None) -> pd.DataFrame:
    _, done, todo = split_season(frame, season, season_cutoff(frame, season, as_of_matchweek))
    teams = sorted(
        set(done["home_team"]) | set(done["away_team"]) | set(todo["home_team"]) | set(todo["away_team"])
    )
    return standings(done, teams)


@dataclass(frozen=True)
class SimulationKey:
    season: int
    as_of_matchweek: int  # 0 = latest results
    model_version: str
    n_runs: int


def simulation_key(
    frame: pd.DataFrame,
    season: int,
    as_of_matchweek: int | None,
    n_runs: int,
    params: dict[str, Any] | None = None,
) -> SimulationKey:
    """Cache key: changes whenever the data or the model's hyperparameters change."""
    history, _, _ = split_season(frame, season, season_cutoff(frame, season, as_of_matchweek))
    payload = json.dumps(
        {
            "data": history[["match_id", "home_goals", "away_goals"]].astype(str).to_numpy().tolist(),
            "params": (params or zoo.hyperparameters())["dixon_coles"],
        }
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return SimulationKey(season, as_of_matchweek or 0, f"{PRODUCTION_MODEL}-{digest}", n_runs)


def season_simulation(
    frame: pd.DataFrame,
    season: int,
    as_of_matchweek: int | None,
    n_runs: int,
    *,
    seed: int = 0,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refit the goals model on everything known at the cutoff, then simulate the remaining fixtures."""
    cutoff = season_cutoff(frame, season, as_of_matchweek)
    history, done, todo = split_season(frame, season, cutoff)
    model = zoo.build(PRODUCTION_MODEL, params)
    assert isinstance(model, DixonColes)
    model.fit(history)
    matrices = (
        model.score_matrix(todo) if len(todo) else np.zeros((0, model.max_goals + 1, model.max_goals + 1))
    )
    result = simulate(done, todo, matrices, n_runs=n_runs, seed=seed)

    final = None
    if not todo.empty and frame.loc[todo.index, "status"].eq("finished").all():
        final = table(frame, season).set_index("team")["position"].to_dict()  # backtest: compare with reality
    return {
        "season": season,
        "as_of_matchweek": as_of_matchweek,
        "cutoff": None if cutoff is None else cutoff.isoformat(),
        "matches_played": len(done),
        "matches_remaining": len(todo),
        **result.to_dict(),
        "actual_final_positions": final,
    }


# ----------------------------------------------------------------------------- ratings
def team_ratings(frame: pd.DataFrame, model: DixonColes, elo_params: EloParams) -> list[dict[str, Any]]:
    timeline = rating_timeline(frame, elo_params)
    latest_elo = timeline.groupby("team")["rating"].last()
    strengths = model.team_strengths().set_index("team")
    current_teams = set(frame.loc[frame["season"] == frame["season"].max(), "home_team"])
    rows = []
    for team in sorted(current_teams):
        s = strengths.loc[team] if team in strengths.index else None
        rows.append(
            {
                "team": team,
                "elo": float(latest_elo.get(team, np.nan)),
                "attack": None if s is None else float(s["goals_for"]),
                "defence": None if s is None else float(s["goals_against"]),
                "rating": None if s is None else float(s["rating"]),
            }
        )
    return sorted(rows, key=lambda r: -(r["rating"] if r["rating"] is not None else -9))


def elo_history(frame: pd.DataFrame, team: str, elo_params: EloParams) -> list[dict[str, Any]]:
    timeline = rating_timeline(played(frame), elo_params)
    rows = timeline[timeline["team"] == team]
    return [
        {
            "match_id": int(r["match_id"]),
            "season": int(r["season"]),
            "kickoff": r["kickoff"].isoformat(),
            "elo": float(r["rating"]),
        }
        for r in rows.to_dict("records")
    ]
