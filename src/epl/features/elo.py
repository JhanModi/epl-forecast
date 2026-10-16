"""Elo ratings that carry team strength across seasons.

Unlike rolling form, ratings persist across summers (regressed part-way to the
mean), which removes the early-season cold start. Promoted teams start at the
average rating of the three teams that finished bottom of the previous season.

One pass runs over *all* rows of a match frame in kickoff order. Unplayed rows
get a pre-match rating but never update it, so training rows and future
fixtures are rated by exactly the same code.
"""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

MEAN_RATING = 1500.0


@dataclass(frozen=True)
class EloParams:
    k: float = 20.0
    home_advantage: float = 55.0
    season_regression: float = 0.25  # share of the distance to the mean removed each summer
    margin_of_victory: bool = True

    def to_dict(self) -> dict[str, float | bool]:
        return asdict(self)


def expected_home_score(home_rating: float, away_rating: float, home_advantage: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(home_rating + home_advantage - away_rating) / 400))


def margin_multiplier(goal_difference: int, winner_rating_gap: float) -> float:
    """FiveThirtyEight-style: big wins move ratings more, damped when the favourite wins."""
    if goal_difference == 0:
        return 1.0
    return float(np.log1p(abs(goal_difference)) * 2.2 / (winner_rating_gap * 0.001 + 2.2))


class _Ratings:
    def __init__(self, params: EloParams) -> None:
        self.params = params
        self.rating: dict[str, float] = {}
        self.last_season: dict[str, int] = {}
        self.points: dict[int, dict[str, int]] = {}  # season -> team -> league points
        self.season_end: dict[int, dict[str, float]] = {}  # season -> ratings when it finished
        self.season: int | None = None

    def before(self, team: str, season: int) -> float:
        if self.season is not None and season > self.season:
            self.season_end[self.season] = dict(self.rating)
        self.season = season if self.season is None else max(self.season, season)
        if team not in self.rating:
            self.rating[team] = self._promoted_rating(season)
        elif self.last_season[team] < season:
            gap = self.rating[team] - MEAN_RATING
            self.rating[team] = MEAN_RATING + (1 - self.params.season_regression) * gap
        self.last_season[team] = season
        return self.rating[team]

    def _promoted_rating(self, season: int) -> float:
        previous = self.points.get(season - 1)
        if not previous:  # first season in the data: no information, start at the mean
            return MEAN_RATING
        bottom_three = sorted(previous, key=previous.__getitem__)[:3]
        return float(np.mean([self.season_end[season - 1][t] for t in bottom_three]))

    def update(self, season: int, home: str, away: str, home_goals: int, away_goals: int) -> None:
        p = self.params
        rh, ra = self.rating[home], self.rating[away]
        actual = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
        k = p.k
        if p.margin_of_victory:
            winner_gap = (rh + p.home_advantage - ra) * (1 if home_goals > away_goals else -1)
            k *= margin_multiplier(home_goals - away_goals, winner_gap)
        delta = k * (actual - expected_home_score(rh, ra, p.home_advantage))
        self.rating[home] += delta
        self.rating[away] -= delta

        table = self.points.setdefault(season, {})
        table[home] = table.get(home, 0) + (3 if actual == 1 else 1 if actual == 0.5 else 0)
        table[away] = table.get(away, 0) + (3 if actual == 0 else 1 if actual == 0.5 else 0)


def elo_ratings(frame: pd.DataFrame, params: EloParams = EloParams()) -> pd.DataFrame:
    """Index-aligned with `frame`: home_elo, away_elo (pre-match) and *_elo_post (NaN if unplayed)."""
    state = _Ratings(params)
    values = np.full((len(frame), 4), np.nan)
    columns = ["season", "home_team", "away_team", "status", "home_goals", "away_goals"]
    order = np.lexsort((frame["match_id"].to_numpy(), frame["kickoff"].to_numpy()))
    rows = frame[columns].to_numpy(dtype=object)

    for i in order:
        season, home, away, status, hg, ag = rows[i]
        values[i, 0], values[i, 1] = state.before(home, season), state.before(away, season)
        if status == "finished":
            state.update(season, home, away, int(hg), int(ag))
            values[i, 2], values[i, 3] = state.rating[home], state.rating[away]
    return pd.DataFrame(
        values, index=frame.index, columns=["home_elo", "away_elo", "home_elo_post", "away_elo_post"]
    )


def rating_timeline(frame: pd.DataFrame, params: EloParams = EloParams()) -> pd.DataFrame:
    """One row per team per played match: the team's rating after that match."""
    played = frame[frame["status"] == "finished"]
    ratings = elo_ratings(played, params)
    base = played[["match_id", "season", "kickoff"]]
    return pd.concat(
        [
            base.assign(team=played["home_team"], rating=ratings["home_elo_post"]),
            base.assign(team=played["away_team"], rating=ratings["away_elo_post"]),
        ],
        ignore_index=True,
    ).sort_values(["kickoff", "match_id"], ignore_index=True)
