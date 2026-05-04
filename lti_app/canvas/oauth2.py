"""Canvas OAuth2 authorization code flow."""

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import structlog

_logger = structlog.get_logger(__name__)


@dataclass
class TokenResponse:
    """Canvas OAuth2 token response."""

    access_token: str
    refresh_token: str = ""
    expires_in: int = 3600


def get_authorization_url(
    session,
    client_id: str,
    redirect_uri: str,
) -> str:
    """Build Canvas OAuth2 authorization URL."""
    params = urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": session.session_id,
    })
    return f"{session.canvas_base_url}/login/oauth2/auth?{params}"


async def exchange_code(
    code: str,
    canvas_base_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> TokenResponse:
    """Exchange authorization code for access and refresh tokens."""
    async with httpx.AsyncClient() as http:
        response = await http.post(
            f"{canvas_base_url}/login/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )

    if response.status_code != 200:
        _logger.error("oauth2_exchange_failed", status=response.status_code, body=response.text[:200])
        raise RuntimeError(f"OAuth2 code exchange failed: {response.status_code}")

    data = response.json()
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_in=data.get("expires_in", 3600),
    )


async def refresh_access_token(
    refresh_token: str,
    canvas_base_url: str,
    client_id: str,
    client_secret: str,
) -> TokenResponse:
    """Refresh an expired Canvas access token."""
    async with httpx.AsyncClient() as http:
        response = await http.post(
            f"{canvas_base_url}/login/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )

    if response.status_code != 200:
        _logger.error("oauth2_refresh_failed", status=response.status_code)
        raise RuntimeError(f"OAuth2 token refresh failed: {response.status_code}")

    data = response.json()
    return TokenResponse(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", refresh_token),
        expires_in=data.get("expires_in", 3600),
    )
