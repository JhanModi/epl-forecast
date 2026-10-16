"""Thin, typed client for the Premier League website API (restricted dataset; see `epl.data.datasets`).

Transient failures (timeouts, 429 and 5xx) are retried with exponential backoff,
and every request has a timeout, so a slow API cannot hang the pipeline.
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from epl.core.config import Settings

log = logging.getLogger(__name__)

MATCHWEEKS = 38


@dataclass(frozen=True, slots=True)
class ApiTeam:
    name: str
    short_name: str | None
    abbr: str | None
    score: int | None
    ht_score: int | None
    red_cards: int | None


@dataclass(frozen=True, slots=True)
class ApiMatch:
    match_id: int
    season: int
    matchweek: int
    kickoff: str
    period: str
    home: ApiTeam
    away: ApiTeam
    stadium: str | None
    attendance: int | None


def _team(payload: dict[str, Any]) -> ApiTeam:
    return ApiTeam(
        name=payload["name"],
        short_name=payload.get("shortName"),
        abbr=payload.get("abbr"),
        score=payload.get("score"),
        ht_score=payload.get("halfTimeScore"),
        red_cards=payload.get("redCards"),
    )


def parse_match(payload: dict[str, Any]) -> ApiMatch:
    return ApiMatch(
        match_id=int(payload["matchId"]),
        season=int(payload["season"]),
        matchweek=int(payload["matchWeek"]),
        kickoff=payload["kickoff"],
        period=payload.get("period", "PreMatch"),
        home=_team(payload["homeTeam"]),
        away=_team(payload["awayTeam"]),
        stadium=payload.get("ground"),
        attendance=payload.get("attendance"),
    )


class PremierLeagueClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        retry = Retry(
            total=5,
            backoff_factor=1.0,  # 1s, 2s, 4s, ...
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers["User-Agent"] = "epl-forecast/1.0 (educational project)"
        return session

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.session.close()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(url, params=params, timeout=self.settings.api_timeout_seconds)
        response.raise_for_status()
        return response.json()

    def matches(self, season: int) -> Iterator[ApiMatch]:
        """Every fixture of a season, played or not."""
        for matchweek in range(1, MATCHWEEKS + 1):
            params = {
                "competition": self.settings.api_competition_id,
                "season": season,
                "matchweek": matchweek,
                "_limit": 100,
            }
            data = self._get(self.settings.api_url, params)["data"]
            log.debug("Fetched matchweek %d/%d (%d matches)", matchweek, MATCHWEEKS, len(data))
            yield from (parse_match(m) for m in data)

    def match_stats(self, match_id: int) -> dict[str, dict[str, float]]:
        """Team statistics for one match, keyed by side ('home' / 'away').

        The API omits any statistic whose value is zero.
        """
        payload = self._get(f"{self.settings.api_url}/{match_id}/stats")
        time.sleep(self.settings.api_request_delay_seconds)  # be polite to a free API
        return {team["side"].lower(): team["stats"] for team in payload}
