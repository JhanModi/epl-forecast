"""FastAPI application factory.

`create_app()` wires settings, state, middleware and routers. Building the app
in a function (rather than at import time) lets tests create isolated
instances pointed at a temporary database.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from epl import __version__
from epl.api import schemas
from epl.api.middleware import RequestContextMiddleware, install_error_handlers
from epl.api.routers import admin, models, predictions, seasons, system, teams
from epl.api.state import AppState
from epl.core.config import Settings, get_settings
from epl.core.logging import configure_logging
from epl.services import jobs

API_PREFIX = "/api/v1"
log = logging.getLogger(__name__)

DESCRIPTION = """
Forecasts for the English Premier League from a time-weighted Dixon-Coles goals
model, with walk-forward evaluation and Monte Carlo season simulation.

All probabilities are produced *before kickoff* from data available at the time.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.epl = AppState(settings, check_interval=settings.cache_check_interval_seconds)
        try:  # jobs whose process died while running them are marked failed
            jobs.expire_stale(settings.database_url)
        except SQLAlchemyError as exc:  # e.g. migrations not applied yet; /ready will report it
            log.warning("Could not check for interrupted jobs: %s", exc)
        yield

    app = FastAPI(
        title="EPL Forecast API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    # Every error is an RFC 9457 problem document; declaring it puts the shape in the OpenAPI contract.
    errors: dict[int | str, dict[str, Any]] = {
        status: {"model": schemas.Problem, "description": description}
        for status, description in (
            (404, "Not found"),
            (422, "Invalid request"),
            (503, "Model not available"),
        )
    }
    for router in (
        system.router,
        teams.router,
        seasons.router,
        predictions.router,
        models.router,
        admin.router,
    ):
        app.include_router(router, prefix=API_PREFIX, responses=errors)

    _mount_dashboard(app, settings)
    return app


def _mount_dashboard(app: FastAPI, settings: Settings) -> None:
    """Serve the built React dashboard from the same origin, with SPA fallback routing."""
    dist = settings.frontend_dist
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, f"No API route /{path}")  # unknown API paths stay JSON 404s
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)  # favicon, robots.txt, ...
        return FileResponse(dist / "index.html")
