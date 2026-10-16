"""Load: raw CSV snapshots -> validated, normalised rows in the database.

The load is idempotent (upserts keyed on natural keys) and transactional: if any
validation error is found nothing is written.
"""

import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from epl.core.config import Settings
from epl.core.timezones import uk_to_utc
from epl.data.db import upsert
from epl.data.schema import DataVersion, Match, Team, TeamMatchStats
from epl.data.snapshots import matches_path, stats_path
from epl.data.validate import ValidationResult, validate_kickoffs, validate_matches, validate_stats

log = logging.getLogger(__name__)

# Typed column -> API statistic name. Missing statistics mean zero (the API omits zeros).
STAT_COLUMNS = {
    "shots": "totalScoringAtt",
    "shots_on_target": "ontargetScoringAtt",
    "shots_in_box": "attemptsIbox",
    "big_chances": "bigChanceCreated",
    "touches_in_box": "touchesInOppBox",
    "corners": "wonCorners",
    "passes": "totalPass",
    "possession": "possessionPercentage",
}

MATCH_INT_COLUMNS = [
    "home_score",
    "away_score",
    "home_ht_score",
    "away_ht_score",
    "home_red_cards",
    "away_red_cards",
]


@dataclass
class LoadReport:
    seasons: list[int]
    teams: int = 0
    matches: int = 0
    stats: int = 0
    data_version: int = 0
    warnings: list[str] = field(default_factory=list)


def _none_if_nan(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def read_matches(settings: Settings, season: int) -> pd.DataFrame:
    matches = pd.read_csv(matches_path(settings, season), parse_dates=["date"])
    if "status" not in matches:  # snapshots taken before status was recorded
        matches["status"] = matches["home_score"].notna().map({True: "finished", False: "scheduled"})
    for side in ("home", "away"):
        for column in ("short_name", "abbr"):
            if f"{side}_{column}" not in matches:
                matches[f"{side}_{column}"] = None
    return matches


def read_stats(settings: Settings, season: int) -> pd.DataFrame:
    """Wide API snapshot -> one row per team-match with typed columns and the raw payload."""
    path = stats_path(settings, season)
    if not path.exists():
        return pd.DataFrame(columns=["match_id", "side", "team", *STAT_COLUMNS, "raw"])
    wide = pd.read_csv(path)

    rows = []
    for side in ("home", "away"):
        prefix = f"{side}_"
        columns = [c for c in wide.columns if c.startswith(prefix) and c != f"{side}_team"]
        names = [c.removeprefix(prefix) for c in columns]
        for match_id, team, values in zip(
            wide["match_id"], wide[f"{side}_team"], wide[columns].to_numpy(), strict=True
        ):
            raw = {n: float(v) for n, v in zip(names, values, strict=True) if not pd.isna(v)}
            typed = {col: raw.get(api, 0.0) for col, api in STAT_COLUMNS.items()}
            rows.append({"match_id": int(match_id), "side": side, "team": team, **typed, "raw": raw})
    return pd.DataFrame(rows)


def team_metadata(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per team name, using the most recent non-null short name / abbreviation."""
    sides = [
        matches[[f"{s}_team", f"{s}_short_name", f"{s}_abbr", "date"]].set_axis(
            ["name", "short_name", "abbr", "date"], axis=1
        )
        for s in ("home", "away")
    ]
    teams = pd.concat(sides).sort_values("date")
    meta = teams.groupby("name")[["short_name", "abbr"]].last()  # last() skips nulls
    meta["short_name"] = meta["short_name"].fillna(pd.Series(meta.index, index=meta.index))
    meta["abbr"] = meta["abbr"].fillna(pd.Series(meta.index.str[:3].str.upper(), index=meta.index))
    return meta.reset_index()


class MissingSnapshotsError(FileNotFoundError):
    pass


def content_fingerprint(matches: pd.DataFrame, stats: pd.DataFrame) -> str:
    """Hash of everything a load writes, so an unchanged reload does not create a new data version."""
    stable_matches = matches.drop(columns=["date"]).assign(date=matches["date"].astype(str))
    match_hash = pd.util.hash_pandas_object(stable_matches.astype(str), index=False).to_numpy()
    stat_columns = ["match_id", "side", *STAT_COLUMNS]
    stat_hash = pd.util.hash_pandas_object(stats[stat_columns].astype(str), index=False)
    return hashlib.sha256(match_hash.tobytes() + stat_hash.to_numpy().tobytes()).hexdigest()


def load_seasons(session: Session, settings: Settings, seasons: list[int]) -> LoadReport:
    available = [s for s in seasons if matches_path(settings, s).exists()]
    if not available:
        raise MissingSnapshotsError(
            f"No snapshots for dataset '{settings.dataset}' in {settings.raw_dir}. "
            "Run `epl extract` first (the demo dataset is generated offline)."
        )
    seasons = available
    report = LoadReport(seasons=seasons)

    matches = pd.concat([read_matches(settings, s) for s in seasons], ignore_index=True)
    stats = pd.concat([read_stats(settings, s) for s in seasons], ignore_index=True)

    checks = ValidationResult()
    results = [validate_matches(matches, settings.teams_per_season), validate_kickoffs(matches)]
    if settings.dataset_info.has_match_stats:
        results.append(validate_stats(stats, matches.loc[matches["status"] == "finished", "match_id"]))
    for result in results:
        checks.errors += result.errors
        checks.warnings += result.warnings
    for warning in checks.warnings:
        log.warning(warning)
    checks.raise_for_errors()
    report.warnings = checks.warnings

    meta = team_metadata(matches)
    report.teams = upsert(session, Team.__table__, meta.to_dict("records"), key=["name"])  # type: ignore[arg-type]
    team_ids: dict[str, int] = {name: id_ for name, id_ in session.execute(select(Team.name, Team.id))}

    # Stored in the same row as the local time, so a corrected kickoff is re-converted on reload.
    kickoffs_utc = uk_to_utc(matches["date"])
    match_rows = [
        {
            "id": int(m["match_id"]),
            "season": int(m["season"]),
            "matchweek": int(m["matchweek"]),
            "kickoff": m["date"].to_pydatetime(),
            "kickoff_utc": kickoff_utc.to_pydatetime(),  # aware UTC (SQLite stores the wall clock)
            "status": m["status"],
            "home_team_id": team_ids[m["home_team"]],
            "away_team_id": team_ids[m["away_team"]],
            **{c: None if pd.isna(m[c]) else int(m[c]) for c in MATCH_INT_COLUMNS},
            "stadium": _none_if_nan(m["stadium"]),
            "attendance": None if pd.isna(m["attendance"]) else int(m["attendance"]),
        }
        for m, kickoff_utc in zip(matches.to_dict("records"), kickoffs_utc, strict=True)
    ]
    report.matches = upsert(session, Match.__table__, match_rows, key=["id"])  # type: ignore[arg-type]

    stat_rows = [
        {
            "match_id": s["match_id"],
            "side": s["side"],
            "team_id": team_ids[s["team"]],
            **{c: (float(s[c]) if c == "possession" else int(s[c])) for c in STAT_COLUMNS},
            "raw": s["raw"],
        }
        for s in stats.to_dict("records")
    ]
    report.stats = upsert(session, TeamMatchStats.__table__, stat_rows, key=["match_id", "side"])  # type: ignore[arg-type]

    # Recorded in the same transaction as the rows, so a reader never sees a version without its data.
    fingerprint = content_fingerprint(matches, stats)
    latest = session.scalars(select(DataVersion).order_by(DataVersion.id.desc()).limit(1)).first()
    if latest is None or latest.fingerprint != fingerprint or latest.dataset != settings.dataset:
        version = DataVersion(
            dataset=settings.dataset,
            fingerprint=fingerprint,
            n_matches=len(matches),
            n_finished=int((matches["status"] == "finished").sum()),
        )
        session.add(version)
        session.flush()
        report.data_version = version.id
    else:
        report.data_version = latest.id

    log.info(
        "Loaded %d teams, %d matches, %d team-match stat rows (data version %d)",
        report.teams,
        report.matches,
        report.stats,
        report.data_version,
    )
    return report
