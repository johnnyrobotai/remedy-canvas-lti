"""Canvas OAuth2 callback and URL endpoints."""

from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import RedirectResponse

from lti_app.auth.dependencies import CurrentSession
from lti_app.canvas.oauth2 import exchange_code, get_authorization_url
from lti_app.config import Settings, get_settings
from lti_app.db.repositories import SessionRepository, get_session_repository

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/oauth2", tags=["oauth2"])


@router.get("/url")
async def oauth2_url(
    request: Request,
    session: CurrentSession,
    settings: Settings = Depends(get_settings),
):
    """Return the Canvas OAuth2 authorization URL for this session."""
    redirect_uri = str(request.url_for("oauth2_callback"))
    url = get_authorization_url(
        session=session,
        client_id=settings.canvas_oauth2_client_id,
        redirect_uri=redirect_uri,
    )
    return {"url": url}


@router.get("/callback", name="oauth2_callback")
async def oauth2_callback(
    request: Request,
    code: str = "",
    state: str = "",
    settings: Settings = Depends(get_settings),
    session_repo: SessionRepository = Depends(get_session_repository),
):
    """Handle Canvas OAuth2 callback — exchange code for tokens."""
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    session_data = session_repo.get_session(state)
    if not session_data:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    canvas_base_url = session_data.get("canvas_base_url", "")
    if not canvas_base_url:
        raise HTTPException(status_code=400, detail="Missing canvas_base_url in session")

    redirect_uri = str(request.url_for("oauth2_callback"))

    tokens = await exchange_code(
        code=code,
        canvas_base_url=canvas_base_url,
        client_id=settings.canvas_oauth2_client_id,
        client_secret=settings.canvas_oauth2_client_secret,
        redirect_uri=redirect_uri,
    )

    session_data["canvas_access_token"] = tokens.access_token
    session_data["canvas_refresh_token"] = tokens.refresh_token
    session_data["token_expires_at"] = (
        datetime.now(UTC) + timedelta(seconds=tokens.expires_in)
    ).isoformat()
    session_repo.save_session(state, session_data)

    _logger.info("oauth2_tokens_stored", session_id=state)
    return RedirectResponse(url="/dashboard", status_code=302)
