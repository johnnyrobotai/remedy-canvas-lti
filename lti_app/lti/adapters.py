"""FastAPI adapters for pylti1p3.

pylti1p3 has no FastAPI contrib — only Flask and Django. These adapters
mirror the Flask contrib pattern, wrapping FastAPI's Request/Response objects.
"""

from typing import Any

from fastapi import Request as FastAPIRequestObj
from fastapi.responses import HTMLResponse, RedirectResponse
from pylti1p3.cookie import CookieService
from pylti1p3.message_launch import MessageLaunch
from pylti1p3.oidc_login import OIDCLogin
from pylti1p3.redirect import Redirect
from pylti1p3.request import Request as Lti1p3Request
from pylti1p3.session import SessionService
from starlette.responses import Response


class FastAPIRequest(Lti1p3Request):
    """Wraps FastAPI's Request for pylti1p3.

    Form data must be pre-read (await request.form()) before constructing this,
    since pylti1p3 calls get_param() synchronously.
    """

    def __init__(
        self,
        request: FastAPIRequestObj,
        form_data: dict[str, str] | None = None,
    ):
        super().__init__()
        self._request = request
        self._params = {**dict(request.query_params), **(form_data or {})}

    def get_param(self, key: str) -> str:
        return self._params.get(key, "")

    def is_secure(self) -> bool:
        # Check X-Forwarded-Proto header (Cloud Run terminates TLS)
        forwarded = self._request.headers.get("x-forwarded-proto", "")
        if forwarded:
            return forwarded == "https"
        return self._request.url.scheme == "https"

    @property
    def session(self):
        # pylti1p3 SessionService uses LaunchDataStorage, not Request.session
        return {}


class FastAPICookieService(CookieService):
    """Accumulates cookies during pylti1p3 processing, applied to Response afterwards."""

    def __init__(self, request: FastAPIRequestObj):
        self._request = request
        self._pending: dict[str, dict[str, Any]] = {}

    def get_cookie(self, name: str) -> str | None:
        return self._request.cookies.get(name)

    def set_cookie(self, name: str, value: str | int, exp: int | None = 3600):
        self._pending[name] = {"value": str(value), "exp": exp}

    def apply_to_response(self, response: Response) -> None:
        is_secure = self._request.url.scheme == "https" or self._request.headers.get(
            "x-forwarded-proto"
        ) == "https"
        for name, data in self._pending.items():
            response.set_cookie(
                key=name,
                value=data["value"],
                max_age=data["exp"],
                httponly=True,
                samesite="none" if is_secure else "lax",
                secure=is_secure,
                path="/",
            )


class FastAPIRedirect(Redirect):
    """pylti1p3 Redirect implementation that produces FastAPI RedirectResponse."""

    def __init__(self, location: str, cookie_service: FastAPICookieService):
        self._location = location
        self._cookie_service = cookie_service

    def do_redirect(self) -> RedirectResponse:
        response = RedirectResponse(url=self._location, status_code=302)
        self._cookie_service.apply_to_response(response)
        return response

    def do_js_redirect(self) -> HTMLResponse:
        html = (
            "<html><head><title>Redirecting...</title></head><body>"
            f'<script>window.location.replace("{self._location}");</script>'
            f'<noscript><a href="{self._location}">Click here to continue</a></noscript>'
            "</body></html>"
        )
        response = HTMLResponse(content=html)
        self._cookie_service.apply_to_response(response)
        return response

    def set_redirect_url(self, location: str):
        self._location = location

    def get_redirect_url(self) -> str:
        return self._location


class FastAPIOIDCLogin(OIDCLogin):
    """pylti1p3 OIDCLogin wired to FastAPI adapters."""

    def __init__(self, request, tool_config, session_service, cookie_service, launch_data_storage=None):
        super().__init__(request, tool_config, session_service, cookie_service, launch_data_storage)
        self._cookie_service = cookie_service

    def get_redirect(self, url: str) -> FastAPIRedirect:
        return FastAPIRedirect(url, self._cookie_service)

    def get_response(self, html: str) -> HTMLResponse:
        response = HTMLResponse(content=html)
        self._cookie_service.apply_to_response(response)
        return response


class FastAPIMessageLaunch(MessageLaunch):
    """pylti1p3 MessageLaunch wired to FastAPI adapters."""

    def __init__(self, request, tool_config, session_service=None, cookie_service=None, launch_data_storage=None):
        super().__init__(request, tool_config, session_service, cookie_service, launch_data_storage)

    def _get_request_param(self, key: str) -> str:
        return self._request.get_param(key)
