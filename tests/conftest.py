from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from epl.api.main import create_app
from epl.core.config import Settings
from epl.data.db import session_scope
from epl.services import pipeline
from tests import factories

ADMIN_TOKEN = "test-admin-token-0123456789-abcdefghij"  # long enough to pass the strength check


@pytest.fixture
def frame() -> pd.DataFrame:
    """Two played seasons of a 6-team synthetic league."""
    return factories.match_frame()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        raw_dir=tmp_path / "raw",
        artifacts_dir=tmp_path / "artifacts",
        frontend_dist=tmp_path / "no-frontend",
        seasons=[2023, 2024],
        teams_per_season=6,
        current_season=2024,
        tuning_season=2023,
        holdout_season=2023,
        admin_token=ADMIN_TOKEN,
        cache_check_interval_seconds=0,
    )


@pytest.fixture
def seeded(settings: Settings) -> Settings:
    """A database with 2023 complete and 2024 half-played, plus a trained production model."""
    data = factories.match_frame()
    second = data[data["season"] == 2024]
    factories.write_snapshots(data, settings, unplayed_from=second["kickoff"].iloc[len(second) // 2])
    pipeline.load(settings)
    pipeline.train(settings, models=["prior", "dixon_coles"])
    return settings


@pytest.fixture
def client(seeded: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(seeded)) as test_client:
        yield test_client


@pytest.fixture
def db(seeded: Settings) -> Iterator[object]:
    with session_scope(seeded.database_url) as session:
        yield session
