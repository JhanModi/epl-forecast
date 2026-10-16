"""A synthetic, freely shareable demo league.

Twenty fictional clubs play four double round-robin seasons with promotion and
relegation (three up, three down). Each club has an attack and a defence
strength that drift every matchweek and are shocked every summer, so the
generating process is *not* the static model the forecasters assume: the demo
measures how the pipeline copes with realistic misspecification.

Goals are Poisson. Match statistics (shots, shots on target, big chances,
possession, ...) are drawn conditional on each side's scoring rate, so the
feature-based models have real, but noisy, signal to learn from.

The true scoring rates of every match are written alongside the data
(``truth_<season>.csv``), which lets the backtest report an *oracle*: the score
of a forecaster that knew the generating probabilities. That is the ceiling no
model can beat on this data.

Calibration. Three constants were set against aggregate statistics of the
2023/24-2025/26 Premier League (computed locally; no match data is copied),
averaging over 20 seeds rather than tuning the committed one:
- final-points standard deviation: real 18.2; demo mean 17.9 (5-95% of seeds
  14.5-20.9), via QUALITY_SD;
- home-win rate: real 43.2%; demo about 43.6%, via HOME_ADVANTAGE;
- goals per match: real 2.99; demo about 2.9, via INTERCEPT.
The committed seed's three complete seasons realise points SD 19.5, 44.5% home
wins, 3.2 goals and 19.7% draws. Known gap: independent Poisson goals produce
fewer draws than real football (24.5%).

Generation is deterministic for a given seed.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
from scipy.stats import poisson

from epl.core.config import Settings
from epl.data.snapshots import MATCH_COLUMNS, fixture_id, matches_path, stats_path, truth_path

log = logging.getLogger(__name__)

SEED = 20_261_010
AS_OF = datetime(2026, 10, 10, 23, 59)  # the demo's "today": later fixtures are unplayed

# Fictional clubs: (name, short name, abbreviation). None of these is a real professional club.
CLUBS = [
    ("Ashbury Rovers", "Ashbury", "ASH"),
    ("Brackenford United", "Brackenford", "BRK"),
    ("Calder Vale", "Calder Vale", "CAL"),
    ("Dunmore Athletic", "Dunmore", "DUN"),
    ("Eastwick City", "Eastwick", "EAS"),
    ("Fernhill Town", "Fernhill", "FER"),
    ("Glenbridge Wanderers", "Glenbridge", "GLE"),
    ("Harrowmere Albion", "Harrowmere", "HAR"),
    ("Ironvale Forest", "Ironvale", "IRO"),
    ("Kestrel Park", "Kestrel Park", "KES"),
    ("Larkhill United", "Larkhill", "LAR"),
    ("Marlow Heath", "Marlow Heath", "MAR"),
    ("Northcliffe City", "Northcliffe", "NOR"),
    ("Oakhaven Rovers", "Oakhaven", "OAK"),
    ("Pemberton Athletic", "Pemberton", "PEM"),
    ("Queensbury Town", "Queensbury", "QUE"),
    ("Redmarsh United", "Redmarsh", "RED"),
    ("Stonebury City", "Stonebury", "STO"),
    ("Thornfield Albion", "Thornfield", "THO"),
    ("Upton Vale", "Upton Vale", "UPT"),
    ("Westmoor Wanderers", "Westmoor", "WES"),
    ("Yarrowdale Town", "Yarrowdale", "YAR"),
    ("Amberley Forest", "Amberley", "AMB"),
    ("Blackwater City", "Blackwater", "BLA"),
    ("Coldharbour United", "Coldharbour", "COL"),
    ("Draycott Athletic", "Draycott", "DRA"),
]
TOP_FLIGHT = 20

# Generating process. Log-scale parameters, as in the Dixon-Coles model the forecasters use.
INTERCEPT = 0.24
HOME_ADVANTAGE = 0.18
# Each club has a persistent *level* (its squad quality) and a current *form* that wanders around it.
QUALITY_SD = 0.25  # spread of club levels; calibrated so final-points spread matches the Premier League
STYLE_SD = 0.08  # attack/defence variation around a club's quality
PROMOTED_SHIFT = 0.25  # second-tier clubs are this much weaker in attack and defence
WEEKLY_DRIFT_SD = 0.03  # form changes from week to week...
WEEKLY_REVERSION = 0.1  # ...and is pulled back towards the club's level
STRENGTH_BOUND = 0.6  # keeps expected goals in a footballing range (at most ~5 a side, rarely reached)
SUMMER_SHOCK_SD = 0.07  # transfers move a club's level each summer...
SUMMER_SHRINK = 0.95  # ...and the league slowly regresses towards parity

# Kickoff slots for the ten matches of a matchweek: (days after Saturday, time).
SLOTS = [
    (0, time(12, 30)),
    *[(0, time(15, 0))] * 5,
    (0, time(17, 30)),
    (1, time(14, 0)),
    (1, time(16, 30)),
    (2, time(20, 0)),
]
BREAKS_AFTER = {3, 7, 11, 29}  # international breaks: no matchweek the following weekend


@dataclass
class _Club:
    name: str
    short: str
    abbr: str
    attack: float  # current form
    defence: float  # current form; higher = concedes more
    level_attack: float = 0.0  # long-run levels that form reverts to
    level_defence: float = 0.0


@dataclass
class _League:
    rng: np.random.Generator
    clubs: dict[str, _Club] = field(default_factory=dict)
    members: list[str] = field(default_factory=list)  # current top-flight clubs
    reserve: list[str] = field(default_factory=list)  # the "second tier" clubs promote from


def _season_start(season: int) -> date:
    """The second Saturday of August."""
    first = date(season, 8, 1)
    first_saturday = first + timedelta(days=(5 - first.weekday()) % 7)
    return first_saturday + timedelta(days=7)


def _round_robin(teams: list[str], rng: np.random.Generator) -> list[list[tuple[str, str]]]:
    """Circle-method double round-robin: 38 rounds, each club home and away against every other."""
    order = list(rng.permutation(teams))
    n = len(order)
    rounds = []
    for r in range(n - 1):
        pairs = [(order[i], order[n - 1 - i]) for i in range(n // 2)]
        rounds.append([(a, b) if (r + i) % 2 == 0 else (b, a) for i, (a, b) in enumerate(pairs)])
        order = [order[0], order[-1], *order[1:-1]]
    return rounds + [[(b, a) for a, b in rnd] for rnd in rounds]


def _new_league(rng: np.random.Generator, quality_sd: float = QUALITY_SD) -> _League:
    league = _League(rng)
    for i, (name, short, abbr) in enumerate(CLUBS):
        # One quality factor drives both sides of the game (good clubs score more AND concede less),
        # plus a smaller independent "style" component.
        quality = float(rng.normal(0.0 if i < TOP_FLIGHT else -PROMOTED_SHIFT, quality_sd))
        attack = _bounded(quality + float(rng.normal(0, STYLE_SD)))
        defence = _bounded(-quality + float(rng.normal(0, STYLE_SD)))
        league.clubs[name] = _Club(name, short, abbr, attack, defence, attack, defence)
    league.members = [c[0] for c in CLUBS[:TOP_FLIGHT]]
    league.reserve = [c[0] for c in CLUBS[TOP_FLIGHT:]]
    return league


def _stats(rng: np.random.Generator, rate: float, opp_rate: float, goals: int) -> dict[str, float]:
    shots = int(rng.poisson(6.0 + 5.5 * rate))
    on_target = max(goals, int(rng.binomial(shots, 0.34)))
    shots = max(shots, on_target)
    in_box = max(goals, int(rng.binomial(shots, 0.64)))
    big_chances = int(rng.poisson(0.9 * rate))
    share = float(np.clip(50 + 18 * np.tanh(rate - opp_rate) + rng.normal(0, 4), 28, 72))
    return {
        "totalScoringAtt": shots,
        "ontargetScoringAtt": on_target,
        "attemptsIbox": in_box,
        "bigChanceCreated": big_chances,
        "touchesInOppBox": int(rng.poisson(9 + 9 * rate)),
        "wonCorners": int(rng.poisson(3.2 + 1.4 * rate)),
        "totalPass": int(rng.poisson(9 * share)),
        "possessionPercentage": round(share, 1),
    }


def _play_season(
    league: _League, season: int, as_of: datetime
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = league.rng
    teams = sorted(league.members)
    index = {t: i for i, t in enumerate(teams)}
    kickoff_week = _season_start(season)
    matches, stats, truth = [], [], []

    for week, fixtures in enumerate(_round_robin(teams, rng), start=1):
        for (home, away), (day, slot) in zip(fixtures, SLOTS, strict=True):
            h, a = league.clubs[home], league.clubs[away]
            kickoff = datetime.combine(kickoff_week + timedelta(days=day), slot)
            lam = float(np.exp(INTERCEPT + HOME_ADVANTAGE + h.attack + a.defence))
            nu = float(np.exp(INTERCEPT + a.attack + h.defence))
            mid = fixture_id(season, index[home], index[away])
            played = kickoff < as_of
            hg, ag = (int(rng.poisson(lam)), int(rng.poisson(nu))) if played else (None, None)

            matches.append(
                {
                    "match_id": mid,
                    "date": kickoff.strftime("%Y-%m-%d %H:%M:%S"),
                    "matchweek": week,
                    "status": "finished" if played else "scheduled",
                    "home_team": home,
                    "away_team": away,
                    "home_short_name": h.short,
                    "away_short_name": a.short,
                    "home_abbr": h.abbr,
                    "away_abbr": a.abbr,
                    "home_score": hg,
                    "away_score": ag,
                    "home_ht_score": None if hg is None else int(rng.binomial(hg, 0.45)),
                    "away_ht_score": None if ag is None else int(rng.binomial(ag, 0.45)),
                    "home_red_cards": None if hg is None else int(rng.random() < 0.05),
                    "away_red_cards": None if ag is None else int(rng.random() < 0.06),
                    "stadium": f"{h.short} Park",
                    "attendance": None if hg is None else int(rng.normal(30_000, 6_000)),
                    "season": season,
                }
            )
            probs = _outcome_probabilities(lam, nu)
            truth.append({"match_id": mid, "home_rate": lam, "away_rate": nu, **probs})
            if hg is not None and ag is not None:
                home_stats, away_stats = _stats(rng, lam, nu, hg), _stats(rng, nu, lam, ag)
                away_stats["possessionPercentage"] = round(100 - home_stats["possessionPercentage"], 1)
                row: dict[str, object] = {"match_id": mid, "home_team": home, "away_team": away}
                row.update({f"home_{k}": v for k, v in home_stats.items()})
                row.update({f"away_{k}": v for k, v in away_stats.items()})
                stats.append(row)

        for club in teams:  # strengths drift through the season
            c = league.clubs[club]
            c.attack = _revert(c.attack, c.level_attack, rng)
            c.defence = _revert(c.defence, c.level_defence, rng)
        kickoff_week += timedelta(days=14 if week in BREAKS_AFTER else 7)

    return (
        pd.DataFrame(matches, columns=MATCH_COLUMNS),
        pd.DataFrame(stats),
        pd.DataFrame(truth),
    )


def _revert(form: float, level: float, rng: np.random.Generator) -> float:
    return _bounded(level + (1 - WEEKLY_REVERSION) * (form - level) + float(rng.normal(0, WEEKLY_DRIFT_SD)))


def _bounded(value: float) -> float:
    return float(np.clip(value, -STRENGTH_BOUND, STRENGTH_BOUND))


def _outcome_probabilities(lam: float, nu: float, max_goals: int = 20) -> dict[str, float]:
    goals = np.arange(max_goals + 1)
    grid = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, nu))
    grid /= grid.sum()  # the tail beyond max_goals is negligible but renormalise anyway
    return {
        "p_home": float(np.tril(grid, -1).sum()),
        "p_draw": float(np.trace(grid)),
        "p_away": float(np.triu(grid, 1).sum()),
    }


def _end_of_season(league: _League, table: pd.Series) -> None:
    """Relegate the bottom three, promote three reserve clubs, and apply summer changes."""
    rng = league.rng
    relegated = list(table.sort_values().index[:3])
    promoted = list(rng.choice(league.reserve, size=3, replace=False))
    league.members = [t for t in league.members if t not in relegated] + promoted
    league.reserve = [t for t in league.reserve if t not in promoted] + relegated
    for club in league.clubs.values():
        change = float(rng.normal(0, SUMMER_SHOCK_SD))  # a better squad scores more and concedes less
        club.level_attack = _bounded(SUMMER_SHRINK * club.level_attack + change)
        club.level_defence = _bounded(SUMMER_SHRINK * club.level_defence - change)
        club.attack, club.defence = club.level_attack, club.level_defence  # a new season starts at its level
    for name in promoted:  # a season in the second tier costs quality relative to the top flight
        club = league.clubs[name]
        club.level_attack = club.attack = min(club.level_attack, -PROMOTED_SHIFT / 2)
        club.level_defence = club.defence = max(club.level_defence, PROMOTED_SHIFT / 2)


def _points(matches: pd.DataFrame) -> pd.Series:
    played = matches[matches["status"] == "finished"]
    points: dict[str, int] = {}
    for home, away, hg, ag in played[["home_team", "away_team", "home_score", "away_score"]].itertuples(
        index=False
    ):
        points[home] = points.get(home, 0) + (3 if hg > ag else 1 if hg == ag else 0)
        points[away] = points.get(away, 0) + (3 if ag > hg else 1 if hg == ag else 0)
    return pd.Series(points, dtype=float)


def generate(
    settings: Settings, seed: int = SEED, as_of: datetime = AS_OF, quality_sd: float = QUALITY_SD
) -> list[int]:
    """Write snapshot CSVs for every configured season; returns the seasons written."""
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    league = _new_league(np.random.default_rng(seed), quality_sd)
    seasons = sorted(settings.seasons)
    for i, season in enumerate(seasons):
        matches, stats, truth = _play_season(league, season, as_of)
        matches.to_csv(matches_path(settings, season), index=False, lineterminator="\n")
        stats.to_csv(stats_path(settings, season), index=False, lineterminator="\n")
        truth.round(6).to_csv(truth_path(settings, season), index=False, lineterminator="\n")
        log.info("Demo %d: %d fixtures, %d played", season, len(matches), len(stats))
        if i < len(seasons) - 1:
            _end_of_season(league, _points(matches))
    return seasons
