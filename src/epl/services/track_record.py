"""Forecast track record: for each match, the latest forecast logged before kickoff, and how it scored.

The rule is the one `pipeline.live_performance` applies (any model version; the latest forecast
created strictly before kickoff wins; ties go to the later row), so averaging the finished rows
reproduces the live RPS. Selection, counting and paging run as one SQL statement: the database does
the work, and `total` and the page are read from the same snapshot.
"""

from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
from sqlalchemy import Select, func, select, true
from sqlalchemy.orm import Session, aliased

from epl.data.schema import Match, Prediction, Team
from epl.evaluation import metrics
from epl.models.base import outcome_index

Status = Literal["finished", "pending"]


def latest_pre_kickoff(season: int, status: Status | None = None) -> Select[Any]:
    """One row per match: its latest forecast created strictly before kickoff.

    The kickoff filter runs before the ranking. Ranking first would let a post-kickoff forecast take
    rank 1 and then be filtered out, hiding the valid earlier forecast for that match.
    """
    ranked = (
        select(
            Match.id.label("match_id"),
            Match.season,
            Match.matchweek,
            Match.kickoff,
            Match.kickoff_utc,
            Match.status,
            Match.home_team_id,
            Match.away_team_id,
            Match.home_score.label("home_goals"),
            Match.away_score.label("away_goals"),
            Prediction.id.label("prediction_id"),
            Prediction.model_version,
            Prediction.created_at,
            Prediction.p_home,
            Prediction.p_draw,
            Prediction.p_away,
            func.row_number()
            .over(
                partition_by=Prediction.match_id,
                order_by=(Prediction.created_at.desc(), Prediction.id.desc()),
            )
            .label("rank"),
        )
        .select_from(Prediction)
        .join(Match, Match.id == Prediction.match_id)
        # Both sides are UTC: comparing with the naive UK-local `kickoff` is an hour off during BST.
        .where(Match.season == season, Prediction.created_at < Match.kickoff_utc)
    )
    if status == "finished":
        ranked = ranked.where(Match.status == "finished")
    elif status == "pending":
        ranked = ranked.where(Match.status != "finished")  # scheduled, live or postponed
    candidates = ranked.subquery("ranked")
    return select(*[c for c in candidates.c if c.name != "rank"]).where(candidates.c.rank == 1)


def _utc(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored timestamp is UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def prediction_log(
    session: Session, season: int, status: Status | None = None, limit: int = 20, offset: int = 0
) -> dict[str, Any]:
    """A page of the track record, newest kickoff first, with the total across all pages."""
    # MATERIALIZED: rank once and reuse it for the count and the page. Without the hint SQLite inlines the
    # CTE into both and runs the window function twice (EXPLAIN QUERY PLAN; 2x the time at 100x the log).
    selected = latest_pre_kickoff(season, status).cte("selected").prefix_with("MATERIALIZED")
    total = select(func.count().label("total")).select_from(selected).subquery("total")
    newest_first = (selected.c.kickoff_utc.desc(), selected.c.match_id.desc())  # match id breaks kickoff ties
    page = select(selected).order_by(*newest_first).limit(limit).offset(offset).subquery("page")
    home, away = aliased(Team), aliased(Team)
    statement = (
        select(total.c.total, page, home.name.label("home_team"), away.name.label("away_team"))
        .select_from(total)
        # LEFT JOIN on a constant: an empty or out-of-range page still returns one row carrying `total`.
        .outerjoin(page, true())
        .outerjoin(home, home.id == page.c.home_team_id)
        .outerjoin(away, away.id == page.c.away_team_id)
        # The subquery's ORDER BY chooses the page but does not order the outer result, so repeat it.
        .order_by(page.c.kickoff_utc.desc(), page.c.match_id.desc())
    )
    rows = session.execute(statement).all()
    items = [_item(row._asdict()) for row in rows if row.prediction_id is not None]  # skip the empty-page row

    finished = [item for item in items if item["status"] == "finished"]
    if finished:
        probs = np.array([[i["p_home"], i["p_draw"], i["p_away"]] for i in finished])
        scores = metrics.rps(probs, outcome_index(pd.DataFrame(finished)))
        for item, score in zip(finished, scores, strict=True):
            item["rps"] = float(score)
    return {"season": season, "items": items, "total": rows[0].total, "limit": limit, "offset": offset}


def _item(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("total", "home_team_id", "away_team_id"):
        del row[key]
    row["kickoff_utc"] = _utc(row["kickoff_utc"])
    row["created_at"] = _utc(row["created_at"])
    row["rps"] = None  # filled in for finished matches; null (not zero) until the result is known
    return row
