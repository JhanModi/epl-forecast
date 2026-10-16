"""openfootball/football.json: Premier League fixtures and results, dedicated to the public domain.

The publisher's README states: "Use as you please with no restrictions whatsoever."
Only fixtures and final scores are available (no team statistics), and the data
is community-maintained with no accuracy guarantee, so it is validated like
any other source before loading.
"""

import logging
import re
from typing import Any

import pandas as pd
import requests

from epl.core.config import Settings, season_label
from epl.data.snapshots import MATCH_COLUMNS, fixture_id, matches_path

log = logging.getLogger(__name__)

URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}-{next:02d}/en.1.json"

# Common display names and abbreviations for clubs (facts about the clubs, not data from any source).
CLUB_ABBREVIATIONS = {
    "Arsenal": "ARS",
    "Aston Villa": "AVL",
    "Bournemouth": "BOU",
    "Brentford": "BRE",
    "Brighton & Hove Albion": "BHA",
    "Burnley": "BUR",
    "Chelsea": "CHE",
    "Coventry City": "COV",
    "Crystal Palace": "CRY",
    "Everton": "EVE",
    "Fulham": "FUL",
    "Hull City": "HUL",
    "Ipswich Town": "IPS",
    "Leeds United": "LEE",
    "Leicester City": "LEI",
    "Liverpool": "LIV",
    "Luton Town": "LUT",
    "Manchester City": "MCI",
    "Manchester United": "MUN",
    "Newcastle United": "NEW",
    "Nottingham Forest": "NFO",
    "Sheffield United": "SHU",
    "Southampton": "SOU",
    "Sunderland": "SUN",
    "Tottenham Hotspur": "TOT",
    "West Ham United": "WHU",
    "Wolverhampton Wanderers": "WOL",
}


def club_name(raw: str) -> str:
    """'Arsenal FC' -> 'Arsenal', 'AFC Bournemouth' -> 'Bournemouth', 'Hull City AFC' -> 'Hull City'."""
    name = re.sub(r"^(AFC|FC)\s+|\s+(AFC|FC)$", "", raw.strip())
    return name.replace(" and ", " & ")


def _abbreviation(name: str) -> str:
    return CLUB_ABBREVIATIONS.get(name, re.sub(r"[^A-Z]", "", name.upper())[:3])


def _matchweek(round_name: str) -> int:
    match = re.search(r"(\d+)", round_name)
    if not match:
        raise ValueError(f"unrecognised round {round_name!r}")
    return int(match.group(1))


def parse_season(payload: dict[str, Any], season: int) -> tuple[pd.DataFrame, list[str]]:
    """football.json season document -> snapshot rows, plus data-quality warnings."""
    warnings: list[str] = []
    raw = payload["matches"]
    teams = sorted({club_name(m["team1"]) for m in raw} | {club_name(m["team2"]) for m in raw})
    index = {t: i for i, t in enumerate(teams)}
    rows = []
    for m in raw:
        home, away = club_name(m["team1"]), club_name(m["team2"])
        score = m.get("score")
        full_time = half_time = None
        if isinstance(score, dict) and "ft" in score:
            full_time, half_time = score["ft"], score.get("ht")
        elif isinstance(score, list) and len(score) == 2:
            full_time = score
            warnings.append(f"{season} {home} v {away}: score without a full-time/half-time breakdown")
        rows.append(
            {
                "match_id": fixture_id(season, index[home], index[away]),
                "date": f"{m['date']} {m.get('time', '15:00')}:00",
                "matchweek": _matchweek(m.get("round", "")),
                "status": "finished" if full_time else "scheduled",
                "home_team": home,
                "away_team": away,
                "home_short_name": home,
                "away_short_name": away,
                "home_abbr": _abbreviation(home),
                "away_abbr": _abbreviation(away),
                "home_score": full_time[0] if full_time else None,
                "away_score": full_time[1] if full_time else None,
                "home_ht_score": half_time[0] if half_time else None,
                "away_ht_score": half_time[1] if half_time else None,
                "home_red_cards": None,
                "away_red_cards": None,
                "stadium": None,
                "attendance": None,
                "season": season,
            }
        )
    frame = pd.DataFrame(rows, columns=MATCH_COLUMNS).sort_values(["date", "match_id"], ignore_index=True)
    return frame, warnings


def extract_season(settings: Settings, season: int, session: requests.Session | None = None) -> int:
    """Download one season and write its snapshot. Returns the number of fixtures."""
    http = session or requests.Session()
    response = http.get(
        URL.format(season=season, next=(season + 1) % 100), timeout=settings.api_timeout_seconds
    )
    response.raise_for_status()
    frame, warnings = parse_season(response.json(), season)
    for warning in warnings:
        log.warning(warning)
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(matches_path(settings, season), index=False, lineterminator="\n")
    log.info("openfootball %s: %d fixtures", season_label(season), len(frame))
    return len(frame)
