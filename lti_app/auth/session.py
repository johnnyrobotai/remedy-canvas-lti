"""JWT session management for LTI sessions.

Sessions are stored in Firestore (or in-memory for local dev).
The JWT is lightweight — only contains session_id + exp.
Full session data lives in the repository.
"""

from datetime import UTC, datetime, timedelta

import jwt
import structlog
from ulid import ULID

from lti_app.config import Settings
from lti_app.db.repositories import SessionRepository
from lti_app.lti.models import LTIClaims, LTISession

_logger = structlog.get_logger(__name__)


class SessionTokenError(Exception):
    """Raised when a session JWT is invalid or expired."""

    def __init__(self, message: str, code: str = "invalid_token"):
        super().__init__(message)
        self.code = code


def create_session_token(session_id: str, expires_at: datetime, secret: str) -> str:
    """Create an HS256 JWT containing only the session_id reference."""
    payload = {
        "sub": session_id,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session_token(token: str, secret: str) -> str:
    """Validate JWT and return session_id. Raises SessionTokenError on failure."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        session_id = payload.get("sub")
        if not session_id:
            raise SessionTokenError("Missing session ID in token", "missing_sub")
        return session_id
    except jwt.ExpiredSignatureError:
        raise SessionTokenError("Session token expired", "expired")
    except jwt.InvalidTokenError as e:
        raise SessionTokenError(f"Invalid session token: {e}", "invalid")


def create_session(
    repo: SessionRepository,
    claims: LTIClaims,
    settings: Settings,
) -> tuple[str, str]:
    """Create an LTI session from launch claims.

    Returns (session_jwt, session_id).
    """
    session_id = str(ULID())
    now = datetime.now(UTC)
    expires = now + timedelta(hours=settings.session_ttl_hours)

    session = LTISession(
        session_id=session_id,
        user_id=claims.user_id,
        user_email=claims.user_email,
        user_name=claims.user_name,
        canvas_course_id=claims.canvas_course_id,
        course_name=claims.context_title,
        canvas_base_url=claims.canvas_base_url,
        roles=claims.roles,
        deployment_id=claims.deployment_id,
        is_instructor=claims.is_instructor,
        is_admin=claims.is_admin,
        created_at=now,
        expires_at=expires,
    )

    repo.save_session(session_id, session.model_dump(mode="json"))

    # Inject Canvas API token if available (bypasses OAuth2 flow for testing)
    if settings.local_canvas_api_token:
        data = repo.get_session(session_id)
        if data and not data.get("canvas_access_token"):
            data["canvas_access_token"] = settings.local_canvas_api_token
            repo.save_session(session_id, data)

    token = create_session_token(session_id, expires, settings.session_secret_key)
    _logger.info(
        "session_created",
        session_id=session_id,
        user_id=claims.user_id,
        course_id=claims.canvas_course_id,
    )
    return token, session_id


def get_session(
    repo: SessionRepository,
    session_id: str,
) -> LTISession | None:
    """Fetch session from repository, check TTL."""
    data = repo.get_session(session_id)
    if not data:
        return None

    try:
        session = LTISession(**data)
    except Exception:
        _logger.warning("session_parse_error", session_id=session_id, exc_info=True)
        return None

    # Double-check expiry (repo may also check, but defense-in-depth)
    if session.expires_at.astimezone(UTC) < datetime.now(UTC):
        repo.delete_session(session_id)
        return None

    return session


def create_bypass_session(
    repo: SessionRepository,
    settings: Settings,
    course_id: int | None = None,
    context_title: str = "",
) -> tuple[str, str]:
    """Create a mock session for AUTH_BYPASS_FOR_LOCAL development.

    Args:
        course_id: Canvas course ID (extracted from LTI login POST form).
                   Falls back to 1 if not provided.
        context_title: Course title (extracted from LTI login POST form).
    """
    mock_claims = LTIClaims(
        iss="https://canvas.instructure.com",
        sub="dev_user_001",
        aud=settings.local_canvas_client_id,
        deployment_id=settings.local_canvas_deployment_id,
        target_link_uri="http://localhost:8001/lti/launch",
        canvas_course_id=course_id or 1,
        canvas_base_url=settings.local_canvas_base_url,
        user_id="dev_user_001",
        user_email="admin@canvas.docker",
        user_name="Dev Admin",
        roles=[
            "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
            "http://purl.imsglobal.org/vocab/lis/v2/institution/person#Administrator",
        ],
        resource_link_id="dev_resource_001",
        context_id=f"dev_context_{course_id or 1}",
        context_title=context_title or f"Course {course_id or 1}",
    )
    token, session_id = create_session(repo, mock_claims, settings)

    # Inject Canvas API token for local dev so scanning works
    if settings.local_canvas_api_token:
        data = repo.get_session(session_id)
        if data:
            data["canvas_access_token"] = settings.local_canvas_api_token
            repo.save_session(session_id, data)

    return token, session_id
