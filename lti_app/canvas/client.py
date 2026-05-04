"""Async Canvas LMS REST API client with pagination and rate limiting."""

import asyncio
import re
from typing import Any, AsyncIterator

import httpx
import structlog

_logger = structlog.get_logger(__name__)

_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_RATE_LIMIT_THRESHOLD = 50
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 0.5
_MAX_BACKOFF = 10.0
_REQUEST_TIMEOUT = 30.0


class CanvasAPIError(Exception):
    """Non-retryable Canvas API error."""

    def __init__(self, status: int, message: str, url: str = ""):
        super().__init__(f"Canvas API {status}: {message}")
        self.status = status
        self.url = url


class CanvasAuthError(CanvasAPIError):
    """Canvas authentication/authorization failure."""

    def __init__(self, message: str = "Canvas authentication failed", url: str = ""):
        super().__init__(401, message, url)


class CanvasRateLimitError(CanvasAPIError):
    """Canvas rate limit exceeded after backoff."""

    def __init__(self, url: str = ""):
        super().__init__(429, "Rate limit exceeded", url)


class CanvasClient:
    """Async Canvas REST API client."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        refresh_token: str = "",
        oauth2_client_id: str = "",
        oauth2_client_secret: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._oauth2_client_id = oauth2_client_id
        self._oauth2_client_secret = oauth2_client_secret
        self._http = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    async def __aenter__(self) -> "CanvasClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self):
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Single GET request. Returns parsed JSON."""
        return await self._request("GET", path, params=params)

    async def get_raw(self, full_url: str) -> Any:
        """GET a full URL (not path) with auth headers. Used for redirect confirmation."""
        headers = {"Authorization": f"Bearer {self._access_token}"}
        response = await self._http.get(full_url, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_paginated(
        self, path: str, params: dict[str, Any] | None = None
    ) -> AsyncIterator[dict]:
        """Auto-paginated GET. Yields individual items from all pages."""
        url = f"{self.base_url}{path}"
        request_params: dict[str, Any] | None = dict(params or {})
        request_params.setdefault("per_page", 100)
        seen_urls: set[str] = set()

        while url:
            # Guard against infinite pagination loops. The cache key must
            # reflect the *effective* request (URL + any extra params), so
            # genuine loops are caught while legitimate next-link URLs (which
            # already encode their own ?page=N&per_page=100) are not flagged.
            if request_params:
                cache_key = f"{url}?{sorted(request_params.items())}"
            else:
                cache_key = url
            if cache_key in seen_urls:
                _logger.warning("paginated_loop_detected", url=url[:120])
                break
            seen_urls.add(cache_key)

            response = await self._request_with_retry("GET", url, params=request_params)
            await self._check_rate_limit(response)

            if response.status_code >= 400:
                self._handle_error(response, url)

            data = response.json()
            if isinstance(data, list):
                for item in data:
                    yield item
            else:
                yield data

            url = self._extract_next_link(response)
            # CRITICAL: pass params=None (not {}) on subsequent calls.
            # The next-link URL from Canvas's Link header already contains the
            # full query string (page=N&per_page=100). httpx.request() REPLACES
            # the URL's query string with `params` whenever params is a dict
            # (even an empty one), which would strip page/per_page and cause
            # Canvas to fall back to per_page=10. Passing None preserves the
            # URL's existing query string verbatim.
            request_params = None

    async def put(self, path: str, body: dict | None = None) -> Any:
        """PUT request. Returns parsed JSON."""
        return await self._request("PUT", path, json=body)

    async def post(self, path: str, body: dict | None = None) -> Any:
        """POST request. Returns parsed JSON."""
        return await self._request("POST", path, json=body)

    async def delete(self, path: str) -> Any:
        """DELETE request. Returns parsed JSON."""
        return await self._request("DELETE", path)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict | None = None,
    ) -> Any:
        """Execute request with retries and error handling."""
        url = f"{self.base_url}{path}" if not path.startswith("http") else path
        backoff = _INITIAL_BACKOFF
        last_error = None
        _refreshed = False

        for attempt in range(_MAX_RETRIES):
            response = await self._request_with_retry(method, url, params=params, json=json)
            await self._check_rate_limit(response)

            if response.status_code == 200 or response.status_code == 201:
                return response.json()

            if response.status_code == 401 and not _refreshed:
                _refreshed = True
                refreshed = await self._try_refresh_token()
                if refreshed:
                    continue
                raise CanvasAuthError(url=url)

            if response.status_code >= 500:
                last_error = CanvasAPIError(
                    response.status_code,
                    response.text[:200],
                    url,
                )
                if attempt < _MAX_RETRIES - 1:
                    _logger.warning(
                        "canvas_retry",
                        attempt=attempt + 1,
                        status=response.status_code,
                        url=url,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                    continue

            if response.status_code >= 400:
                self._handle_error(response, url)

        if last_error:
            raise last_error
        raise CanvasAPIError(500, "Max retries exceeded", url)

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        """Execute a single HTTP request, retrying on network-level errors."""
        backoff = _INITIAL_BACKOFF
        last_exc: httpx.RequestError | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                return await self._raw_request(method, url, params=params, json=json)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    _logger.warning(
                        "canvas_network_retry",
                        attempt=attempt + 1,
                        url=url,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)

        raise CanvasAPIError(0, f"Network error: {last_exc}", url)

    async def _raw_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        """Execute a single HTTP request."""
        headers = {"Authorization": f"Bearer {self._access_token}"}
        return await self._http.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
        )

    async def _check_rate_limit(self, response: httpx.Response) -> None:
        """Back off if Canvas rate limit is getting low."""
        remaining = response.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None:
            try:
                remaining_float = float(remaining)
                if remaining_float < _RATE_LIMIT_THRESHOLD:
                    wait = max(0.5, (_RATE_LIMIT_THRESHOLD - remaining_float) / 10)
                    wait = min(wait, _MAX_BACKOFF)
                    _logger.info("canvas_rate_limit_backoff", remaining=remaining_float, wait=wait)
                    await asyncio.sleep(wait)
            except ValueError:
                pass

    async def _try_refresh_token(self) -> bool:
        """Attempt to refresh the Canvas OAuth2 access token."""
        if not self._refresh_token or not self._oauth2_client_id:
            return False

        try:
            response = await self._http.post(
                f"{self.base_url}/login/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._oauth2_client_id,
                    "client_secret": self._oauth2_client_secret,
                    "refresh_token": self._refresh_token,
                },
            )
            if response.status_code == 200:
                data = response.json()
                self._access_token = data["access_token"]
                _logger.info("canvas_token_refreshed")
                return True
        except (httpx.RequestError, KeyError) as e:
            _logger.error("canvas_token_refresh_failed", error=str(e))
        return False

    @staticmethod
    def _extract_next_link(response: httpx.Response) -> str | None:
        """Extract next page URL from Link header."""
        link_header = response.headers.get("Link", "")
        match = _LINK_NEXT_RE.search(link_header)
        return match.group(1) if match else None

    @staticmethod
    def _handle_error(response: httpx.Response, url: str) -> None:
        """Raise appropriate error for non-OK response."""
        try:
            body = response.json()
            message = body.get("message", body.get("errors", response.text[:200]))
        except Exception:
            message = response.text[:200]

        if response.status_code == 401:
            raise CanvasAuthError(url=url)
        if response.status_code == 429:
            raise CanvasRateLimitError(url=url)
        raise CanvasAPIError(response.status_code, str(message), url)
