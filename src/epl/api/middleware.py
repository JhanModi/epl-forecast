"""Cross-cutting HTTP concerns: request IDs, timing, access logs and error responses."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from epl.core.logging import request_id

log = logging.getLogger("epl.api")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tag each request with an ID (honouring an incoming X-Request-ID) and log it with its latency."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        if request.url.path.startswith("/api"):
            log.info(
                "%s %s -> %d (%.1f ms)",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                extra={
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed_ms, 1),
                },
            )
        return response


def _problem(
    status: int, title: str, detail: str | None, headers: dict[str, str] | None = None
) -> JSONResponse:
    """RFC 9457 problem-details body."""
    return JSONResponse(
        {
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "request_id": request_id.get(),
        },
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        title = str(exc.detail) if exc.status_code < 500 else "Server error"
        return _problem(exc.status_code, title, str(exc.detail), getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _problem(422, "Invalid request", detail)

    @app.exception_handler(FileNotFoundError)
    async def model_missing(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return _problem(503, "Model not available", str(exc))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error")
        return _problem(500, "Internal server error", None)
