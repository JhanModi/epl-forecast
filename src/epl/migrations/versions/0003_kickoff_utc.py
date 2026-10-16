"""kickoff in UTC

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11 16:00:00.000000

`matches.kickoff` is naive UK local time. Forecast timestamps are UTC, and comparing the two in SQL
gives session-dependent answers on PostgreSQL and is wrong by an hour during BST on SQLite, so the
same instant is stored in UTC. Existing rows are backfilled with the Europe/London rules (not a fixed
offset); a kickoff that is ambiguous or nonexistent in UK time stops the migration.

Self-contained on purpose: it uses its own table stub, not the application's ORM classes, so later
schema changes cannot alter what this revision does.
"""

from collections.abc import Sequence

import pandas as pd
import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

matches = sa.table(
    "matches",
    sa.column("id", sa.Integer()),
    sa.column("kickoff", sa.DateTime()),
    sa.column("kickoff_utc", sa.DateTime(timezone=True)),
)


def _backfill() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(matches.c.id, matches.c.kickoff)).all()
    if not rows:
        return
    local = pd.Series([kickoff for _, kickoff in rows], dtype="datetime64[us]")
    utc = local.dt.tz_localize("Europe/London", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    if bad := [f"{match_id} at {kickoff}" for (match_id, kickoff), u in zip(rows, utc, strict=True) if pd.isna(u)]:
        raise RuntimeError(f"Kickoffs that are ambiguous or nonexistent in UK time; correct and reload: {bad}")
    bind.execute(
        matches.update().where(matches.c.id == sa.bindparam("match_id")).values(kickoff_utc=sa.bindparam("utc")),
        [{"match_id": match_id, "utc": u.to_pydatetime()} for (match_id, _), u in zip(rows, utc, strict=True)],
    )


def upgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kickoff_utc', sa.DateTime(timezone=True), nullable=True))
    _backfill()
    # On SQLite this rebuilds the table; env.py pauses foreign-key enforcement so the rebuild cannot
    # cascade-delete the predictions and statistics that reference these rows.
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.alter_column('kickoff_utc', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_column('kickoff_utc')
