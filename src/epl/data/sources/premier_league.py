"""Extract from the Premier League's website API (the restricted `premier-league` dataset).

The site's terms of use prohibit redistributing its material or building a
database from it, so snapshots written here are for local, personal use and
are excluded from Git and the Docker build context (see `epl.data.datasets`).

Extraction is incremental: fixtures are refreshed on every run, but statistics
are only downloaded for finished matches that are not in the snapshot yet.
"""

import logging

import pandas as pd

from epl.core.config import Settings
from epl.data.snapshots import ExtractReport, matches_path, stats_path
from epl.data.sources.premier_league_client import ApiMatch, PremierLeagueClient

log = logging.getLogger(__name__)

STATUS = {"FullTime": "finished", "PreMatch": "scheduled", "Postponed": "postponed", "Abandoned": "postponed"}


def match_row(match: ApiMatch) -> dict[str, object]:
    return {
        "match_id": match.match_id,
        "date": match.kickoff,
        "matchweek": match.matchweek,
        "status": STATUS.get(match.period, "live"),
        "home_team": match.home.name,
        "away_team": match.away.name,
        "home_short_name": match.home.short_name,
        "away_short_name": match.away.short_name,
        "home_abbr": match.home.abbr,
        "away_abbr": match.away.abbr,
        "home_score": match.home.score,
        "away_score": match.away.score,
        "home_ht_score": match.home.ht_score,
        "away_ht_score": match.away.ht_score,
        "home_red_cards": match.home.red_cards,
        "away_red_cards": match.away.red_cards,
        "stadium": match.stadium,
        "attendance": match.attendance,
        "season": match.season,
    }


def extract_season(
    season: int, settings: Settings, client: PremierLeagueClient, with_stats: bool = True
) -> ExtractReport:
    settings.raw_dir.mkdir(parents=True, exist_ok=True)

    matches = (
        pd.DataFrame([match_row(m) for m in client.matches(season)])
        .drop_duplicates("match_id")
        .sort_values(["date", "match_id"])
        .reset_index(drop=True)
    )
    matches.to_csv(matches_path(settings, season), index=False, lineterminator="\n")
    finished = matches[matches["status"] == "finished"]
    log.info("Season %d: %d fixtures, %d finished", season, len(matches), len(finished))

    new_stats = 0
    if with_stats:
        path = stats_path(settings, season)
        existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["match_id"])
        missing = finished[~finished["match_id"].isin(existing["match_id"])]

        rows = []
        for i, match in enumerate(missing.to_dict("records"), start=1):
            row: dict[str, object] = {
                "match_id": match["match_id"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
            }
            for side, stats in client.match_stats(int(match["match_id"])).items():
                row.update({f"{side}_{name}": value for name, value in stats.items()})
            rows.append(row)
            if i % 50 == 0 or i == len(missing):
                log.info("Season %d: stats %d/%d", season, i, len(missing))

        if rows:
            combined = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
            combined.to_csv(path, index=False, lineterminator="\n")
        new_stats = len(rows)

    return ExtractReport(season, len(matches), len(finished), new_stats)
