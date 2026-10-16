"""The raw snapshot format every data source writes and `epl.data.load` reads.

Per season, ``matches_<label>.csv`` holds one row per fixture (played or not)
and the optional ``stats_<label>.csv`` holds one row per played match with
team statistics as ``home_<stat>`` / ``away_<stat>`` (statistic names follow the
Premier League API, e.g. ``totalScoringAtt``; a missing value means zero).
"""

from dataclasses import dataclass
from pathlib import Path

from epl.core.config import Settings, season_label

MATCH_COLUMNS = [
    "match_id",
    "date",
    "matchweek",
    "status",
    "home_team",
    "away_team",
    "home_short_name",
    "away_short_name",
    "home_abbr",
    "away_abbr",
    "home_score",
    "away_score",
    "home_ht_score",
    "away_ht_score",
    "home_red_cards",
    "away_red_cards",
    "stadium",
    "attendance",
    "season",
]

STATUSES = ("scheduled", "live", "finished", "postponed")


@dataclass(frozen=True)
class ExtractReport:
    season: int
    fixtures: int
    finished: int
    new_stats: int


def matches_path(settings: Settings, season: int) -> Path:
    return settings.raw_dir / f"matches_{season_label(season)}.csv"


def stats_path(settings: Settings, season: int) -> Path:
    return settings.raw_dir / f"stats_{season_label(season)}.csv"


def truth_path(settings: Settings, season: int) -> Path:
    """Synthetic datasets only: the true scoring rates each match was simulated from."""
    return settings.raw_dir / f"truth_{season_label(season)}.csv"


def fixture_id(season: int, home_index: int, away_index: int) -> int:
    """A stable id for sources without one: each ordered pairing occurs once per season."""
    return season * 10_000 + home_index * 100 + away_index
