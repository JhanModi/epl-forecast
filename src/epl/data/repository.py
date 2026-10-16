"""Read side: query the database into the DataFrames the modelling code works on.

Models never touch SQL; they receive a *match frame* with one row per match:
match_id, season, matchweek, kickoff, status, home_team, away_team,
home_goals, away_goals and the typed statistics prefixed home_/away_.
"""

from collections.abc import Sequence

import pandas as pd
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, aliased

from epl.data.load import STAT_COLUMNS
from epl.data.schema import Match, Team, TeamMatchStats

STAT_NAMES = list(STAT_COLUMNS)


def _match_query(seasons: Sequence[int] | None) -> Select:
    home, away = aliased(Team), aliased(Team)
    home_stats, away_stats = aliased(TeamMatchStats), aliased(TeamMatchStats)
    query = (
        select(
            Match.id.label("match_id"),
            Match.season,
            Match.matchweek,
            Match.kickoff,
            Match.status,
            home.name.label("home_team"),
            away.name.label("away_team"),
            Match.home_score.label("home_goals"),
            Match.away_score.label("away_goals"),
            Match.home_red_cards,
            Match.away_red_cards,
            *[getattr(home_stats, s).label(f"home_{s}") for s in STAT_NAMES],
            *[getattr(away_stats, s).label(f"away_{s}") for s in STAT_NAMES],
        )
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .outerjoin(home_stats, (home_stats.match_id == Match.id) & (home_stats.side == "home"))
        .outerjoin(away_stats, (away_stats.match_id == Match.id) & (away_stats.side == "away"))
        .order_by(Match.kickoff, Match.id)
    )
    if seasons is not None:
        query = query.where(Match.season.in_(list(seasons)))
    return query


def match_frame(session: Session, seasons: Sequence[int] | None = None) -> pd.DataFrame:
    """All fixtures (played and unplayed) for the given seasons, oldest first."""
    rows = session.execute(_match_query(seasons)).mappings().all()
    frame = pd.DataFrame(rows, columns=list(_match_query(None).selected_columns.keys()))
    frame["kickoff"] = pd.to_datetime(frame["kickoff"])
    for column in ("home_goals", "away_goals"):
        frame[column] = frame[column].astype("Int64")
    return frame


def played(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["status"] == "finished"].reset_index(drop=True)


def teams(session: Session) -> list[Team]:
    return list(session.scalars(select(Team).order_by(Team.name)))


def seasons(session: Session) -> list[int]:
    return list(session.scalars(select(Match.season).distinct().order_by(Match.season)))
