import numpy as np
import pandas as pd
import pytest

from epl.features import ALL_FEATURES, FORM_FEATURES, EloParams, build_features, elo_ratings
from epl.features.elo import MEAN_RATING
from epl.models.base import mask_results


def fixtures(rows: list[tuple[str, str, int, int]], season: int = 2025) -> pd.DataFrame:
    """A tiny match frame from (home, away, home_goals, away_goals), one match a week."""
    return pd.DataFrame(
        [
            {
                "match_id": season * 100 + i,
                "season": season,
                "matchweek": i + 1,
                "kickoff": pd.Timestamp(f"{season}-08-01") + pd.Timedelta(weeks=i),
                "status": "finished",
                "home_team": h,
                "away_team": a,
                "home_goals": hg,
                "away_goals": ag,
                **{
                    f"{side}_{s}": v
                    for side in ("home", "away")
                    for s, v in [
                        ("shots", 10 + i),
                        ("shots_on_target", 4),
                        ("shots_in_box", 6),
                        ("big_chances", 2),
                        ("touches_in_box", 20),
                        ("corners", 5),
                        ("passes", 400),
                        ("possession", 50.0),
                    ]
                },
            }
            for i, (h, a, hg, ag) in enumerate(rows)
        ]
    ).astype({"home_goals": "Int64", "away_goals": "Int64"})


def test_first_match_has_no_history() -> None:
    features = build_features(fixtures([("A", "B", 2, 0)]))

    assert (features[FORM_FEATURES] == 0).all(axis=None)
    assert features.loc[0, "home_elo"] == features.loc[0, "away_elo"] == MEAN_RATING


def test_a_matchs_own_result_never_reaches_its_features() -> None:
    games = [("A", "B", 1, 0), ("B", "A", 2, 2), ("A", "B", 0, 1)]
    original = build_features(fixtures(games))
    changed = build_features(fixtures([*games[:-1], ("A", "B", 7, 0)]))

    pd.testing.assert_frame_equal(original[ALL_FEATURES], changed[ALL_FEATURES])


def test_changing_a_result_only_affects_later_matches(frame: pd.DataFrame) -> None:
    altered = frame.copy()
    target = 20
    altered.loc[target, ["home_goals", "away_goals"]] = [9, 0]

    before, after = build_features(frame), build_features(altered)
    unchanged = frame["kickoff"] <= frame.loc[target, "kickoff"]

    pd.testing.assert_frame_equal(before[unchanged], after[unchanged])
    assert not before[~unchanged].equals(after[~unchanged])


def test_training_and_serving_features_are_identical(frame: pd.DataFrame) -> None:
    """A fixture's features must not depend on whether its result is already known."""
    cut = len(frame) - 3
    as_training = build_features(frame).iloc[cut:]
    as_serving = build_features(pd.concat([frame.iloc[:cut], mask_results(frame.iloc[cut:])])).iloc[cut:]

    # Fixtures within the same window share no information, so compare only the first unplayed one.
    pd.testing.assert_series_equal(as_training.iloc[0], as_serving.iloc[0])


def test_form_uses_only_the_last_five_matches() -> None:
    # A (always at home) goes W W L D W L, then plays a seventh match.
    scores = [(1, 0), (1, 0), (0, 1), (1, 1), (1, 0), (0, 1), (0, 0)]
    features = build_features(fixtures([("A", "B", h, a) for h, a in scores]))

    assert features["home_form_points"].tolist() == [0, 3, 6, 6, 7, 10, 7]
    assert features["away_form_points"].tolist() == [0, 0, 0, 3, 4, 4, 7]


def test_venue_points_count_only_matches_at_that_venue() -> None:
    features = build_features(fixtures([("A", "B", 1, 0), ("B", "A", 0, 1), ("A", "B", 1, 1)]))

    assert features["home_form_points"].tolist() == [0, 0, 6]
    assert features["home_venue_points"].tolist() == [0, 0, 3]


def test_stats_are_averaged_over_previous_matches() -> None:
    features = build_features(fixtures([("A", "B", 1, 0)] * 3))  # A's shots: 10, 11, 12

    assert features["home_avg_shots"].tolist() == [0, 10, 10.5]


def test_form_resets_each_season_but_elo_carries_over() -> None:
    games = pd.concat(
        [fixtures([("A", "B", 3, 0)], 2024), fixtures([("A", "B", 3, 0)], 2025)], ignore_index=True
    )
    features = build_features(games)

    assert (features.loc[1, FORM_FEATURES] == 0).all()
    assert features.loc[1, "home_elo"] > MEAN_RATING > features.loc[1, "away_elo"]


def test_elo_updates_are_zero_sum(frame: pd.DataFrame) -> None:
    ratings = elo_ratings(frame)
    pre = ratings["home_elo"] + ratings["away_elo"]
    post = ratings["home_elo_post"] + ratings["away_elo_post"]

    np.testing.assert_allclose(pre, post)


def test_elo_regresses_towards_the_mean_between_seasons() -> None:
    params = EloParams(season_regression=0.5)
    games = pd.concat(
        [fixtures([("A", "B", 5, 0)], 2024), fixtures([("A", "B", 0, 0)], 2025)], ignore_index=True
    )
    ratings = elo_ratings(games, params)

    end_of_first = ratings.loc[0, "home_elo_post"]
    assert ratings.loc[1, "home_elo"] == pytest.approx(MEAN_RATING + 0.5 * (end_of_first - MEAN_RATING))


def test_promoted_teams_start_at_the_relegated_teams_average() -> None:
    # 2024: A > B > C > D. In 2025 B plays first (its rating is regressed), then newcomer E debuts.
    first = fixtures(
        [
            ("A", "B", 2, 0),
            ("A", "C", 2, 0),
            ("A", "D", 2, 0),
            ("B", "C", 1, 0),
            ("B", "D", 1, 0),
            ("C", "D", 1, 0),
        ],
        2024,
    )
    second = fixtures([("B", "A", 0, 0), ("E", "A", 0, 0)], 2025)
    ratings = elo_ratings(pd.concat([first, second], ignore_index=True))

    end_of_2024 = {}
    for i in first.index:  # kickoff order, so the last write is each team's final rating
        end_of_2024[first.loc[i, "home_team"]] = ratings.loc[i, "home_elo_post"]
        end_of_2024[first.loc[i, "away_team"]] = ratings.loc[i, "away_elo_post"]
    expected = np.mean([end_of_2024[t] for t in ("B", "C", "D")])

    assert ratings.loc[len(first) + 1, "home_elo"] == pytest.approx(expected)
