"""Synthetic leagues with known true parameters, for tests that need ground truth."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from epl.core.config import Settings, season_label


@dataclass
class League:
    teams: list[str]
    attack: dict[str, float]
    defence: dict[str, float]
    home_advantage: float = 0.25
    intercept: float = 0.1
    seed: int = 0
    stats: bool = True
    relegated: dict[int, list[str]] = field(default_factory=dict)


def default_league(n_teams: int = 6, seed: int = 0) -> League:
    teams = [f"Team {chr(65 + i)}" for i in range(n_teams)]
    spread = np.linspace(0.4, -0.4, n_teams)
    return League(
        teams=teams,
        attack=dict(zip(teams, spread, strict=True)),
        defence=dict(zip(teams, -spread, strict=True)),
        seed=seed,
    )


def season_fixtures(teams: list[str], season: int, rounds: int = 1) -> pd.DataFrame:
    """Double round-robin, one 'matchweek' per pairing round, weekly from August."""
    rows = []
    for r in range(rounds):
        for i, home in enumerate(teams):
            for j, away in enumerate(teams):
                if i != j:
                    rows.append({"home_team": home, "away_team": away, "round": r})
    fixtures = pd.DataFrame(rows).sample(frac=1, random_state=season).reset_index(drop=True)
    per_week = max(1, len(teams) // 2)
    fixtures["matchweek"] = fixtures.index // per_week + 1
    fixtures["kickoff"] = pd.Timestamp(f"{season}-08-10 15:00") + pd.to_timedelta(
        (fixtures["matchweek"] - 1) * 7 + fixtures.index % per_week % 3, unit="D"
    )
    fixtures["season"] = season
    fixtures["match_id"] = season * 10_000 + fixtures.index
    return fixtures


def play(league: League, fixtures: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    lam = np.exp(
        league.intercept
        + league.home_advantage
        + fixtures["home_team"].map(league.attack)
        + fixtures["away_team"].map(league.defence)
    )
    nu = np.exp(
        league.intercept
        + fixtures["away_team"].map(league.attack)
        + fixtures["home_team"].map(league.defence)
    )
    out = fixtures.copy()
    out["home_goals"] = rng.poisson(lam).astype(int)
    out["away_goals"] = rng.poisson(nu).astype(int)
    out["status"] = "finished"
    for side in ("home", "away"):
        goals = out[f"{side}_goals"]
        out[f"{side}_shots"] = goals * 3 + rng.integers(4, 12, len(out))
        out[f"{side}_shots_on_target"] = goals + rng.integers(0, 4, len(out))
        out[f"{side}_shots_in_box"] = out[f"{side}_shots"] // 2
        out[f"{side}_big_chances"] = goals + rng.integers(0, 2, len(out))
        out[f"{side}_touches_in_box"] = out[f"{side}_shots"] * 2
        out[f"{side}_corners"] = rng.integers(1, 9, len(out))
        out[f"{side}_passes"] = rng.integers(300, 600, len(out))
    out["home_possession"] = rng.uniform(35, 65, len(out)).round(1)
    out["away_possession"] = 100 - out["home_possession"]
    out["home_red_cards"] = out["away_red_cards"] = 0
    return out


def match_frame(
    league: League | None = None, seasons: tuple[int, ...] = (2023, 2024), rounds: int = 1
) -> pd.DataFrame:
    """A played match frame in the shape `epl.data.repository.match_frame` returns."""
    league = league or default_league()
    rng = np.random.default_rng(league.seed)
    frames = [play(league, season_fixtures(league.teams, s, rounds), rng) for s in seasons]
    frame = pd.concat(frames, ignore_index=True).sort_values(["kickoff", "match_id"], ignore_index=True)
    frame["home_goals"] = frame["home_goals"].astype("Int64")
    frame["away_goals"] = frame["away_goals"].astype("Int64")
    return frame.drop(columns=["round"])


API_STATS = {
    "shots": "totalScoringAtt",
    "shots_on_target": "ontargetScoringAtt",
    "shots_in_box": "attemptsIbox",
    "big_chances": "bigChanceCreated",
    "touches_in_box": "touchesInOppBox",
    "corners": "wonCorners",
    "passes": "totalPass",
    "possession": "possessionPercentage",
}


def write_snapshots(
    frame: pd.DataFrame, settings: Settings, unplayed_from: pd.Timestamp | None = None
) -> None:
    """Write a frame as raw API-style CSV snapshots, optionally leaving later fixtures unplayed."""
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    for season, season_games in frame.groupby("season"):
        games = season_games.copy()
        if unplayed_from is not None:
            later = games["kickoff"] >= unplayed_from
            games.loc[later, ["home_goals", "away_goals"]] = pd.NA
            games.loc[later, "status"] = "scheduled"
        matches = pd.DataFrame(
            {
                "match_id": games["match_id"],
                "date": games["kickoff"].dt.strftime("%Y-%m-%d %H:%M:%S"),
                "matchweek": games["matchweek"],
                "status": games["status"],
                "home_team": games["home_team"],
                "away_team": games["away_team"],
                "home_short_name": games["home_team"].str.replace("Team ", ""),
                "away_short_name": games["away_team"].str.replace("Team ", ""),
                "home_abbr": "T" + games["home_team"].str[-1] + "X",
                "away_abbr": "T" + games["away_team"].str[-1] + "X",
                "home_score": games["home_goals"],
                "away_score": games["away_goals"],
                "home_ht_score": pd.NA,
                "away_ht_score": pd.NA,
                "home_red_cards": 0,
                "away_red_cards": 0,
                "stadium": "Test Ground",
                "attendance": 30_000,
                "season": season,
            }
        )
        label = season_label(int(season))  # type: ignore[call-overload]
        matches.to_csv(settings.raw_dir / f"matches_{label}.csv", index=False)

        played = games[games["status"] == "finished"]
        stats = pd.DataFrame(
            {
                "match_id": played["match_id"],
                "home_team": played["home_team"],
                "away_team": played["away_team"],
            }
        )
        for side in ("home", "away"):
            for ours, api in API_STATS.items():
                stats[f"{side}_{api}"] = played[f"{side}_{ours}"]
        stats.to_csv(settings.raw_dir / f"stats_{label}.csv", index=False)
