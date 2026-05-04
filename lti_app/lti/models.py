"""LTI 1.3 data models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PlatformRegistration(BaseModel):
    """A Canvas LMS platform registration for LTI 1.3."""

    id: str
    iss: str  # e.g. "https://canvas.instructure.com"
    client_id: str  # Canvas Developer Key client ID
    deployment_id: str
    canvas_base_url: str  # e.g. "https://laccd.instructure.com"
    auth_login_url: str  # Canvas OIDC auth endpoint
    auth_token_url: str  # Canvas token endpoint
    jwks_url: str  # Canvas public JWKS URL
    tool_private_key_pem: str  # Tool's RSA private key PEM
    tool_public_key_pem: str = ""  # Tool's RSA public key PEM
    oauth2_client_id: str = ""  # Phase 2
    oauth2_client_secret: str = ""  # Phase 2


class LTIClaims(BaseModel):
    """Extracted claims from a validated LTI 1.3 launch JWT."""

    iss: str
    sub: str  # Canvas user ID
    aud: str | list[str]
    deployment_id: str
    target_link_uri: str = ""
    canvas_course_id: int
    canvas_base_url: str
    user_id: str
    user_email: str = ""
    user_name: str = ""
    user_given_name: str = ""
    user_family_name: str = ""
    roles: list[str] = []
    resource_link_id: str = ""
    context_id: str = ""
    context_title: str = ""

    @property
    def is_instructor(self) -> bool:
        return any(
            "Instructor" in r or "ContentDeveloper" in r or "TeachingAssistant" in r
            for r in self.roles
        )

    @property
    def is_admin(self) -> bool:
        return any(
            "Administrator" in r or "urn:lti:sysrole:ims/lis/SysAdmin" in r
            for r in self.roles
        )


class LTISession(BaseModel):
    """Session data stored in Firestore lti_sessions/{session_id}."""

    session_id: str
    user_id: str
    user_email: str
    user_name: str
    canvas_course_id: int
    course_name: str = ""
    canvas_base_url: str
    roles: list[str] = []
    deployment_id: str
    is_instructor: bool = False
    is_admin: bool = False
    created_at: datetime
    expires_at: datetime
    # Phase 2: Canvas API token fields
    canvas_access_token: str = ""
    canvas_refresh_token: str = ""
    token_expires_at: Optional[datetime] = None

    @property
    def has_canvas_token(self) -> bool:
        """Check if this session has a valid Canvas API token."""
        return bool(self.canvas_access_token)


class LTISessionResponse(BaseModel):
    """API response for GET /api/session — what the frontend receives."""

    canvasCourseId: int
    courseName: str
    userId: str
    userName: str
    userEmail: str
    roles: list[str]
    isInstructor: bool
    isAdmin: bool
    sessionToken: str  # The JWT session token
    canvasBaseUrl: str

    @classmethod
    def from_session(cls, session: LTISession, token: str) -> "LTISessionResponse":
        return cls(
            canvasCourseId=session.canvas_course_id,
            courseName=session.course_name,
            userId=session.user_id,
            userName=session.user_name,
            userEmail=session.user_email,
            roles=session.roles,
            isInstructor=session.is_instructor,
            isAdmin=session.is_admin,
            sessionToken=token,
            canvasBaseUrl=session.canvas_base_url,
        )
