import os

import pandas as pd
import pytest
from sqlalchemy import func, select, text

from epl.core.config import Settings
from epl.data import migrate
from epl.data.db import get_engine, session_scope
from epl.data.load import load_seasons
from epl.data.repository import match_frame
from epl.data.schema import Match, Team, TeamMatchStats
from epl.data.snapshots import stats_path
from epl.data.sources.premier_league import extract_season
from epl.data.sources.premier_league_client import parse_match
from epl.data.validate import DataValidationError, validate_matches
from tests import factories

API_MATCH = {
    "matchId": "2645195",
    "season": "2026",
    "matchWeek": 1,
    "kickoff": "2026-08-21 20:00:00",
    "period": "FullTime",
    "ground": "Emirates Stadium, London",
    "attendance": 60098,
    "homeTeam": {
        "name": "Arsenal",
        "shortName": "Arsenal",
        "abbr": "ARS",
        "score": 3,
        "halfTimeScore": 2,
        "redCards": 0,
    },
    "awayTeam": {
        "name": "Coventry City",
        "shortName": "Coventry",
        "abbr": "COV",
        "score": 0,
        "halfTimeScore": 0,
        "redCards": 0,
    },
}


def load(settings: Settings) -> None:
    migrate.upgrade(settings.database_url)
    with session_scope(settings.database_url) as session:
        load_seasons(session, settings, settings.seasons)


def counts(settings: Settings) -> tuple[int, int, int]:
    with session_scope(settings.database_url) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(t)) for t in (Team, Match, TeamMatchStats)
        )  # type: ignore[return-value]


def test_parse_match_from_api_payload() -> None:
    match = parse_match(API_MATCH)

    assert (match.match_id, match.season, match.matchweek) == (2645195, 2026, 1)
    assert (match.home.abbr, match.home.score, match.away.score) == ("ARS", 3, 0)


def test_load_round_trips_through_the_database(settings: Settings, frame: pd.DataFrame) -> None:
    factories.write_snapshots(frame, settings)
    load(settings)

    with session_scope(settings.database_url) as session:
        loaded = match_frame(session)

    assert len(loaded) == len(frame)
    pd.testing.assert_series_equal(
        loaded["home_goals"].reset_index(drop=True),
        frame["home_goals"].reset_index(drop=True),
        check_names=False,
    )
    assert loaded["home_shots"].notna().all()


def test_load_is_idempotent(settings: Settings, frame: pd.DataFrame) -> None:
    factories.write_snapshots(frame, settings)
    load(settings)
    first = counts(settings)
    load(settings)

    assert counts(settings) == first == (6, len(frame), 2 * len(frame))


def test_unplayed_fixtures_are_loaded_without_scores(settings: Settings, frame: pd.DataFrame) -> None:
    factories.write_snapshots(frame, settings, unplayed_from=frame["kickoff"].iloc[-10])
    load(settings)

    with session_scope(settings.database_url) as session:
        loaded = match_frame(session)

    scheduled = loaded[loaded["status"] == "scheduled"]
    assert len(scheduled) == 10
    assert scheduled["home_goals"].isna().all()


def test_missing_statistics_mean_zero(settings: Settings, frame: pd.DataFrame) -> None:
    factories.write_snapshots(frame, settings)
    path = stats_path(settings, 2023)
    stats = pd.read_csv(path)
    stats.loc[0, "home_ontargetScoringAtt"] = None  # the API omits zero-valued stats
    stats.to_csv(path, index=False)
    load(settings)

    with session_scope(settings.database_url) as session:
        row = session.get(TeamMatchStats, (int(stats.loc[0, "match_id"]), "home"))
        assert row is not None and row.shots_on_target == 0
        assert "ontargetScoringAtt" not in row.raw


def test_validation_rejects_bad_data(frame: pd.DataFrame) -> None:
    matches = frame.rename(columns={"home_goals": "home_score", "away_goals": "away_score"})
    bad = pd.concat([matches, matches.head(1)], ignore_index=True)  # duplicate match id
    bad.loc[1, "away_team"] = bad.loc[1, "home_team"]  # a team playing itself

    result = validate_matches(bad, teams_per_season=6)

    assert any("duplicate match ids" in e for e in result.errors)
    assert any("playing itself" in e for e in result.errors)
    with pytest.raises(DataValidationError):
        result.raise_for_errors()


def test_invalid_snapshot_is_not_loaded(settings: Settings, frame: pd.DataFrame) -> None:
    factories.write_snapshots(frame, settings)
    settings_wrong_size = settings.model_copy(update={"teams_per_season": 20})
    migrate.upgrade(settings.database_url)

    with pytest.raises(DataValidationError), session_scope(settings.database_url) as session:
        load_seasons(session, settings_wrong_size, settings.seasons)
    assert counts(settings) == (0, 0, 0)  # the transaction rolled back


class FakeClient:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.stats_requests: list[int] = []

    def matches(self, season: int):  # type: ignore[no-untyped-def]
        for row in self.frame[self.frame["season"] == season].itertuples():
            payload = API_MATCH | {
                "matchId": str(row.match_id),
                "season": str(season),
                "matchWeek": row.matchweek,
                "kickoff": str(row.kickoff),
                "homeTeam": API_MATCH["homeTeam"] | {"name": row.home_team, "score": int(row.home_goals)},
                "awayTeam": API_MATCH["awayTeam"] | {"name": row.away_team, "score": int(row.away_goals)},
            }
            yield parse_match(payload)

    def match_stats(self, match_id: int) -> dict[str, dict[str, float]]:
        self.stats_requests.append(match_id)
        return {
            "home": {"totalScoringAtt": 10, "possessionPercentage": 55},
            "away": {"totalScoringAtt": 5, "possessionPercentage": 45},
        }


def test_extract_only_downloads_new_statistics(settings: Settings, frame: pd.DataFrame) -> None:
    client = FakeClient(frame)

    first = extract_season(2023, settings, client)  # type: ignore[arg-type]
    second = extract_season(2023, settings, client)  # type: ignore[arg-type]

    assert first.new_stats == (frame["season"] == 2023).sum()
    assert second.new_stats == 0
    assert len(client.stats_requests) == first.new_stats


@pytest.mark.postgres
@pytest.mark.skipif("EPL_TEST_DATABASE_URL" not in os.environ, reason="needs a PostgreSQL URL")
def test_load_on_postgres(settings: Settings, frame: pd.DataFrame) -> None:
    url = os.environ["EPL_TEST_DATABASE_URL"]
    with get_engine(url).begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    pg = settings.model_copy(update={"database_url": url})
    factories.write_snapshots(frame, pg)
    load(pg)
    load(pg)

    assert counts(pg) == (6, len(frame), 2 * len(frame))
