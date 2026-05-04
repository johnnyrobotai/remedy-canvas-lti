"""LTI 1.3 launch business logic.

handle_oidc_login() — OIDC third-party initiation (Canvas POSTs here first)
handle_launch_callback() — Launch callback (Canvas POSTs id_token here)

These are called from api/routes/lti.py route handlers.
"""

import base64
import json

import structlog
from fastapi import Request
from starlette.responses import RedirectResponse, Response

from lti_app.config import Settings
from lti_app.lti.adapters import (
    FastAPICookieService,
    FastAPIMessageLaunch,
    FastAPIOIDCLogin,
    FastAPIRequest,
)
from lti_app.lti.models import LTIClaims
from lti_app.lti.tool_conf import FirestoreToolConf

_logger = structlog.get_logger(__name__)

# LTI claim URIs
_CLAIM_DEPLOYMENT = "https://purl.imsglobal.org/spec/lti/claim/deployment_id"
_CLAIM_ROLES = "https://purl.imsglobal.org/spec/lti/claim/roles"
_CLAIM_CONTEXT = "https://purl.imsglobal.org/spec/lti/claim/context"
_CLAIM_CUSTOM = "https://purl.imsglobal.org/spec/lti/claim/custom"
_CLAIM_TARGET_LINK = "https://purl.imsglobal.org/spec/lti/claim/target_link_uri"
_CLAIM_RESOURCE_LINK = "https://purl.imsglobal.org/spec/lti/claim/resource_link"


async def handle_oidc_login(
    request: Request,
    tool_conf: FirestoreToolConf,
    launch_data_storage,
    settings: Settings,
) -> Response:
    """OIDC third-party initiation. Returns redirect to Canvas auth endpoint."""
    from pylti1p3.session import SessionService

    form_data = dict(await request.form())

    # FastAPIRequest merges query_params internally — pass only form_data
    lti_request = FastAPIRequest(request, form_data=form_data)
    cookie_service = FastAPICookieService(request)
    session_service = SessionService(lti_request)

    oidc = FastAPIOIDCLogin(
        lti_request,
        tool_conf,
        session_service,
        cookie_service,
        launch_data_storage,
    )

    # Use X-Forwarded headers from reverse proxy, or fall back to request.base_url
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    forwarded_host = request.headers.get("x-forwarded-host", "") or request.headers.get("host", "")
    if forwarded_proto and forwarded_host:
        launch_url = f"{forwarded_proto}://{forwarded_host}/lti/launch"
    else:
        launch_url = str(request.base_url).rstrip("/") + "/lti/launch"
    redirect = oidc.redirect(launch_url)
    # pylti1p3 FastAPI adapter returns a RedirectResponse directly
    if hasattr(redirect, 'do_redirect'):
        return redirect.do_redirect()
    return redirect


async def handle_launch_callback(
    request: Request,
    tool_conf: FirestoreToolConf,
    launch_data_storage,
    settings: Settings,
) -> tuple[LTIClaims, Response]:
    """Validate LTI launch JWT, extract claims.

    Returns (claims, base_response) — caller sets session cookie on the response.
    """
    from pylti1p3.session import SessionService

    form_data = dict(await request.form())

    # Pre-warm ToolConf cache before pylti1p3's sync validation calls
    id_token = form_data.get("id_token", "")
    if id_token:
        iss, client_id = _peek_jwt_claims(id_token)
        if iss:
            tool_conf.preload_registration(iss, client_id)

    lti_request = FastAPIRequest(request, form_data=form_data)
    cookie_service = FastAPICookieService(request)
    session_service = SessionService(lti_request)

    message_launch = FastAPIMessageLaunch(
        lti_request,
        tool_conf,
        session_service,
        cookie_service,
        launch_data_storage,
    )
    message_launch.validate()

    jwt_body = message_launch.get_launch_data()
    claims = _extract_claims(jwt_body)

    # Build response — caller will add session cookie
    frontend_url = "/dashboard"
    response = RedirectResponse(url=frontend_url, status_code=302)
    cookie_service.apply_to_response(response)

    _logger.info(
        "lti_launch_success",
        user_id=claims.user_id,
        course_id=claims.canvas_course_id,
        roles=claims.roles,
    )
    return claims, response


def _peek_jwt_claims(id_token: str) -> tuple[str, str]:
    """Extract iss and client_id from JWT payload WITHOUT signature verification.

    This is safe — actual verification is done by pylti1p3.validate().
    We just need iss/client_id to pre-warm the ToolConf cache.
    """
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return "", ""
        # Base64 decode the payload (2nd segment)
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        iss = payload.get("iss", "")
        aud = payload.get("aud", "")
        client_id = aud if isinstance(aud, str) else (aud[0] if aud else "")
        return iss, client_id
    except Exception:
        _logger.debug("peek_jwt_failed", exc_info=True)
        return "", ""


def _extract_claims(jwt_body: dict) -> LTIClaims:
    """Map pylti1p3's raw JWT body to our LTIClaims model."""
    context = jwt_body.get(_CLAIM_CONTEXT, {})
    custom = jwt_body.get(_CLAIM_CUSTOM, {})
    resource_link = jwt_body.get(_CLAIM_RESOURCE_LINK, {})
    roles = jwt_body.get(_CLAIM_ROLES, [])

    # Canvas course ID: check custom params first, then context.id
    raw_course_id = (
        custom.get("canvas_course_id")
        or custom.get("course_id")
        or context.get("id", "0")
    )
    try:
        canvas_course_id = int(raw_course_id)
    except (ValueError, TypeError):
        canvas_course_id = 0

    # Canvas base URL: from custom params or derive from iss
    canvas_base_url = custom.get("canvas_base_url", "") or custom.get(
        "canvas_api_base_url", ""
    )
    if not canvas_base_url:
        iss = jwt_body.get("iss", "")
        if "instructure.com" in iss:
            canvas_base_url = iss

    # User info
    sub = jwt_body.get("sub", "")

    return LTIClaims(
        iss=jwt_body.get("iss", ""),
        sub=sub,
        aud=jwt_body.get("aud", ""),
        deployment_id=jwt_body.get(_CLAIM_DEPLOYMENT, ""),
        target_link_uri=jwt_body.get(_CLAIM_TARGET_LINK, ""),
        canvas_course_id=canvas_course_id,
        canvas_base_url=canvas_base_url,
        user_id=sub,
        user_email=jwt_body.get("email", ""),
        user_name=jwt_body.get("name", ""),
        user_given_name=jwt_body.get("given_name", ""),
        user_family_name=jwt_body.get("family_name", ""),
        roles=roles,
        resource_link_id=resource_link.get("id", ""),
        context_id=context.get("id", ""),
        context_title=context.get("title", ""),
    )
