"""Relational schema (SQLAlchemy 2.0 typed ORM).

Modelled statistics get typed columns; the full ~230-statistic payload from the
API is kept alongside as JSON so nothing is thrown away at load time.
Schema changes go through Alembic migrations (``src/epl/migrations``).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    short_name: Mapped[str] = mapped_column(String(32))
    abbr: Mapped[str] = mapped_column(String(3))


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="ck_matches_distinct_teams"),
        CheckConstraint("status IN ('scheduled', 'live', 'finished', 'postponed')", name="ck_matches_status"),
        Index("ix_matches_season_kickoff", "season", "kickoff"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)  # the API's matchId
    season: Mapped[int]
    matchweek: Mapped[int]
    kickoff: Mapped[datetime] = mapped_column(DateTime)  # naive UK local time, as the sources publish it
    # The same instant in UTC (set by the loader): what forecast timestamps are compared with.
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    home_ht_score: Mapped[int | None]
    away_ht_score: Mapped[int | None]
    home_red_cards: Mapped[int | None]
    away_red_cards: Mapped[int | None]
    stadium: Mapped[str | None] = mapped_column(String(128))
    attendance: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class TeamMatchStats(Base):
    """One row per team per finished match."""

    __tablename__ = "team_match_stats"
    __table_args__ = (CheckConstraint("side IN ('home', 'away')", name="ck_stats_side"),)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True)
    side: Mapped[str] = mapped_column(String(4), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    shots: Mapped[int]
    shots_on_target: Mapped[int]
    shots_in_box: Mapped[int]
    big_chances: Mapped[int]
    touches_in_box: Mapped[int]
    corners: Mapped[int]
    passes: Mapped[int]
    possession: Mapped[float] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)


class Prediction(Base):
    """Pre-kickoff predictions, logged so live accuracy can be monitored once results arrive."""

    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("match_id", "model_version", name="uq_predictions_match_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    p_home: Mapped[float] = mapped_column(Float)
    p_draw: Mapped[float] = mapped_column(Float)
    p_away: Mapped[float] = mapped_column(Float)
    exp_home_goals: Mapped[float | None] = mapped_column(Float)
    exp_away_goals: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulationRun(Base):
    """Read-through cache of Monte Carlo season simulations."""

    __tablename__ = "simulation_runs"
    __table_args__ = (
        UniqueConstraint("season", "as_of_matchweek", "model_version", "n_runs", name="uq_simulation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int]
    as_of_matchweek: Mapped[int]
    model_version: Mapped[str] = mapped_column(String(64))
    n_runs: Mapped[int]
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DataVersion(Base):
    """One row per load that changed the data. Serving processes poll the latest id to invalidate caches."""

    __tablename__ = "data_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64))
    n_matches: Mapped[int]
    n_finished: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


ACTIVE_JOB = "status IN ('queued', 'running')"


class Job(Base):
    """A background job (e.g. a refresh), persisted so its state survives restarts and is shared by workers.

    The partial unique index allows at most one *active* job of each kind across all processes. A running
    job renews `lease_expires_at`; an active job whose lease has lapsed belonged to a process that died
    and is marked failed by the next process to look.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_jobs_status"),
        Index(
            "uq_jobs_one_active_per_kind",
            "kind",
            unique=True,
            sqlite_where=text(ACTIVE_JOB),
            postgresql_where=text(ACTIVE_JOB),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    owner: Mapped[str] = mapped_column(String(128))  # host:pid of the process running it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
