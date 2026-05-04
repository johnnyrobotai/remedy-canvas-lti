"""LTI 1.3 route handlers.

POST /lti/login  — OIDC third-party initiation
POST /lti/launch — Launch callback with id_token
GET  /lti/jwks   — Tool public JWKS
GET  /api/session — Current session info for frontend
"""

import structlog
from fastapi import APIRouter, Depends, Request
from starlette.responses import RedirectResponse

from lti_app.auth.dependencies import CurrentSession
from lti_app.auth.session import (
    create_bypass_session,
    create_session,
    create_session_token,
)
from lti_app.config import Settings, get_settings
from lti_app.db.repositories import (
    RegistrationRepository,
    SessionRepository,
    get_registration_repository,
    get_session_repository,
)
from lti_app.lti.jwks import get_tool_jwks
from lti_app.lti.launch import handle_launch_callback, handle_oidc_login
from lti_app.lti.models import LTISessionResponse
from lti_app.lti.token_store import DictLaunchDataStorage
from lti_app.lti.tool_conf import FirestoreToolConf

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["lti"])

# Module-level singletons (shared across requests)
_dict_storage = DictLaunchDataStorage()
_tool_conf: FirestoreToolConf | None = None


def get_tool_conf() -> FirestoreToolConf:
    """Return cached ToolConf singleton. Initialized during app lifespan."""
    if _tool_conf is None:
        raise RuntimeError("ToolConf not initialized. App lifespan not started.")
    return _tool_conf


def initialize_tool_conf(reg_repo: RegistrationRepository) -> None:
    """Initialize and cache the ToolConf singleton. Called from app lifespan."""
    global _tool_conf
    _tool_conf = FirestoreToolConf(reg_repo)
    _tool_conf.preload_all()
    _logger.info("tool_conf_initialized", registrations=len(_tool_conf.get_all_registrations_dicts()))


def _get_launch_storage(request: Request, settings: Settings) -> DictLaunchDataStorage:
    """Return in-memory launch data storage (ephemeral, lives seconds)."""
    return _dict_storage


# --- LTI Routes ---


@router.post("/lti/login")
async def oidc_login(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """OIDC third-party initiation. Canvas POSTs here first."""
    if settings.auth_bypass_for_local:
        # In bypass mode, course_id is NOT in the OIDC login POST form data.
        # Canvas only sends iss, login_hint, client_id, lti_message_hint, etc.
        # The course_id arrives in the signed id_token during the full OIDC flow.
        # For bypass, extract from Referer header or decode lti_message_hint JWT.
        import re
        import base64
        import json as _json

        course_id = ""

        # Strategy 1: Referer header (Canvas sends the referring page URL)
        referer = request.headers.get("referer", "")
        ref_match = re.search(r"/courses/(\d+)", referer)
        if ref_match:
            course_id = ref_match.group(1)

        # Strategy 2: Decode lti_message_hint JWT without verification
        if not course_id:
            form = await request.form()
            hint = str(form.get("lti_message_hint", ""))
            if hint and "." in hint:
                try:
                    payload_b64 = hint.split(".")[1]
                    padding = 4 - len(payload_b64) % 4
                    if padding != 4:
                        payload_b64 += "=" * padding
                    payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
                    _logger.info("lti_message_hint_decoded", payload_keys=list(payload.keys()), payload=str(payload)[:500])
                    # Canvas lti_message_hint contains context_id as a large internal ID
                    # e.g. 10000000000003 for course 3. Extract the real course ID.
                    raw_id = (
                        payload.get("canvas_course_id")
                        or payload.get("course_id")
                        or payload.get("context_id")
                    )
                    if raw_id and payload.get("context_type") == "Course":
                        raw_id = int(raw_id)
                        # Canvas internal IDs have a 10000000000000 prefix
                        if raw_id > 10000000000000:
                            course_id = str(raw_id - 10000000000000)
                        else:
                            course_id = str(raw_id)
                except Exception as e:
                    _logger.warning("lti_message_hint_decode_failed", error=str(e))

        _logger.info("lti_login_bypass", course_id=course_id, referer=referer[:100])
        redirect_url = f"/lti/launch?bypass=true&course_id={course_id}" if course_id else "/lti/launch?bypass=true"
        return RedirectResponse(url=redirect_url, status_code=302)

    tool_conf = get_tool_conf()
    storage = _get_launch_storage(request, settings)
    return await handle_oidc_login(request, tool_conf, storage, settings)


@router.post("/lti/launch")
async def lti_launch(
    request: Request,
    settings: Settings = Depends(get_settings),
    session_repo: SessionRepository = Depends(get_session_repository),
):
    """LTI launch callback. Canvas POSTs id_token here."""
    if settings.auth_bypass_for_local:
        form = await request.form()
        course_id_str = form.get("custom_canvas_course_id", "")
        course_id = int(course_id_str) if str(course_id_str).isdigit() else None
        token, _ = create_bypass_session(session_repo, settings, course_id=course_id)
        response = RedirectResponse(url="/dashboard", status_code=302)
        _set_session_cookie(response, token, request, settings)
        return response

    tool_conf = get_tool_conf()
    storage = _get_launch_storage(request, settings)
    claims, response = await handle_launch_callback(
        request, tool_conf, storage, settings
    )

    token, session_id = create_session(session_repo, claims, settings)

    # Check if Canvas OAuth2 token is needed
    if settings.canvas_oauth2_client_id:
        from lti_app.canvas.oauth2 import get_authorization_url

        oauth2_redirect_uri = str(request.url_for("oauth2_callback"))
        auth_url = get_authorization_url(
            session=type("S", (), {"session_id": session_id, "canvas_base_url": claims.canvas_base_url})(),
            client_id=settings.canvas_oauth2_client_id,
            redirect_uri=oauth2_redirect_uri,
        )
        oauth2_response = RedirectResponse(url=auth_url, status_code=302)
        _set_session_cookie(oauth2_response, token, request, settings)
        return oauth2_response

    _set_session_cookie(response, token, request, settings)
    return response


@router.get("/lti/launch")
async def lti_launch_bypass(
    request: Request,
    settings: Settings = Depends(get_settings),
    session_repo: SessionRepository = Depends(get_session_repository),
):
    """GET handler for local dev bypass redirect from /lti/login."""
    if not settings.auth_bypass_for_local:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    # Extract course_id from query param (passed by oidc_login bypass)
    course_id_str = request.query_params.get("course_id", "")
    course_id = int(course_id_str) if course_id_str.isdigit() else None

    token, _ = create_bypass_session(session_repo, settings, course_id=course_id)
    response = RedirectResponse(url="/dashboard", status_code=302)
    _set_session_cookie(response, token, request, settings)
    return response


@router.get("/lti/jwks")
async def jwks_endpoint():
    """Tool's public JWKS endpoint for Canvas to verify service tokens."""
    tool_conf = get_tool_conf()
    return get_tool_jwks(tool_conf)


# --- API Routes ---


@router.get("/api/session")
async def get_session_info(
    session: CurrentSession,
    settings: Settings = Depends(get_settings),
):
    """Return current session data for frontend LTIContext."""
    token = create_session_token(
        session.session_id, session.expires_at, settings.session_secret_key
    )
    return LTISessionResponse.from_session(session, token)


# --- Helpers ---


def _set_session_cookie(response, token: str, request: Request, settings: Settings) -> None:
    # LTI tools run in iframes (cross-origin), always need SameSite=None + Secure
    # for the session cookie to be sent back by the browser
    response.set_cookie(
        key="lti_session",
        value=token,
        httponly=True,
        samesite="none",
        secure=True,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
