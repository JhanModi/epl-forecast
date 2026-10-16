"""Data sources and distribution rules."""

import hashlib
import itertools
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest
import requests

from epl.core.config import Settings
from epl.data.datasets import DATASETS
from epl.data.load import STAT_COLUMNS
from epl.data.sources import openfootball, synthetic
from epl.data.validate import validate_matches
from epl.models import zoo
from epl.services import pipeline
from scripts.check_distribution import forbidden
from tests import factories

ROOT = Path(__file__).resolve().parents[1]


def digest(directory: Path) -> str:
    sha = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        sha.update(path.name.encode() + path.read_bytes())
    return sha.hexdigest()


# --------------------------------------------------------------------------- synthetic demo league
@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    settings = Settings(raw_dir=tmp_path_factory.mktemp("demo"))
    synthetic.generate(settings)
    return settings


def test_demo_generation_is_deterministic(demo: Settings, tmp_path: Path) -> None:
    again = Settings(raw_dir=tmp_path)
    synthetic.generate(again)

    assert digest(demo.raw_dir) == digest(again.raw_dir)


def test_committed_demo_data_matches_the_generator(demo: Settings) -> None:
    committed = ROOT / "data" / "demo"
    if not committed.exists():
        pytest.skip("demo data not generated in this checkout")
    assert digest(committed) == digest(demo.raw_dir), "regenerate with `EPL_DATASET=demo epl extract`"


def test_demo_league_passes_validation_and_looks_like_football(demo: Settings) -> None:
    matches = pd.concat(
        [pd.read_csv(p) for p in sorted(demo.raw_dir.glob("matches_*.csv"))], ignore_index=True
    )
    assert validate_matches(matches, 20).errors == []

    played = matches[matches["status"] == "finished"]
    home_wins = (played["home_score"] > played["away_score"]).mean()
    draws = (played["home_score"] == played["away_score"]).mean()
    goals = (played["home_score"] + played["away_score"]).mean()
    assert 0.38 < home_wins < 0.50 and 0.18 < draws < 0.32 and 2.4 < goals < 3.4


def test_demo_has_promotion_and_relegation(demo: Settings) -> None:
    rosters = [set(pd.read_csv(p)["home_team"]) for p in sorted(demo.raw_dir.glob("matches_*.csv"))]

    for before, after in itertools.pairwise(rosters):
        assert len(after - before) == 3


def test_demo_truth_probabilities_are_valid(demo: Settings) -> None:
    truth = pd.read_csv(next(demo.raw_dir.glob("truth_*.csv")))

    assert ((truth[["p_home", "p_draw", "p_away"]].sum(axis=1) - 1).abs() < 1e-3).all()


def test_demo_clubs_are_fictional() -> None:
    real = set(openfootball.CLUB_ABBREVIATIONS)
    assert not real & {name for name, _, _ in synthetic.CLUBS}


# --------------------------------------------------------------------------- openfootball adapter
# A hand-written document in football.json's shape (fictional teams; no third-party data copied).
def _fixture(round_: int, date: str, home: str, away: str, score: object = None) -> dict[str, object]:
    match: dict[str, object] = {
        "round": f"Matchday {round_}",
        "date": date,
        "time": "15:00",
        "team1": home,
        "team2": away,
    }
    if score is not None:
        match["score"] = score
    return match


FOOTBALL_JSON = {
    "name": "Example League 2026/27",
    "matches": [
        _fixture(1, "2026-08-21", "Alpha FC", "AFC Beta", {"ht": [1, 0], "ft": [2, 1]}),
        _fixture(1, "2026-08-22", "Gamma Town AFC", "Delta and Echo FC", [0, 0]),
        _fixture(2, "2026-08-29", "AFC Beta", "Alpha FC"),
    ],
}


def test_openfootball_documents_become_snapshot_rows() -> None:
    frame, warnings = openfootball.parse_season(FOOTBALL_JSON, 2026)

    assert frame["home_team"].tolist() == ["Alpha", "Gamma Town", "Beta"]
    assert frame["away_team"].tolist()[1] == "Delta & Echo"
    assert frame["status"].tolist() == ["finished", "finished", "scheduled"]
    assert frame.loc[0, ["home_score", "away_score", "home_ht_score"]].tolist() == [2, 1, 1]
    assert frame["matchweek"].tolist() == [1, 1, 2]
    assert frame["match_id"].is_unique
    assert len(warnings) == 1 and "breakdown" in warnings[0]


def test_openfootball_ids_are_stable_when_a_fixture_moves() -> None:
    moved = {**FOOTBALL_JSON, "matches": [dict(m) for m in FOOTBALL_JSON["matches"]]}
    moved["matches"][2]["date"] = "2026-09-30"  # rescheduled

    original, _ = openfootball.parse_season(FOOTBALL_JSON, 2026)
    rescheduled, _ = openfootball.parse_season(moved, 2026)

    assert set(original["match_id"]) == set(rescheduled["match_id"])


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Arsenal FC", "Arsenal"),
        ("AFC Bournemouth", "Bournemouth"),
        ("Brighton and Hove Albion FC", "Brighton & Hove Albion"),
    ],
)
def test_club_names_are_normalised(raw: str, name: str) -> None:
    assert openfootball.club_name(raw) == name


def test_results_only_datasets_train_without_stats_features() -> None:
    frame = factories.match_frame()
    stats = [f"{side}_{stat}" for side in ("home", "away") for stat in STAT_COLUMNS]
    frame[stats] = float("nan")

    model = zoo.build("logistic_regression").fit(frame.iloc[:-5])

    assert not any("shots" in f or "possession" in f for f in model.params()["features"])  # type: ignore[union-attr]
    assert model.predict_proba(frame.iloc[-5:]).shape == (5, 3)


# --------------------------------------------------------------------------- distribution rules
def test_restricted_paths_are_forbidden() -> None:
    assert forbidden("data/premier-league/matches_2023_24.csv")
    assert forbidden("data/artifacts/premier-league/backtest/report.json")
    assert forbidden("data/demo.db")
    assert forbidden("data/demo/matches_2023_24.csv") is None
    assert forbidden("data/artifacts/demo/backtest/report.json") is None


def test_local_only_material_is_forbidden() -> None:
    for path in [
        ".env",
        "frontend/.env.local",
        "MEMORY.md",
        "SESSION_HANDOFF.md",
        ".claude/settings.local.json",
        "archive/v0/src/collect.py",
        "venv/pyvenv.cfg",
        "NEU_faculty.xlsx",
        "docs/review/EVIDENCE_LOG.md",
    ]:
        assert forbidden(path), path
    for path in [
        ".env.example",
        "README.md",
        "docs/ARCHITECTURE.md",
        "src/epl/data/sources/premier_league.py",
    ]:
        assert forbidden(path) is None, path


def test_every_dataset_declares_its_terms() -> None:
    for info in DATASETS.values():
        assert info.license and info.notes
        assert info.redistributable in (True, False, None)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_ignores_restricted_data() -> None:
    paths = [
        "data/premier-league/matches_2023_24.csv",
        "data/artifacts/premier-league/models/x/latest.json",
        "data/premier-league.db",
        "data/artifacts/demo/models/dixon_coles/latest.json",
    ]
    ignored = subprocess.run(
        ["git", "check-ignore", *paths], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout

    assert set(ignored.split()) == set(paths)
    shareable = subprocess.run(
        ["git", "check-ignore", "data/demo/matches_2023_24.csv"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shareable.returncode == 1  # not ignored: the demo dataset ships with the repository


def test_dockerignore_mirrors_the_allow_list() -> None:
    rules = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()

    assert "data/*" in rules and "!data/demo" in rules
    assert not any(rule.startswith("!data/premier-league") for rule in rules)


def test_demo_build_needs_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(requests.Session, "request", no_network)
    settings = Settings(
        raw_dir=tmp_path / "raw",
        database_url=f"sqlite:///{(tmp_path / 'demo.db').as_posix()}",
        artifacts_dir=tmp_path / "artifacts",
        seasons=[2023, 2024],
        current_season=2024,
    )
    pipeline.extract(settings, settings.seasons)  # demo "extraction" generates offline
    report = pipeline.load(settings)

    assert report.matches == 760


def test_shell_scripts_use_lf_line_endings() -> None:
    """Regression: a CRLF entrypoint.sh made the container fail with 'exec ... no such file or directory'."""
    for script in ROOT.glob("docker/*.sh"):
        assert b"\r" not in script.read_bytes(), f"{script.name} has CRLF line endings"


def test_generated_files_are_byte_identical_across_platforms(demo: Settings) -> None:
    """Writers emit LF explicitly, so the demo snapshot is the same bytes on Windows and Linux."""
    for path in demo.raw_dir.iterdir():
        assert b"\r" not in path.read_bytes(), path.name
