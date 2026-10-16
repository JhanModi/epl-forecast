"""Engine and session management, plus a dialect-aware bulk upsert."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Table, create_engine, event
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker


@lru_cache
def get_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if path := url.removeprefix("sqlite:///"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores FKs unless asked
            cursor.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str) -> Iterator[Session]:
    """A transaction: commit on success, roll back on any exception."""
    session = session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upsert(session: Session, table: Table, rows: Sequence[dict[str, Any]], key: Sequence[str]) -> int:
    """INSERT ... ON CONFLICT (key) DO UPDATE, on SQLite or PostgreSQL.

    Makes every load idempotent: running it twice leaves the database unchanged.
    """
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect not in ("sqlite", "postgresql"):
        raise NotImplementedError(f"upsert is not implemented for {dialect}")

    # Stay under SQLite's bound-parameter limit on wide tables.
    batch = max(1, 30_000 // max(len(rows[0]), 1))
    for start in range(0, len(rows), batch):
        chunk = list(rows[start : start + batch])
        statement = (postgresql.insert(table) if dialect == "postgresql" else sqlite.insert(table)).values(
            chunk
        )
        updates = {name: statement.excluded[name] for name in rows[0] if name not in key}
        session.execute(statement.on_conflict_do_update(index_elements=list(key), set_=updates))
    return len(rows)
