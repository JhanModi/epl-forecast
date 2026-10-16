"""Data sources. Each writes the snapshot format in `epl.data.snapshots` to the dataset's raw directory."""

import pandas as pd

from epl.core.config import Settings
from epl.data.snapshots import ExtractReport, matches_path, stats_path
from epl.data.sources import openfootball, synthetic
from epl.data.sources.premier_league import extract_season as extract_premier_league_season
from epl.data.sources.premier_league_client import PremierLeagueClient


def extract(settings: Settings, seasons: list[int], with_stats: bool = True) -> list[ExtractReport]:
    """Refresh the configured dataset's snapshots for `seasons`."""
    if settings.dataset == "demo":
        synthetic.generate(settings)  # deterministic: every season is regenerated from the fixed seed
        return [_report(settings, s, stats=True) for s in sorted(settings.seasons) if s in seasons]
    if settings.dataset == "openfootball":
        for season in seasons:
            openfootball.extract_season(settings, season)
        return [_report(settings, s, stats=False) for s in seasons]
    with PremierLeagueClient(settings) as client:
        return [extract_premier_league_season(s, settings, client, with_stats) for s in seasons]


def _report(settings: Settings, season: int, stats: bool) -> ExtractReport:
    matches = pd.read_csv(matches_path(settings, season))
    finished = int((matches["status"] == "finished").sum())
    new_stats = len(pd.read_csv(stats_path(settings, season))) if stats else 0
    return ExtractReport(season, len(matches), finished, new_stats)
