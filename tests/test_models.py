from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import approx_fprime

from epl.models import zoo
from epl.models.base import mask_results, outcome_index, outcome_probabilities
from epl.models.classifiers import OrderedLogit, PriorBaseline
from epl.models.dixon_coles import DixonColes, promoted_teams, rosters
from epl.models.registry import ModelRegistry
from tests import factories


@pytest.fixture(scope="module")
def big_league() -> tuple[factories.League, pd.DataFrame]:
    """Enough matches (8 teams x 6 rounds x 2 seasons) for parameter recovery."""
    league = factories.default_league(n_teams=8, seed=1)
    return league, factories.match_frame(league, seasons=(2023, 2024), rounds=6)


def test_dixon_coles_gradient_matches_finite_differences(frame: pd.DataFrame) -> None:
    model = DixonColes().fit(frame)
    design = model._design(frame, model.promoted_)
    rng = np.random.default_rng(0)
    theta = model.theta_ + rng.normal(0, 0.05, model.theta_.shape)
    theta[model.param_names_.index("rho")] = 0.08  # exercise every branch of the τ correction

    analytic = model._objective(theta, design)[1]
    numeric = approx_fprime(theta, lambda t: model._objective(t, design)[0], 1e-6)

    np.testing.assert_allclose(analytic, numeric, rtol=1e-4, atol=1e-3)


def test_dixon_coles_recovers_known_parameters(big_league: tuple[factories.League, pd.DataFrame]) -> None:
    league, frame = big_league
    model = DixonColes(xi=0.0, prior_sd=10.0).fit(frame)  # no decay, negligible prior: plain MLE
    c = model.coefficients()

    assert c["home_advantage"] == pytest.approx(league.home_advantage, abs=0.08)
    fitted = [c[f"attack:{t}"] - c[f"defence:{t}"] for t in league.teams]
    true = [league.attack[t] - league.defence[t] for t in league.teams]
    assert np.corrcoef(fitted, true)[0, 1] > 0.95


def test_score_matrix_is_a_probability_distribution(frame: pd.DataFrame) -> None:
    model = DixonColes().fit(frame)
    matrices = model.score_matrix(frame.head(5))

    assert matrices.shape == (5, 11, 11)
    assert (matrices >= 0).all()
    np.testing.assert_allclose(matrices.sum(axis=(1, 2)), 1.0)
    np.testing.assert_allclose(outcome_probabilities(matrices).sum(axis=1), 1.0)


def test_stronger_team_is_favoured(big_league: tuple[factories.League, pd.DataFrame]) -> None:
    _, frame = big_league
    model = DixonColes().fit(frame)
    strong_v_weak = frame.head(1).assign(home_team="Team A", away_team="Team H")
    weak_v_strong = frame.head(1).assign(home_team="Team H", away_team="Team A")

    assert model.predict_proba(strong_v_weak)[0, 0] > 0.6
    assert model.predict_proba(weak_v_strong)[0, 2] > model.predict_proba(weak_v_strong)[0, 0]


def test_unseen_promoted_team_gets_the_promoted_prior(frame: pd.DataFrame) -> None:
    model = DixonColes().fit(frame)
    c = model.coefficients()
    # A newcomer replacing a team next season is "promoted": prior attack 0 + promoted offset.
    fixture = frame.head(1).assign(season=2025, home_team="Newcomer", away_team="Team A")
    next_season = pd.concat(
        [fixture, frame.head(1).assign(season=2025, home_team="Team B", away_team="Team C")]
    )

    expected_home = np.exp(c["intercept"] + c["home_advantage"] + c["promoted_attack"] + c["defence:Team A"])
    assert model.expected_goals(next_season)[0, 0] == pytest.approx(expected_home)


def test_promoted_teams_are_new_arrivals_or_known_first_season() -> None:
    by_season = {2023: {"Burnley", "Arsenal"}, 2024: {"Arsenal", "Ipswich Town"}}

    assert promoted_teams(by_season) == {"Burnley", "Ipswich Town"}


def test_rosters_group_teams_by_season(frame: pd.DataFrame) -> None:
    assert set(rosters(frame)) == {2023, 2024}


def test_ordered_logit_learns_direction() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(2000, 1))
    latent = 1.5 * x[:, 0] + rng.logistic(size=2000)
    y = np.select([latent > 0.6, latent > -0.6], [0, 1], 2)  # 0 home, 1 draw, 2 away

    probs = OrderedLogit().fit(x, y).predict_proba(np.array([[2.0], [-2.0]]))

    np.testing.assert_allclose(probs.sum(axis=1), 1.0)
    assert probs[0, 0] > 0.8 and probs[1, 2] > 0.8


def test_prior_baseline_predicts_class_frequencies(frame: pd.DataFrame) -> None:
    probs = PriorBaseline().fit(frame).predict_proba(frame.head(3))
    freq = np.bincount(outcome_index(frame), minlength=3) / len(frame)

    np.testing.assert_allclose(probs, np.tile(freq, (3, 1)))


@pytest.mark.parametrize("name", zoo.MODEL_NAMES)
def test_every_model_returns_valid_probabilities(name: str, frame: pd.DataFrame) -> None:
    history, fixtures = frame.iloc[:-5], mask_results(frame.iloc[-5:])

    probs = zoo.build(name).fit(history).predict_proba(fixtures)

    assert probs.shape == (5, 3)
    assert ((probs >= 0) & (probs <= 1)).all()
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-9)


def test_feature_models_ignore_fixture_results(frame: pd.DataFrame) -> None:
    history, fixtures = frame.iloc[:-5], frame.iloc[-5:]
    model = zoo.build("logistic_regression").fit(history)
    tampered = fixtures.assign(home_goals=9, away_goals=0, status="finished")

    np.testing.assert_allclose(model.predict_proba(fixtures), model.predict_proba(tampered))


def test_unknown_model_name_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown model"):
        zoo.build("crystal_ball")


@pytest.mark.parametrize("name", zoo.MODEL_NAMES)
def test_every_model_survives_a_registry_round_trip(name: str, frame: pd.DataFrame, tmp_path: Path) -> None:
    history, fixtures = frame.iloc[:-5], mask_results(frame.iloc[-5:])
    model = zoo.build(name).fit(history)
    registry = ModelRegistry(tmp_path)

    card = registry.save(model, history)
    loaded, loaded_card = registry.load(name)

    assert loaded_card.version == card.version == registry.latest_version(name)
    assert loaded_card.n_matches == len(history)
    np.testing.assert_allclose(loaded.predict_proba(fixtures), model.predict_proba(fixtures))
