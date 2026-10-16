from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epl.api.main import create_app
from epl.core.config import Settings
from epl.models.registry import ModelRegistry

API = "/api/v1"


def test_health(client: TestClient) -> None:
    response = client.get(f"{API}/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_database_and_model(client: TestClient) -> None:
    assert client.get(f"{API}/ready").json() == {
        "status": "ready",
        "database": True,
        "model": True,
        "detail": None,
    }


def test_ready_is_degraded_without_a_model(settings: Settings) -> None:
    with TestClient(create_app(settings)) as bare:
        body = bare.get(f"{API}/ready").json()

    assert body["status"] == "degraded"
    assert body["model"] is False


def test_meta_describes_the_served_data(client: TestClient) -> None:
    meta = client.get(f"{API}/meta").json()

    assert meta["current_season"] == 2024
    assert meta["production_model"] == "dixon_coles"
    assert meta["matches_played"] > 0 and meta["matches_scheduled"] > 0


def test_request_id_is_echoed_and_timed(client: TestClient) -> None:
    response = client.get(f"{API}/health", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"
    assert response.headers["Server-Timing"].startswith("app;dur=")


def test_fixtures_filter_by_status(client: TestClient) -> None:
    scheduled = client.get(f"{API}/seasons/2024/fixtures", params={"status": "scheduled"}).json()
    finished = client.get(f"{API}/seasons/2024/fixtures", params={"status": "finished"}).json()

    assert scheduled and all(f["home_goals"] is None for f in scheduled)
    assert finished and all(f["home_goals"] is not None for f in finished)


def test_standings_are_a_valid_table(client: TestClient) -> None:
    table = client.get(f"{API}/seasons/2023/standings").json()

    assert [row["position"] for row in table] == list(range(1, 7))
    assert sum(row["won"] for row in table) == sum(row["lost"] for row in table)
    points = [row["points"] for row in table]
    assert points == sorted(points, reverse=True)


def test_unknown_season_is_a_problem_json_404(client: TestClient) -> None:
    response = client.get(f"{API}/seasons/2099/standings")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["request_id"]


def test_validation_errors_are_422(client: TestClient) -> None:
    response = client.get(f"{API}/seasons/2024/simulation", params={"runs": 5})

    assert response.status_code == 422
    assert "runs" in response.json()["detail"]


def test_simulation_is_cached_after_the_first_call(client: TestClient) -> None:
    params = {"runs": 1000}
    first = client.get(f"{API}/seasons/2024/simulation", params=params).json()
    second = client.get(f"{API}/seasons/2024/simulation", params=params).json()

    assert (first["cached"], second["cached"]) == (False, True)
    assert first["teams"] == second["teams"]
    assert sum(t["p_title"] for t in first["teams"]) == pytest.approx(1.0, abs=1e-3)


def test_upcoming_predictions(client: TestClient) -> None:
    body = client.get(f"{API}/predictions/upcoming", params={"limit": 3}).json()

    assert body["model"] == "dixon_coles"
    assert len(body["predictions"]) == 3
    for p in body["predictions"]:
        assert p["p_home"] + p["p_draw"] + p["p_away"] == pytest.approx(1.0)
        assert len(p["top_scorelines"]) == 5


def test_matchup_returns_a_score_grid(client: TestClient) -> None:
    body = client.get(f"{API}/predictions/matchup", params={"home": "Team A", "away": "Team F"}).json()

    assert len(body["score_matrix"]) == 7
    assert body["p_home"] > body["p_away"]


@pytest.mark.parametrize(
    ("params", "status"),
    [({"home": "Team A", "away": "Team A"}, 422), ({"home": "Team A", "away": "Nobody FC"}, 404)],
)
def test_matchup_rejects_bad_teams(client: TestClient, params: dict[str, str], status: int) -> None:
    assert client.get(f"{API}/predictions/matchup", params=params).status_code == status


def test_ratings_and_elo_history(client: TestClient) -> None:
    ratings = client.get(f"{API}/ratings").json()
    teams = client.get(f"{API}/teams").json()
    history = client.get(f"{API}/teams/{teams[0]['id']}/elo").json()

    assert len(ratings) == 6
    assert ratings[0]["rating"] >= ratings[-1]["rating"]
    assert history and all("elo" in point for point in history)


def test_models_lists_registered_cards(client: TestClient, seeded: Settings) -> None:
    cards = client.get(f"{API}/models").json()

    assert {c["name"] for c in cards} == {"prior", "dixon_coles"}
    assert cards[0]["data_fingerprint"]
    assert ModelRegistry(seeded.artifacts_dir).latest_version("dixon_coles") in {c["version"] for c in cards}


def test_backtest_report_is_404_until_generated(client: TestClient) -> None:
    assert client.get(f"{API}/evaluation/backtest").status_code == 404


def test_admin_requires_a_valid_token(client: TestClient) -> None:
    assert client.post(f"{API}/admin/refresh").status_code == 401
    assert client.post(f"{API}/admin/refresh", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_admin_disabled_without_configured_token(seeded: Settings) -> None:
    open_settings = seeded.model_copy(update={"admin_token": None})
    with TestClient(create_app(open_settings)) as c:
        assert c.post(f"{API}/admin/refresh", headers={"X-Admin-Token": "anything"}).status_code == 403


def test_openapi_schema_is_published(client: TestClient) -> None:
    schema = client.get(f"{API}/openapi.json").json()

    assert f"{API}/predictions/upcoming" in schema["paths"]


def test_dashboard_is_served_with_spa_fallback(seeded: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>dashboard</html>")
    (dist / "favicon.svg").write_text("<svg/>")
    with TestClient(create_app(seeded.model_copy(update={"frontend_dist": dist}))) as app:
        assert "dashboard" in app.get("/").text
        assert "dashboard" in app.get("/simulator").text  # client-side route
        assert app.get("/favicon.svg").text == "<svg/>"
        assert app.get("/../pyproject.toml").text != "secret"  # no path traversal
        missing = app.get("/api/v1/does-not-exist")
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("application/problem+json")
