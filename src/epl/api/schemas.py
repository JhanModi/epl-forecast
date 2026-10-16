"""Response models: the API's public contract (also exported as OpenAPI for the TypeScript client)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Health(Schema):
    status: Literal["ok"]
    version: str


class Readiness(Schema):
    status: Literal["ready", "degraded"]
    database: bool
    model: bool
    detail: str | None = None


class Team(Schema):
    id: int
    name: str
    short_name: str
    abbr: str


class Dataset(Schema):
    name: str
    label: str
    synthetic: bool
    redistributable: bool | None = Field(description="null = permission not established")
    has_match_stats: bool
    license: str
    source_url: str | None
    notes: str


class Meta(Schema):
    version: str
    dataset: Dataset
    data_version: int = Field(description="Increments whenever a load changes the data")
    current_season: int
    seasons: list[int]
    tuning_season: int
    holdout_season: int
    production_model: str
    model_version: str | None
    trained_through: str | None
    last_result: datetime | None
    matches_played: int
    matches_scheduled: int


class Fixture(Schema):
    match_id: int
    season: int
    matchweek: int
    kickoff: datetime
    status: str
    home_team: str
    away_team: str
    home_goals: int | None
    away_goals: int | None


class Scoreline(Schema):
    home: int
    away: int
    probability: float


class FixturePrediction(Schema):
    match_id: int
    season: int
    matchweek: int
    kickoff: datetime
    home_team: str
    away_team: str
    p_home: float = Field(ge=0, le=1)
    p_draw: float = Field(ge=0, le=1)
    p_away: float = Field(ge=0, le=1)
    expected_home_goals: float
    expected_away_goals: float
    over_2_5: float
    both_teams_score: float
    home_clean_sheet: float
    away_clean_sheet: float
    top_scorelines: list[Scoreline]


class UpcomingPredictions(Schema):
    model: str
    model_version: str
    predictions: list[FixturePrediction]


class Matchup(FixturePrediction):
    score_matrix: list[list[float]] = Field(description="P(home i, away j) for 0..6 goals each")


class StandingRow(Schema):
    position: int
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int


class SimulatedTeam(StandingRow):
    expected_points: float
    points_p05: float
    points_p95: float
    expected_position: float
    p_title: float
    p_top_four: float
    p_relegation: float


class Simulation(Schema):
    season: int
    as_of_matchweek: int | None
    cutoff: str | None
    matches_played: int
    matches_remaining: int
    n_runs: int
    teams: list[SimulatedTeam]
    position_probabilities: list[list[float]] = Field(description="Row per team (same order as `teams`)")
    actual_final_positions: dict[str, int] | None
    cached: bool


class TeamRating(Schema):
    team: str
    elo: float
    attack: float | None = Field(description="Expected goals scored vs an average side, neutral venue")
    defence: float | None = Field(description="Expected goals conceded vs an average side, neutral venue")
    rating: float | None = Field(description="attack - defence")


class EloPoint(Schema):
    match_id: int
    season: int
    kickoff: datetime
    elo: float


class ModelCardOut(Schema):
    name: str
    version: str
    description: str
    created_at: str
    trained_through: str
    n_matches: int
    data_fingerprint: str
    params: dict[str, Any]
    package_version: str
    git_commit: str | None
    metrics: dict[str, Any]


class JobAccepted(Schema):
    job_id: str
    status: str


class Job(Schema):
    job_id: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    owner: str = Field(description="host:pid of the process that ran the job")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class Problem(Schema):
    """RFC 9457 problem details."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    request_id: str | None = None


# ----------------------------------------------------------------------------- backtest report
class Estimate(Schema):
    mean: float
    low: float = Field(description="Bootstrap 95% CI lower bound")
    high: float = Field(description="Bootstrap 95% CI upper bound")


class Comparison(Schema):
    difference: Estimate = Field(description="Mean per-match RPS difference (this model - other)")
    t_statistic: float
    p_value: float


class CalibrationPoint(Schema):
    forecast: float
    observed: float
    count: int


class Calibration(Schema):
    curves: dict[str, list[CalibrationPoint]]
    ece: float = Field(description="Expected calibration error, averaged over outcomes")


class ModelReport(Schema):
    n: int
    metrics: dict[str, Estimate] = Field(description="rps, log_loss, brier, accuracy")
    calibration: Calibration
    rps_by_matchweek: dict[str, float]
    confusion_matrix: list[list[int]] = Field(description="Rows actual, columns predicted: home, draw, away")
    mean_probabilities: dict[str, float]
    vs_reference: Comparison
    vs_best: Comparison


class SeasonReport(Schema):
    ranking: list[str]
    best: str = Field(description="Best deployable model (the oracle, if present, is excluded)")
    reference: str
    ceiling: str | None = Field(
        default=None, description="Oracle using the true probabilities (synthetic data)"
    )
    n_matches: int
    outcome_rates: dict[str, float]
    models: dict[str, ModelReport]


class BacktestReport(Schema):
    generated_at: str
    dataset: Dataset | None = None
    tuning_season: int
    holdout_season: int
    step_days: int
    hyperparameters: dict[str, Any]
    seasons: dict[str, SeasonReport]


class LoggedForecast(Schema):
    """One match in the track record: the latest forecast logged before its kickoff."""

    match_id: int
    season: int
    matchweek: int
    kickoff: datetime = Field(description="UK local time, as for every other kickoff in this API")
    kickoff_utc: datetime = Field(description="The same instant in UTC")
    status: str
    home_team: str
    away_team: str
    prediction_id: int
    model_version: str
    created_at: datetime = Field(description="When the forecast was logged (UTC); always before kickoff")
    p_home: float = Field(ge=0, le=1)
    p_draw: float = Field(ge=0, le=1)
    p_away: float = Field(ge=0, le=1)
    home_goals: int | None = Field(description="Null until the match is finished")
    away_goals: int | None = Field(description="Null until the match is finished")
    rps: float | None = Field(description="Ranked probability score of this forecast; null until finished")


class PredictionLog(Schema):
    season: int
    items: list[LoggedForecast]
    total: int = Field(description="Matching matches across all pages")
    limit: int
    offset: int


class LivePerformance(Schema):
    season: int
    n: int
    pending: int = 0
    metrics: dict[str, Estimate] | None = None
