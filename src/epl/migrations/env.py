"""Alembic environment: migrations run against EPL_DATABASE_URL using the ORM metadata."""

from alembic import context

from epl.core.config import get_settings
from epl.data.db import get_engine
from epl.data.schema import Base

config = context.config
url = config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with get_engine(url).connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # Batch mode rebuilds a SQLite table by copying it and dropping the original. With foreign
            # keys enforced, that DROP cascade-deletes every referencing row (all logged predictions,
            # silently), so enforcement is paused for the run (the pragma is ignored inside a
            # transaction, hence first) and integrity is checked before committing.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            # Batch mode lets ALTER TABLE work on SQLite, which cannot alter columns in place.
            context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
                if sqlite and (violations := connection.exec_driver_sql("PRAGMA foreign_key_check").all()):
                    raise RuntimeError(f"Migration would leave foreign-key violations: {violations[:5]}")
            connection.commit()
        finally:
            if sqlite:
                # Never hand a connection with enforcement off back to the pool the app shares.
                connection.invalidate()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
