"""FastAPI dependencies (injected per request, overridable in tests)."""

import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from epl.api.state import AppState
from epl.data.db import session_factory


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.epl
    return state


def get_session(state: Annotated[AppState, Depends(get_state)]) -> Iterator[Session]:
    session = session_factory(state.settings.database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_admin(
    state: Annotated[AppState, Depends(get_state)],
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = state.settings.admin_token
    if not expected:
        raise HTTPException(403, "Admin endpoints are disabled (EPL_ADMIN_TOKEN is not set)")
    # Constant-time comparison (on bytes), so the token cannot be guessed from response timing.
    provided = (x_admin_token or "").encode()
    if not hmac.compare_digest(provided, expected.get_secret_value().encode()):
        raise HTTPException(401, "Invalid admin token")


StateDep = Annotated[AppState, Depends(get_state)]
SessionDep = Annotated[Session, Depends(get_session)]
