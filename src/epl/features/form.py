"""Leakage-free pre-match features from a match frame.

Approach: compute each team's rolling state *after* every match it has played,
then attach to each fixture the state after the team's latest match strictly
before kickoff (an as-of join). The same function serves training rows and
unplayed fixtures, so there is no training/serving skew, and a match's own
result can never reach its features.

Rolling form resets each season (a deliberate choice: squads change over the
summer); Elo ratings carry strength across seasons instead.
"""

import hashlib
from collections import OrderedDict

import numpy as np
import pandas as pd

from epl.features.elo import EloParams, elo_ratings

WINDOW = 5

# Rolling statistic -> how it is aggregated over the window.
TEAM_ROLLING = {
    "points": "sum",
    "goals_for": "sum",
    "goals_against": "sum",
    "shots": "mean",
    "shots_on_target": "mean",
    "shots_in_box": "mean",
    "big_chances": "mean",
    "possession": "mean",
    "shots_against": "mean",
    "big_chances_against": "mean",
}
SUMMED = [c for c, agg in TEAM_ROLLING.items() if agg == "sum"]


def team_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per team per fixture (played or not)."""
    sides = []
    for side, opp in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "row": frame.index,
                    "match_id": frame["match_id"],
                    "season": frame["season"],
                    "kickoff": frame["kickoff"],
                    "team": frame[f"{side}_team"],
                    "venue": side,
                    "played": frame["status"].eq("finished"),
                    "goals_for": frame[f"{side}_goals"].astype(float),
                    "goals_against": frame[f"{opp}_goals"].astype(float),
                    "shots": frame[f"{side}_shots"],
                    "shots_on_target": frame[f"{side}_shots_on_target"],
                    "shots_in_box": frame[f"{side}_shots_in_box"],
                    "big_chances": frame[f"{side}_big_chances"],
                    "possession": frame[f"{side}_possession"],
                    "shots_against": frame[f"{opp}_shots"],
                    "big_chances_against": frame[f"{opp}_big_chances"],
                }
            )
        )
    rows = pd.concat(sides, ignore_index=True)
    rows["points"] = np.select(
        [rows["goals_for"] > rows["goals_against"], rows["goals_for"] == rows["goals_against"]],
        [3.0, 1.0],
        0.0,
    )
    rows.loc[~rows["played"], "points"] = np.nan
    return rows.sort_values(["kickoff", "match_id"], ignore_index=True)


def _states(played: pd.DataFrame, by: list[str], columns: dict[str, str]) -> pd.DataFrame:
    """Rolling aggregate of each column over the team's last WINDOW matches, *including* the current one."""
    grouped = played.groupby(by, sort=False)
    states = played[[*by, "kickoff"]].copy()
    for column, agg in columns.items():
        states[column] = grouped[column].transform(lambda s, a=agg: s.rolling(WINDOW, min_periods=1).agg(a))
    return states


def _as_of(left: pd.DataFrame, states: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """For each left row, the latest state strictly before its kickoff (same team, same season)."""
    joined = pd.merge_asof(
        left.sort_values("kickoff"),
        states.sort_values("kickoff"),
        on="kickoff",
        by=by,
        allow_exact_matches=False,  # a match's own result never feeds its features
        direction="backward",
    )
    return joined.set_index("_key").sort_index()


def build_features(frame: pd.DataFrame, elo_params: EloParams = EloParams()) -> pd.DataFrame:
    """Index-aligned with `frame`: one row of pre-match features per fixture."""
    rows = team_rows(frame)
    played = rows[rows["played"]].copy()
    played[list(TEAM_ROLLING)] = played[list(TEAM_ROLLING)].fillna(0.0)
    keys = rows[["row", "season", "kickoff", "team", "venue"]].assign(
        _key=lambda d: d["venue"] + "|" + d["row"].astype(str)
    )

    overall = _as_of(keys, _states(played, ["team", "season"], TEAM_ROLLING), ["team", "season"])
    venue = _as_of(
        keys, _states(played, ["team", "season", "venue"], {"points": "sum"}), ["team", "season", "venue"]
    )

    features = pd.DataFrame(index=frame.index)
    for side in ("home", "away"):
        side_keys = side + "|" + frame.index.astype(str)
        state = overall.loc[side_keys].fillna({c: 0.0 for c in TEAM_ROLLING})
        for column in TEAM_ROLLING:
            name = f"{side}_form_{column}" if column in SUMMED else f"{side}_avg_{column}"
            features[name] = state[column].to_numpy()
        features[f"{side}_form_goal_difference"] = (state["goals_for"] - state["goals_against"]).to_numpy()
        features[f"{side}_venue_points"] = venue.loc[side_keys, "points"].fillna(0.0).to_numpy()

    elo = elo_ratings(frame, elo_params)
    features["home_elo"], features["away_elo"] = elo["home_elo"], elo["away_elo"]

    features["elo_difference"] = features["home_elo"] - features["away_elo"]
    features["form_points_difference"] = features["home_form_points"] - features["away_form_points"]
    features["form_goal_difference_difference"] = (
        features["home_form_goal_difference"] - features["away_form_goal_difference"]
    )
    for stat in ("shots", "shots_on_target", "big_chances", "possession"):
        features[f"{stat}_difference"] = features[f"home_avg_{stat}"] - features[f"away_avg_{stat}"]
    return features


_CACHE: OrderedDict[tuple[str, EloParams], pd.DataFrame] = OrderedDict()
_CACHE_SIZE = 16


def fingerprint(frame: pd.DataFrame) -> str:
    """Content hash of a frame (values and index), used as a cache key."""
    hashes = pd.util.hash_pandas_object(frame, index=True).to_numpy()
    return hashlib.sha1(hashes.tobytes(), usedforsecurity=False).hexdigest()


def cached_features(frame: pd.DataFrame, elo_params: EloParams = EloParams()) -> pd.DataFrame:
    """`build_features` with a small LRU cache.

    In a backtest step several models need features for the same history; this
    computes them once. Callers must treat the result as read-only.
    """
    key = (fingerprint(frame), elo_params)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    features = build_features(frame, elo_params)
    _CACHE[key] = features
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return features


ELO_FEATURES = ["elo_difference"]
FORM_FEATURES = [
    "home_form_points",
    "away_form_points",
    "home_form_goal_difference",
    "away_form_goal_difference",
    "home_venue_points",
    "away_venue_points",
    "form_points_difference",
    "form_goal_difference_difference",
]
STATS_FEATURES = [
    "home_avg_shots_on_target",
    "away_avg_shots_on_target",
    "home_avg_big_chances",
    "away_avg_big_chances",
    "home_avg_shots_against",
    "away_avg_shots_against",
    "home_avg_big_chances_against",
    "away_avg_big_chances_against",
    "shots_difference",
    "shots_on_target_difference",
    "big_chances_difference",
    "possession_difference",
]
ALL_FEATURES = ELO_FEATURES + FORM_FEATURES + STATS_FEATURES
