"""Database-backed cache of season simulations, with bounded retention.

Keys change whenever the data or hyperparameters change (see
`forecasting.simulation_key`), so stale entries are never served; retention
only bounds storage. After each insert the table is trimmed to the newest
`max_rows` entries.
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from epl.data.schema import SimulationRun
from epl.services.forecasting import SimulationKey


def lookup(session: Session, key: SimulationKey) -> dict[str, Any] | None:
    row = session.scalar(
        select(SimulationRun).where(
            SimulationRun.season == key.season,
            SimulationRun.as_of_matchweek == key.as_of_matchweek,
            SimulationRun.model_version == key.model_version,
            SimulationRun.n_runs == key.n_runs,
        )
    )
    return None if row is None else row.result


def store(session: Session, key: SimulationKey, result: dict[str, Any], max_rows: int) -> None:
    try:
        with session.begin_nested():  # another worker may have stored the same key meanwhile
            session.add(SimulationRun(**key.__dict__, result=result))
    except IntegrityError:
        pass
    newest = (
        select(SimulationRun.id)
        .order_by(SimulationRun.created_at.desc(), SimulationRun.id.desc())
        .limit(max_rows)
    )
    session.execute(delete(SimulationRun).where(SimulationRun.id.not_in(newest)))


def count(session: Session) -> int:
    return len(session.scalars(select(SimulationRun.id)).all())
