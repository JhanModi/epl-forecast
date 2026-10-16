"""Run Alembic migrations programmatically, so `epl db upgrade` works from an installed package."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def check(url: str) -> None:
    """Fail if the ORM models have drifted from the migrations."""
    command.check(alembic_config(url))
