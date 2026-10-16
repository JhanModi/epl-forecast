import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from epl.cli import app as cli
from epl.core import config
from epl.core.config import Settings
from epl.core.logging import JsonFormatter, request_id
from epl.data.db import session_scope
from epl.data.repository import match_frame
from epl.data.sources.premier_league_client import MATCHWEEKS, PremierLeagueClient
from epl.evaluation import tuning
from epl.services import pipeline
from tests import factories
from tests.conftest import ADMIN_TOKEN


def first_unplayed_kickoff(settings: Settings) -> datetime:
    with session_scope(settings.database_url) as session:
        frame = match_frame(session)
    kickoff = frame.loc[frame["status"] == "scheduled", "kickoff"].min()
    return pd.Timestamp(kickoff).tz_localize("Europe/London").tz_convert(UTC).to_pydatetime()


def test_predictions_are_logged_once_per_model_version(seeded: Settings) -> None:
    now = first_unplayed_kickoff(seeded) - timedelta(days=1)

    with session_scope(seeded.database_url) as session:
        first = pipeline.log_predictions(session, seeded, days=7, now=now)
        again = pipeline.log_predictions(session, seeded, days=7, now=now)

    assert first > 0
    assert again == 0  # idempotent for the same model version


def test_live_performance_scores_pre_kickoff_forecasts(seeded: Settings) -> None:
    now = first_unplayed_kickoff(seeded) - timedelta(days=1)
    with session_scope(seeded.database_url) as session:
        logged = pipeline.log_predictions(session, seeded, days=7, now=now)
        assert pipeline.live_performance(session, seeded.current_season)["pending"] == logged

    # The results come in: rewrite the snapshot fully played and reload.
    factories.write_snapshots(factories.match_frame(), seeded)
    pipeline.load(seeded)
    with session_scope(seeded.database_url) as session:
        result = pipeline.live_performance(session, seeded.current_season)

    assert result["n"] == logged
    assert 0 <= result["metrics"]["rps"]["mean"] <= 1


def test_backtest_report_is_written_and_served(seeded: Settings, client: TestClient) -> None:
    report = pipeline.run_backtest(seeded, [2024], models=["prior", "dixon_coles"])

    assert report["seasons"]["2024"]["ranking"]
    assert pipeline.report_path(seeded).exists()
    served = client.get("/api/v1/evaluation/backtest").json()
    assert served["seasons"]["2024"]["best"] in {"prior", "dixon_coles"}


def test_admin_refresh_job_runs_to_completion(
    client: TestClient, seeded: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "extract", lambda *a, **k: [])  # no network in tests
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    accepted = client.post("/api/v1/admin/refresh", headers=headers)
    job = client.get(f"/api/v1/admin/jobs/{accepted.json()['job_id']}", headers=headers).json()

    assert accepted.status_code == 202
    assert job["status"] == "succeeded", job
    assert client.get("/api/v1/admin/jobs/nope", headers=headers).status_code == 404


def test_tuning_picks_the_best_trial(
    frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(
        tuning,
        "GRIDS",
        {
            "elo": {"k": [20.0, 40.0]},
            "dixon_coles": {"prior_sd": [0.2, 0.5]},
            "logistic_regression": {"C": [0.05]},
            "gradient_boosting": {"max_iter": [25]},
        },
    )
    result = tuning.tune(frame, 2024)
    tuning.save(result, tmp_path / "hyperparameters.json")

    for group, trials in result["trials"].items():
        best = min(trials, key=lambda t: t["rps"])["params"]
        assert {k: result["best"][group][k] for k in best} == best
    assert json.loads((tmp_path / "hyperparameters.json").read_text())["dixon_coles"]["prior_sd"] in (
        0.2,
        0.5,
    )
    assert set(tuning.best_trial_table(result)["group"]) == set(result["trials"])


@pytest.fixture
def cli_env(seeded: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(config, "get_settings", lambda: seeded)
    monkeypatch.setattr("epl.cli.get_settings", lambda: seeded)
    return seeded


def test_cli_commands(cli_env: Settings, tmp_path: Any) -> None:
    runner = CliRunner()

    assert runner.invoke(cli, ["--help"]).exit_code == 0
    assert "Loaded 6 teams" in runner.invoke(cli, ["load"]).output
    assert "dixon_coles" in runner.invoke(cli, ["train"]).output
    simulated = runner.invoke(cli, ["simulate", "--season", "2024", "--runs", "1000"])
    assert simulated.exit_code == 0 and "p_title" in simulated.output
    schema = tmp_path / "openapi.json"
    assert runner.invoke(cli, ["openapi", "--out", str(schema)]).exit_code == 0
    assert "/api/v1/meta" in json.loads(schema.read_text())["paths"]
    assert "up to date" in runner.invoke(cli, ["db", "upgrade"]).output


def test_json_logs_carry_the_request_id_and_extra_fields() -> None:
    record = logging.LogRecord("epl.api", logging.INFO, __file__, 1, "GET %s", ("/x",), None)
    record.duration_ms = 1.5
    token = request_id.set("req-1")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id.reset(token)

    assert payload["msg"] == "GET /x"
    assert payload["request_id"] == "req-1"
    assert payload["duration_ms"] == 1.5


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


class FakeSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def get(self, url: str | bytes, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls.append(str(url))
        if str(url).endswith("/stats"):
            return FakeResponse(
                [{"side": "Home", "stats": {"totalScoringAtt": 9}}, {"side": "Away", "stats": {}}]
            )
        week = kwargs["params"]["matchweek"]
        match = {
            "matchId": str(week),
            "season": "2026",
            "matchWeek": week,
            "kickoff": "2026-08-21 20:00:00",
            "homeTeam": {"name": "Arsenal"},
            "awayTeam": {"name": "Chelsea"},
        }
        return FakeResponse({"data": [match]})


def test_client_walks_every_matchweek_and_parses_stats(settings: Settings) -> None:
    session = FakeSession()
    client = PremierLeagueClient(settings.model_copy(update={"api_request_delay_seconds": 0}), session)

    with client:
        matches = list(client.matches(2026))
        stats = client.match_stats(1)

    assert len(matches) == MATCHWEEKS
    assert matches[0].period == "PreMatch"
    assert stats == {"home": {"totalScoringAtt": 9}, "away": {}}


def test_default_session_retries_transient_errors(settings: Settings) -> None:
    adapter = PremierLeagueClient(settings).session.get_adapter("https://example.com")

    assert adapter.max_retries.total == 5  # type: ignore[attr-defined]
    assert 503 in adapter.max_retries.status_forcelist  # type: ignore[attr-defined]
