"""Application settings, loaded from environment variables (prefix ``EPL_``) or a ``.env`` file.

Everything that differs between a laptop, CI and a container lives here, so the
code itself never hard-codes a path or a connection string (12-factor config).
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from epl.data.datasets import DatasetInfo, DatasetName, dataset_info

ROOT = Path(__file__).resolve().parents[3]

# Values that look like configuration but protect nothing.
WEAK_TOKENS = {"change-me", "changeme", "secret", "password", "admin", "token", "test"}
MIN_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EPL_", env_file=".env", extra="ignore")

    # Data. Paths default to locations scoped by dataset (see `_dataset_scoped_paths`).
    dataset: DatasetName = "demo"
    data_root: Path = ROOT / "data"
    raw_dir: Path = ROOT / "data" / "demo"
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'demo.db').as_posix()}"
    artifacts_dir: Path = ROOT / "data" / "artifacts" / "demo"

    # Premier League public API (the restricted `premier-league` dataset only)
    api_url: str = "https://sdp-prem-prod.premier-league-prod.pulselive.com/api/v2/matches"
    api_competition_id: int = 8
    api_timeout_seconds: float = 30.0
    api_request_delay_seconds: float = 0.2

    # Seasons are identified by their starting year: 2025 -> 2025/26.
    seasons: list[int] = Field(default_factory=lambda: [2023, 2024, 2025, 2026])
    teams_per_season: int = 20
    current_season: int = 2026
    tuning_season: int = 2024
    holdout_season: int = 2025

    # Simulation resource controls (see docs/adr/0003-bounded-simulation-work.md)
    simulation_runs: int = 10_000
    simulation_run_sizes: tuple[int, ...] = (1_000, 10_000)
    simulation_max_concurrency: int = Field(default=2, ge=1)
    simulation_queue_timeout_seconds: float = Field(default=10.0, gt=0)
    simulation_rate_per_minute: int = Field(default=20, ge=1)  # uncached simulations per client
    simulation_cache_max_rows: int = Field(default=256, ge=1)

    # Serving
    cache_check_interval_seconds: float = Field(default=1.0, ge=0)  # how stale a cached frame/model may be
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    frontend_dist: Path = ROOT / "frontend" / "dist"
    admin_token: SecretStr | None = None  # admin endpoints are disabled unless this is set
    log_level: str = "INFO"
    log_json: bool = False

    @model_validator(mode="before")
    @classmethod
    def _dataset_scoped_paths(cls, values: Any) -> Any:
        """Unless set explicitly, raw data, the database and artifacts live under the dataset's name."""
        if not isinstance(values, dict):
            return values
        dataset = values.get("dataset", "demo")
        root = Path(values.get("data_root", ROOT / "data"))
        values.setdefault("raw_dir", root / dataset)
        values.setdefault("artifacts_dir", root / "artifacts" / dataset)
        values.setdefault("database_url", f"sqlite:///{(root / f'{dataset}.db').as_posix()}")
        return values

    @field_validator("admin_token", mode="before")
    @classmethod
    def _strong_admin_token(cls, value: Any) -> Any:
        """Fail at startup on a weak token rather than run with a guessable one."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw is None or raw == "":
            return None  # unset (or an empty compose variable): admin endpoints stay disabled
        if not isinstance(raw, str) or len(raw) < MIN_TOKEN_LENGTH or raw.lower() in WEAK_TOKENS:
            raise ValueError(
                f"EPL_ADMIN_TOKEN must be at least {MIN_TOKEN_LENGTH} random characters "
                '(e.g. python -c "import secrets; print(secrets.token_urlsafe(32))"), or unset to disable '
                "the admin endpoints"
            )
        return raw

    @field_validator("simulation_run_sizes")
    @classmethod
    def _sorted_sizes(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or min(value) < 100:
            raise ValueError("simulation_run_sizes needs at least one size of 100 runs or more")
        return tuple(sorted(set(value)))

    @property
    def dataset_info(self) -> DatasetInfo:
        return dataset_info(self.dataset)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def season_label(season: int) -> str:
    """2023 -> '2023_24'"""
    return f"{season}_{(season + 1) % 100:02d}"


def season_name(season: int) -> str:
    """2023 -> '2023/24'"""
    return season_label(season).replace("_", "/")
