"""FastAPI dependencies for LTI session authentication."""

from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request

from lti_app.auth.session import (
    SessionTokenError,
    create_bypass_session,
    decode_session_token,
    get_session,
)
from lti_app.config import Settings, get_settings
from lti_app.db.repositories import SessionRepository, get_session_repository
from lti_app.lti.models import LTISession

_logger = structlog.get_logger(__name__)


async def get_current_session(
    request: Request,
    settings: Settings = Depends(get_settings),
    session_repo: SessionRepository = Depends(get_session_repository),
) -> LTISession:
    """Extract and validate the LTI session from cookie or Authorization header.

    When AUTH_BYPASS_FOR_LOCAL=true, reads session from cookie (set during
    /lti/launch bypass). Falls back to creating a default session if no cookie.
    """
    # --- Bypass mode ---
    if settings.auth_bypass_for_local:
        # Try reading from cookie first (set by LTI launch bypass per-course)
        token = _extract_token(request)
        if token:
            try:
                session_id = decode_session_token(token, settings.session_secret_key)
                session = get_session(session_repo, session_id)
                if session:
                    return session
            except SessionTokenError:
                pass
        # No valid cookie — extract course_id from URL path, fallback to 1
        import re
        course_id_match = re.search(r'/courses/(\d+)', request.url.path)
        course_id = int(course_id_match.group(1)) if course_id_match else 1
        _, session_id = create_bypass_session(session_repo, settings, course_id=course_id)
        session = get_session(session_repo, session_id)
        if session:
            return session
        raise HTTPException(status_code=500, detail="Failed to create bypass session")

    # --- Production mode ---
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="No LTI session token provided")

    try:
        session_id = decode_session_token(token, settings.session_secret_key)
    except SessionTokenError as e:
        _logger.debug("session_token_invalid", code=e.code)
        raise HTTPException(status_code=401, detail=str(e))

    session = get_session(session_repo, session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or not found")

    return session


async def require_instructor(
    session: LTISession = Depends(get_current_session),
) -> LTISession:
    """Require Instructor or ContentDeveloper role."""
    if not session.is_instructor:
        raise HTTPException(status_code=403, detail="Instructor role required")
    return session


async def require_admin(
    session: LTISession = Depends(get_current_session),
) -> LTISession:
    """Require Administrator role."""
    if not session.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return session


def verify_session_course_id(session: LTISession, course_id: int | str) -> None:
    """Raise HTTPException(403) if the LTI session is not authorized
    for the given path course_id.

    CLU-82: prevents cross-course data access on any route that takes
    both an InstructorSession and a path ``{course_id}``. Without this
    check an authenticated instructor for course A could fetch course
    B's data (including remediated export file bytes) just by putting
    a different integer in the URL.

    Admin sessions are not granted any special bypass — admins launch
    from a specific course like anyone else and are only authorized for
    that one. If an admin needs to access a different course, they
    launch the tool from that course.
    """
    try:
        requested_course_id = int(course_id)
    except (TypeError, ValueError):
        requested_course_id = -1

    if session.canvas_course_id != requested_course_id:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to access this course",
        )


def _extract_token(request: Request) -> str | None:
    """Extract session token from cookie or Authorization header."""
    # Cookie first (set during /lti/launch redirect)
    token = request.cookies.get("lti_session")
    if token:
        return token

    # Authorization: Bearer fallback (Safari ITP iframe workaround)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    return None


# Type aliases for clean route signatures
CurrentSession = Annotated[LTISession, Depends(get_current_session)]
InstructorSession = Annotated[LTISession, Depends(require_instructor)]
AdminSession = Annotated[LTISession, Depends(require_admin)]
