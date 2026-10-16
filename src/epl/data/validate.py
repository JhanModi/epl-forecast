"""Data-quality checks run before anything reaches the database.

Errors stop the load (the data is wrong); warnings are logged (the data is
unusual but usable, e.g. a season still in progress).
"""

from dataclasses import dataclass, field

import pandas as pd

from epl.core.timezones import uk_to_utc

TEAMS_PER_SEASON = 20


class DataValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("Data validation failed:\n  - " + "\n  - ".join(issues))
        self.issues = issues


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def raise_for_errors(self) -> None:
        if self.errors:
            raise DataValidationError(self.errors)


def validate_matches(matches: pd.DataFrame, teams_per_season: int = TEAMS_PER_SEASON) -> ValidationResult:
    result = ValidationResult()
    matches_per_team = 2 * (teams_per_season - 1)
    err, warn = result.errors.append, result.warnings.append

    if dupes := matches["match_id"][matches["match_id"].duplicated()].tolist():
        err(f"duplicate match ids: {dupes[:5]}")
    if (matches["home_team"] == matches["away_team"]).any():
        err("a team is listed as playing itself")

    finished = matches[matches["status"] == "finished"]
    if finished[["home_score", "away_score"]].isna().any(axis=None):
        err("finished matches with a missing score")
    if (finished[["home_score", "away_score"]] < 0).any(axis=None):
        err("negative scores")

    for season, games in matches.groupby("season"):
        teams = pd.unique(games[["home_team", "away_team"]].to_numpy().ravel())
        if len(teams) != teams_per_season:
            err(f"{season}: expected {teams_per_season} teams, found {len(teams)}")
        if games.duplicated(["home_team", "away_team"]).any():
            err(f"{season}: the same home/away fixture appears twice")
        appearances = pd.concat([games["home_team"], games["away_team"]]).value_counts()
        if (appearances > matches_per_team).any():
            err(f"{season}: a team has more than {matches_per_team} fixtures")
        played = (games["status"] == "finished").sum()
        if played < len(games):
            warn(f"{season}: {played}/{len(games)} matches finished (season in progress)")

    return result


def validate_kickoffs(matches: pd.DataFrame) -> ValidationResult:
    """Every kickoff (naive UK local time, column `date`) must name exactly one UTC instant.

    All offending matches are listed at once, so the source can be corrected in one pass.
    """
    result = ValidationResult()
    unconvertible = matches.loc[uk_to_utc(matches["date"]).isna() & matches["date"].notna()]
    if len(unconvertible):
        listed = ", ".join(
            f"{m} at {d}" for m, d in zip(unconvertible["match_id"], unconvertible["date"], strict=True)
        )
        result.errors.append(
            "kickoff times that are ambiguous or do not exist in UK local time (the clocks change "
            f"that night), correct them in the source: {listed}"
        )
    return result


def validate_stats(stats: pd.DataFrame, finished_ids: pd.Series) -> ValidationResult:
    """`stats` has one row per team-match with the typed stat columns."""
    result = ValidationResult()

    possession = stats.groupby("match_id")["possession"].sum()
    if (bad := possession[(possession - 100).abs() > 1.0]).size:
        result.errors.append(f"possession does not sum to 100 in matches {bad.index[:5].tolist()}")
    if (stats["shots_on_target"] > stats["shots"]).any():
        result.errors.append("more shots on target than shots")
    if (stats.groupby("match_id").size() != 2).any():
        result.errors.append("a match does not have exactly two sides of statistics")

    if missing := sorted(set(finished_ids) - set(stats["match_id"])):
        result.warnings.append(f"{len(missing)} finished matches have no statistics yet")
    return result
