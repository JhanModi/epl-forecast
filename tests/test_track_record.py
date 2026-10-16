"""The forecast track record: UTC kickoffs, the latest-before-kickoff rule, paging and scoring.

Expected UTC values are hard-coded rather than computed with the code under test, so a shared
mistake in the conversion cannot make a test pass.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from epl.core.config import Settings
from epl.core.timezones import uk_to_utc
from epl.data import migrate
from epl.data.db import get_engine, session_scope
from epl.data.schema import Match, Prediction
from epl.data.validate import validate_kickoffs
from epl.evaluation import metrics
from epl.models.base import outcome_index
from epl.services import pipeline, track_record
from epl.services.track_record import _utc
from tests import factories

# A database still at revision 0002, written as raw SQL: the ORM classes describe the newest schema.
# One BST and one GMT kickoff, with forecasts and statistics that reference them.
AT_0002 = [
    "INSERT INTO teams (id, name, short_name, abbr) VALUES (1, 'A', 'A', 'AAA'), (2, 'B', 'B', 'BBB')",
    "INSERT INTO matches (id, season, matchweek, kickoff, status, home_team_id, away_team_id, updated_at) "
    "VALUES (1, 2026, 1, '2026-08-15 15:00:00.000000', 'finished', 1, 2, '2026-08-01 00:00:00.000000'), "
    "(2, 2026, 20, '2027-01-09 15:00:00.000000', 'scheduled', 2, 1, '2026-08-01 00:00:00.000000')",
    "INSERT INTO predictions (match_id, model_name, model_version, p_home, p_draw, p_away, created_at) "
    "VALUES (1, 'dixon_coles', 'v1', 0.5, 0.3, 0.2, '2026-08-14 09:00:00.000000'), "
    "(2, 'dixon_coles', 'v1', 0.4, 0.3, 0.3, '2027-01-08 09:00:00.000000')",
    "INSERT INTO team_match_stats (match_id, side, team_id, shots, shots_on_target, shots_in_box, "
    "big_chances, touches_in_box, corners, passes, possession, raw) "
    "VALUES (1, 'home', 1, 10, 4, 6, 2, 20, 5, 400, 55.0, '{}'), "
    "(1, 'away', 2, 6, 2, 3, 1, 12, 3, 330, 45.0, '{}')",
]


# --------------------------------------------------------------------------- UK local time -> UTC
@pytest.mark.parametrize(
    ("local", "utc"),
    [
        ("2027-01-09 15:00", "2027-01-09 15:00"),  # winter: GMT = UTC
        ("2026-08-15 15:00", "2026-08-15 14:00"),  # summer: BST = UTC+1
        ("2027-03-28 00:30", "2027-03-28 00:30"),  # the clocks go forward at 01:00 GMT ...
        ("2027-03-28 14:00", "2027-03-28 13:00"),  # ... so that afternoon is already BST
        ("2026-10-25 00:30", "2026-10-24 23:30"),  # the clocks go back at 02:00 BST ...
        ("2026-10-25 14:00", "2026-10-25 14:00"),  # ... so that afternoon is GMT again
    ],
)
def test_uk_kickoffs_convert_to_utc_with_the_clock_rules(local: str, utc: str) -> None:
    converted = uk_to_utc(pd.Series([pd.Timestamp(local)]))[0]
    assert converted == pd.Timestamp(utc, tz="UTC")


@pytest.mark.parametrize(
    "local",
    ["2027-03-28 01:30", "2026-10-25 01:30"],  # skipped (spring forward) / repeated (fall back)
)
def test_nonexistent_and_ambiguous_kickoffs_are_not_guessed(local: str) -> None:
    assert pd.isna(uk_to_utc(pd.Series([pd.Timestamp(local)]))[0])


def test_validation_lists_every_unconvertible_kickoff() -> None:
    matches = pd.DataFrame(
        {
            "match_id": [1, 2, 3],
            "date": pd.to_datetime(["2027-03-28 01:30", "2026-08-15 15:00", "2026-10-25 01:15"]),
        }
    )
    (error,) = validate_kickoffs(matches).errors
    assert "1 at 2027-03-28 01:30" in error and "3 at 2026-10-25 01:15" in error
    assert "2 at" not in error


def test_load_stores_the_utc_instant_and_reconverts_a_corrected_kickoff(settings: Settings) -> None:
    frame = factories.match_frame()
    first = frame.index[0]
    frame.loc[first, "kickoff"] = pd.Timestamp("2023-08-12 15:00")  # BST
    factories.write_snapshots(frame, settings)
    pipeline.load(settings)
    match_id = int(frame.loc[first, "match_id"])
    with get_engine(settings.database_url).connect() as conn:
        stored = conn.exec_driver_sql(f"SELECT kickoff, kickoff_utc FROM matches WHERE id = {match_id}").one()
    assert tuple(stored) == ("2023-08-12 15:00:00.000000", "2023-08-12 14:00:00.000000")

    frame.loc[first, "kickoff"] = pd.Timestamp("2023-12-02 12:30")  # rescheduled into GMT
    factories.write_snapshots(frame, settings)
    pipeline.load(settings)
    with session_scope(settings.database_url) as session:
        match = session.get(Match, match_id)
        assert match is not None
        assert match.kickoff_utc.replace(tzinfo=UTC) == datetime(2023, 12, 2, 12, 30, tzinfo=UTC)


def test_upgrade_backfills_utc_and_keeps_every_referencing_row(tmp_path: Path) -> None:
    """Making the column NOT NULL rebuilds `matches` on SQLite; that must not cascade into other tables."""
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    migrate.upgrade(url, "0002")
    with get_engine(url).begin() as conn:
        for statement in AT_0002:
            conn.exec_driver_sql(statement)

    migrate.upgrade(url)

    with get_engine(url).connect() as conn:
        assert conn.exec_driver_sql("SELECT id, kickoff_utc FROM matches ORDER BY id").all() == [
            (1, "2026-08-15 14:00:00.000000"),  # BST
            (2, "2027-01-09 15:00:00.000000"),  # GMT
        ]
        assert conn.exec_driver_sql("SELECT count(*) FROM predictions").scalar() == 2
        assert conn.exec_driver_sql("SELECT count(*) FROM team_match_stats").scalar() == 2
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1  # enforcement is back on for the app
        with pytest.raises(IntegrityError):  # the rebuilt table kept its CHECK constraints
            conn.exec_driver_sql("UPDATE matches SET status = 'abandoned' WHERE id = 1")
        conn.rollback()
        with pytest.raises(IntegrityError):  # ... and its foreign keys
            conn.exec_driver_sql("UPDATE matches SET home_team_id = 99 WHERE id = 1")
        conn.rollback()
        assert conn.execute(select(Match.kickoff_utc).where(Match.id == 1)).scalar_one() is not None


# --------------------------------------------------------------------------- latest forecast before kickoff
def add_forecast(
    session: Session,
    match_id: int,
    version: str,
    created_at: datetime,
    probs: tuple[float, float, float] = (0.5, 0.3, 0.2),
) -> int:
    """Inserted directly: `log_predictions` never writes a post-kickoff forecast, the case under test."""
    row = Prediction(
        match_id=match_id,
        model_name="dixon_coles",
        model_version=version,
        p_home=probs[0],
        p_draw=probs[1],
        p_away=probs[2],
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    return row.id


def season_matches(session: Session, season: int, status: str) -> list[Match]:
    return list(
        session.scalars(
            select(Match).where(Match.season == season, Match.status == status).order_by(Match.kickoff_utc)
        )
    )


def test_a_forecast_after_kickoff_never_hides_the_earlier_one(seeded: Settings) -> None:
    """The regression that matters most: two plausible bugs make this fail.

    - Ranking before filtering returns no row for the match (the late forecast takes rank 1, then is dropped).
    - Comparing UTC `created_at` with the naive UK-local `kickoff` accepts the late forecast (14:30 < 15:00).
    """
    with session_scope(seeded.database_url) as session:
        match = season_matches(session, seeded.current_season, "scheduled")[0]
        kickoff_utc = _utc(match.kickoff_utc)
        assert match.kickoff.hour - kickoff_utc.hour == 1, "needs a BST kickoff, where local and UTC differ"
        valid = add_forecast(session, match.id, "v1", kickoff_utc - timedelta(hours=5))
        add_forecast(session, match.id, "v2", kickoff_utc + timedelta(minutes=30))  # 30 min after kickoff

        log = track_record.prediction_log(session, seeded.current_season)

    assert [(i["match_id"], i["prediction_id"]) for i in log["items"]] == [(match.id, valid)]
    assert log["total"] == 1


def test_a_forecast_logged_exactly_at_kickoff_is_excluded(seeded: Settings) -> None:
    with session_scope(seeded.database_url) as session:
        match = season_matches(session, seeded.current_season, "scheduled")[0]
        add_forecast(session, match.id, "v1", _utc(match.kickoff_utc))
        log = track_record.prediction_log(session, seeded.current_season)
    assert (log["items"], log["total"]) == ([], 0)


def test_the_latest_pre_kickoff_version_wins_and_equal_times_go_to_the_later_row(seeded: Settings) -> None:
    with session_scope(seeded.database_url) as session:
        first, second = season_matches(session, seeded.current_season, "scheduled")[:2]
        add_forecast(session, first.id, "v1", _utc(first.kickoff_utc) - timedelta(days=2))
        newer = add_forecast(session, first.id, "v2", _utc(first.kickoff_utc) - timedelta(hours=1))
        same_time = _utc(second.kickoff_utc) - timedelta(hours=3)
        add_forecast(session, second.id, "v1", same_time)
        later_row = add_forecast(session, second.id, "v2", same_time)
        log = track_record.prediction_log(session, seeded.current_season)
    assert {i["match_id"]: i["prediction_id"] for i in log["items"]} == {
        first.id: newer,
        second.id: later_row,
    }


def log_every_match(session: Session, season: int) -> dict[int, int]:
    """One forecast a day before every kickoff of the season: match id -> prediction id."""
    matches = session.scalars(select(Match).where(Match.season == season)).all()
    return {m.id: add_forecast(session, m.id, "v1", _utc(m.kickoff_utc) - timedelta(days=1)) for m in matches}


def test_status_filters_split_the_log_and_the_totals_add_up(seeded: Settings) -> None:
    season = seeded.current_season
    with session_scope(seeded.database_url) as session:
        logged = log_every_match(session, season)
        n_finished = len(season_matches(session, season, "finished"))
        both = track_record.prediction_log(session, season, limit=100)
        finished = track_record.prediction_log(session, season, status="finished", limit=100)
        pending = track_record.prediction_log(session, season, status="pending", limit=100)

    assert both["total"] == len(logged) == finished["total"] + pending["total"]
    assert 0 < finished["total"] == n_finished < both["total"]
    assert {i["status"] for i in finished["items"]} == {"finished"}
    assert all(i["rps"] is not None and i["home_goals"] is not None for i in finished["items"])
    assert "finished" not in {i["status"] for i in pending["items"]}
    assert all(i["rps"] is None and i["home_goals"] is None for i in pending["items"])  # null, not zero


def test_pages_are_disjoint_complete_newest_first_and_share_one_total(seeded: Settings) -> None:
    season = seeded.current_season
    with session_scope(seeded.database_url) as session:
        # Six matches share one kickoff, as on a Saturday afternoon: ordering must fall back to match id.
        tied = sorted(session.scalars(select(Match.id).where(Match.season == season)).all())[:6]
        session.execute(
            update(Match)
            .where(Match.id.in_(tied))
            .values(kickoff=datetime(2024, 9, 14, 15), kickoff_utc=datetime(2024, 9, 14, 14, tzinfo=UTC))
        )
        logged = log_every_match(session, season)
        pages = [
            track_record.prediction_log(session, season, limit=4, offset=o) for o in range(0, len(logged), 4)
        ]
        everything = track_record.prediction_log(session, season, limit=100)

    paged = [i["match_id"] for page in pages for i in page["items"]]
    assert paged == [i["match_id"] for i in everything["items"]]  # same order: no overlap, no gap
    assert sorted(paged) == sorted(logged)
    assert {page["total"] for page in pages} == {len(logged)}
    keys = [(i["kickoff_utc"], i["match_id"]) for i in everything["items"]]
    assert keys == sorted(keys, reverse=True)
    assert [m for m in paged if m in tied] == sorted(tied, reverse=True)


def test_a_page_past_the_end_is_empty_but_still_reports_the_total(seeded: Settings) -> None:
    with session_scope(seeded.database_url) as session:
        logged = log_every_match(session, seeded.current_season)
        past_the_end = track_record.prediction_log(session, seeded.current_season, offset=len(logged))
        no_forecasts = track_record.prediction_log(session, 2023)
    assert (past_the_end["items"], past_the_end["total"]) == ([], len(logged))
    assert (no_forecasts["items"], no_forecasts["total"]) == ([], 0)


def test_finished_rows_score_like_metrics_rps_and_average_to_the_live_figure(seeded: Settings) -> None:
    """A2, with expected scores worked out by hand, independently of both implementations."""
    season = seeded.current_season
    chosen = [(0.6, 0.25, 0.15), (0.2, 0.3, 0.5), (0.34, 0.33, 0.33)]
    with session_scope(seeded.database_url) as session:
        played = season_matches(session, season, "finished")[:3]
        for match, probs in zip(played, chosen, strict=True):
            kickoff = _utc(match.kickoff_utc)
            add_forecast(
                session, match.id, "v1", kickoff - timedelta(days=3), (0.9, 0.05, 0.05)
            )  # superseded
            if match is played[-1]:  # an equal-time tie: both endpoints must pick the later row
                add_forecast(session, match.id, "v2", kickoff - timedelta(hours=2), (0.05, 0.05, 0.9))
                add_forecast(session, match.id, "v3", kickoff - timedelta(hours=2), probs)
            else:
                add_forecast(session, match.id, "v2", kickoff - timedelta(hours=2), probs)
        scores = {m.id: (m.home_score, m.away_score) for m in played}
        log = track_record.prediction_log(session, season, status="finished")
        live = pipeline.live_performance(session, season)

    rows = {i["match_id"]: i for i in log["items"]}
    for match, (p_home, p_draw, p_away) in zip(played, chosen, strict=True):
        home, away = scores[match.id]
        # RPS over ordered outcomes (home, draw, away): mean squared error of cumulative probabilities.
        home_win, home_win_or_draw = float(home > away), float(home >= away)
        by_hand = ((p_home - home_win) ** 2 + (p_home + p_draw - home_win_or_draw) ** 2) / 2
        outcome = outcome_index(pd.DataFrame({"home_goals": [home], "away_goals": [away]}))
        row = rows[match.id]
        assert (row["home_goals"], row["away_goals"]) == (home, away)
        assert (row["p_home"], row["p_draw"], row["p_away"]) == (p_home, p_draw, p_away)
        assert row["rps"] == pytest.approx(by_hand)
        assert row["rps"] == pytest.approx(metrics.rps(np.array([[p_home, p_draw, p_away]]), outcome)[0])
    assert live["n"] == log["total"] == 3
    assert np.mean([i["rps"] for i in log["items"]]) == pytest.approx(live["metrics"]["rps"]["mean"])


# --------------------------------------------------------------------------- HTTP contract
def test_the_endpoint_serves_a_typed_page_of_the_current_season(seeded: Settings, client: TestClient) -> None:
    with session_scope(seeded.database_url) as session:
        match = season_matches(session, seeded.current_season, "scheduled")[0]
        prediction_id = add_forecast(session, match.id, "v1", _utc(match.kickoff_utc) - timedelta(hours=5))
        local_kickoff = match.kickoff

    response = client.get("/api/v1/predictions/log")

    assert response.status_code == 200
    body = response.json()
    assert (body["season"], body["total"], body["limit"], body["offset"]) == (seeded.current_season, 1, 20, 0)
    (item,) = body["items"]
    assert (item["prediction_id"], item["match_id"], item["model_version"]) == (prediction_id, match.id, "v1")
    assert item["home_goals"] is None and item["rps"] is None
    assert item["kickoff"] == local_kickoff.isoformat()  # UK local time, like every other kickoff in the API
    assert item["kickoff_utc"].endswith("Z") and item["created_at"].endswith("Z")
    assert item["home_team"] and item["away_team"]


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "limit=ten", "offset=-1", "offset=10001", "status=live", "season=1999"]
)
def test_the_endpoint_rejects_invalid_parameters(client: TestClient, query: str) -> None:
    response = client.get(f"/api/v1/predictions/log?{query}")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


# --------------------------------------------------------------------------- PostgreSQL
def postgres(settings: Settings) -> Settings:
    url = os.environ["EPL_TEST_DATABASE_URL"]
    with get_engine(url).begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    return settings.model_copy(update={"database_url": url})


@pytest.mark.postgres
@pytest.mark.skipif("EPL_TEST_DATABASE_URL" not in os.environ, reason="needs a PostgreSQL URL")
def test_upgrade_backfills_utc_on_postgres(settings: Settings) -> None:
    pg = postgres(settings)
    migrate.upgrade(pg.database_url, "0002")
    with get_engine(pg.database_url).begin() as conn:
        for statement in AT_0002:
            conn.execute(text(statement))
    migrate.upgrade(pg.database_url)
    with get_engine(pg.database_url).connect() as conn:
        utc = (
            conn.execute(text("SELECT kickoff_utc AT TIME ZONE 'UTC' FROM matches ORDER BY id"))
            .scalars()
            .all()
        )
        assert utc == [datetime(2026, 8, 15, 14), datetime(2027, 1, 9, 15)]
        assert conn.execute(text("SELECT count(*) FROM predictions")).scalar() == 2


@pytest.mark.postgres
@pytest.mark.skipif("EPL_TEST_DATABASE_URL" not in os.environ, reason="needs a PostgreSQL URL")
def test_selection_on_postgres_does_not_depend_on_the_session_time_zone(settings: Settings) -> None:
    """Comparing timestamptz with the naive kickoff would flip with the session zone; UTC-to-UTC cannot."""
    pg = postgres(settings)
    data = factories.match_frame()
    second = data[data["season"] == 2024]
    factories.write_snapshots(data, pg, unplayed_from=second["kickoff"].iloc[len(second) // 2])
    pipeline.load(pg)
    with session_scope(pg.database_url) as session:
        match = season_matches(session, pg.current_season, "scheduled")[0]
        kickoff = _utc(match.kickoff_utc)
        valid = add_forecast(session, match.id, "v1", kickoff - timedelta(hours=5))
        add_forecast(session, match.id, "v2", kickoff + timedelta(minutes=30))
        chosen = set()
        for zone in ("UTC", "Europe/London", "America/New_York"):
            session.execute(text(f"SET TIME ZONE '{zone}'"))
            log = track_record.prediction_log(session, pg.current_season)
            chosen.add(tuple((i["prediction_id"], i["kickoff_utc"]) for i in log["items"]))
    assert chosen == {((valid, kickoff),)}
