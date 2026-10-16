"""Command-line entry point: `epl --help`."""

import json
import logging
from typing import Annotated

import pandas as pd
import typer
from pydantic import ValidationError

from epl.core.config import get_settings, season_name
from epl.core.logging import configure_logging

app = typer.Typer(help="Premier League forecasting platform.", no_args_is_help=True, add_completion=False)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
app.add_typer(db_app, name="db")

Seasons = Annotated[list[int] | None, typer.Option("--season", "-s", help="Season start year; repeatable.")]
log = logging.getLogger("epl")


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:  # misconfiguration: say what is wrong instead of a traceback
        for error in exc.errors():
            field = "EPL_" + "_".join(str(part) for part in error["loc"]).upper()
            typer.echo(f"Configuration error in {field}: {error['msg']}", err=True)
        raise typer.Exit(2) from None
    configure_logging("DEBUG" if verbose else settings.log_level, settings.log_json)
    logging.getLogger("epl.evaluation.backtest").setLevel(logging.WARNING if not verbose else logging.DEBUG)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all pending migrations."""
    from epl.data import migrate

    migrate.upgrade(get_settings().database_url)
    typer.echo("Database is up to date.")


@db_app.command("check")
def db_check() -> None:
    """Fail if the ORM models have drifted from the migrations."""
    from epl.data import migrate

    migrate.check(get_settings().database_url)
    typer.echo("Models and migrations are in sync.")


@app.command()
def extract(
    seasons: Seasons = None,
    skip_stats: Annotated[bool, typer.Option(help="Only refresh fixtures and results.")] = False,
) -> None:
    """Download fixtures, results and match statistics from the Premier League API."""
    from epl.services import pipeline

    settings = get_settings()
    for report in pipeline.extract(settings, seasons or [settings.current_season], not skip_stats):
        typer.echo(
            f"{season_name(report.season)}: {report.fixtures} fixtures, {report.finished} finished, "
            f"{report.new_stats} new stat sheets"
        )


@app.command()
def load(seasons: Seasons = None) -> None:
    """Validate the raw CSV snapshots and upsert them into the database."""
    from epl.services import pipeline

    report = pipeline.load(get_settings(), seasons)
    typer.echo(f"Loaded {report.teams} teams, {report.matches} matches, {report.stats} team-match stat rows.")


@app.command()
def tune(season: Annotated[int | None, typer.Option(help="Defaults to the tuning season.")] = None) -> None:
    """Grid-search hyperparameters on the tuning season and save them."""
    from epl.data.db import session_scope
    from epl.data.repository import match_frame
    from epl.evaluation import tuning
    from epl.models import zoo

    settings = get_settings()
    season = season or settings.tuning_season
    with session_scope(settings.database_url) as session:
        frame = match_frame(session)
    result = tuning.tune(frame[frame["season"] <= season], season)  # later seasons are never visible
    path = zoo.params_path(settings.artifacts_dir)
    tuning.save(result, path)
    typer.echo(f"Saved to {path}")
    typer.echo(json.dumps(result["best"], indent=2))


@app.command()
def backtest(seasons: Seasons = None) -> None:
    """Walk-forward evaluation of every model; writes artifacts/backtest/report.json."""
    from epl.services import pipeline

    settings = get_settings()
    seasons = seasons or [settings.tuning_season, settings.holdout_season, settings.current_season]
    report = pipeline.run_backtest(settings, seasons)
    for season, result in report["seasons"].items():
        rows = {
            m: {k: v["mean"] for k, v in result["models"][m]["metrics"].items()}
            | {"p_vs_baseline": result["models"][m]["vs_reference"]["p_value"]}
            for m in result["ranking"]
        }
        typer.echo(f"\n{season_name(int(season))}  ({result['n_matches']} matches)")
        typer.echo(pd.DataFrame(rows).T.round(4).to_string())


@app.command()
def train(
    if_missing: Annotated[
        bool, typer.Option(help="Skip if a production model is already registered.")
    ] = False,
) -> None:
    """Fit every model on all finished matches and register it."""
    from epl.models.registry import ModelRegistry
    from epl.services import pipeline
    from epl.services.forecasting import PRODUCTION_MODEL

    settings = get_settings()
    if if_missing:
        try:
            version = ModelRegistry(settings.artifacts_dir).latest_version(PRODUCTION_MODEL)
        except FileNotFoundError:
            pass
        else:
            typer.echo(f"{PRODUCTION_MODEL} {version} already registered; skipping training.")
            return
    for card in pipeline.train(settings):
        typer.echo(
            f"{card.name:22s} {card.version}  ({card.n_matches} matches through {card.trained_through})"
        )


@app.command()
def predict(days: Annotated[int, typer.Option(help="Forecast fixtures within this many days.")] = 7) -> None:
    """Log the production model's forecasts for upcoming fixtures."""
    from epl.data.db import session_scope
    from epl.services import pipeline

    settings = get_settings()
    with session_scope(settings.database_url) as session:
        n = pipeline.log_predictions(session, settings, days)
    typer.echo(f"Logged {n} new forecasts.")


@app.command()
def simulate(
    season: Annotated[int | None, typer.Option()] = None,
    as_of: Annotated[int | None, typer.Option(help="Simulate from the start of this matchweek.")] = None,
    runs: Annotated[int, typer.Option()] = 10_000,
) -> None:
    """Monte Carlo the rest of a season and print title / top-four / relegation odds."""
    from epl.data.db import session_scope
    from epl.data.repository import match_frame
    from epl.services.forecasting import season_simulation

    settings = get_settings()
    season = season or settings.current_season
    with session_scope(settings.database_url) as session:
        frame = match_frame(session)
    from epl.models import zoo

    result = season_simulation(frame, season, as_of, runs, params=zoo.tuned(settings))
    table = pd.DataFrame(result["teams"])[
        ["team", "points", "expected_points", "p_title", "p_top_four", "p_relegation"]
    ]
    typer.echo(f"{season_name(season)} after {result['matches_played']} matches, {runs:,} simulations\n")
    typer.echo(table.round(3).to_string(index=False))


@app.command()
def refresh() -> None:
    """Weekly job: pull the latest results, reload, retrain and log new forecasts."""
    from epl.data.db import session_scope
    from epl.services import pipeline

    settings = get_settings()
    pipeline.extract(settings, [settings.current_season])
    pipeline.load(settings)
    pipeline.train(settings)
    with session_scope(settings.database_url) as session:
        n = pipeline.log_predictions(session, settings)
    typer.echo(f"Refresh complete; logged {n} new forecasts.")


@app.command()
def build(skip_backtest: Annotated[bool, typer.Option()] = False) -> None:
    """Rebuild everything from the committed CSV snapshots (no network)."""
    from epl.data.db import session_scope
    from epl.services import pipeline

    settings = get_settings()
    pipeline.load(settings)
    if not skip_backtest:
        pipeline.run_backtest(
            settings, [settings.tuning_season, settings.holdout_season, settings.current_season]
        )
    pipeline.train(settings)
    with session_scope(settings.database_url) as session:
        pipeline.log_predictions(session, settings)
    typer.echo("Build complete.")


@app.command()
def openapi(
    out: Annotated[str, typer.Option(help="Where to write the schema.")] = "docs/openapi.json",
) -> None:
    """Export the OpenAPI schema (the TypeScript client is generated from it)."""
    from pathlib import Path

    from epl.api.main import create_app

    schema = create_app().openapi()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    typer.echo(f"Wrote {out} ({len(schema['paths'])} paths)")


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the REST API (and the built dashboard, if present)."""
    import uvicorn

    # The API middleware writes a structured access log, so uvicorn's own is switched off.
    uvicorn.run(
        "epl.api.main:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    app()
