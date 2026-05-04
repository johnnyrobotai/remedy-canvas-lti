"""Hybrid image fetcher for Canvas course images."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
from dataclasses import dataclass
from urllib.parse import unquote

import httpx
import structlog

from lti_app.canvas.client import CanvasClient

_logger = structlog.get_logger(__name__)

DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?;base64,(?P<data>.+)$", re.I | re.S
)
CANVAS_FILE_PATTERN = re.compile(
    r"/(?:courses/\d+/)?files/(?P<file_id>\d+)(?:/(?:preview|download))?",
)
CANVAS_FILE_CONTENTS_PATTERN = re.compile(
    r"/courses/(?P<course_id>\d+)/file_contents/(?P<file_path>.+)",
)
EQUATION_IMAGE_PATTERN = re.compile(r"/equation_images/", re.I)
CANVAS_COURSE_REF_PATTERN = re.compile(
    r"(?:\$|%24)CANVAS_COURSE_REFERENCE(?:\$|%24)/file_ref/", re.I
)

_EXTERNAL_TIMEOUT = 10.0


@dataclass(frozen=True)
class ImagePayload:
    """Resolved image bytes with MIME type."""

    image_bytes: bytes
    mime_type: str


class ImageFetchError(Exception):
    """Raised when an image cannot be fetched."""


class ImageFetcher:
    """Fetch image bytes from Canvas files, external URLs, or data URIs."""

    def __init__(self, canvas_client: CanvasClient) -> None:
        self._canvas_client = canvas_client
        # Per-course filename → file_id cache. Resolving a legacy
        # /courses/{id}/file_contents/course files/{name} URL requires a
        # Canvas API search. Cache hits avoid hammering the API when the
        # same image is referenced from multiple pages (very common).
        self._file_contents_cache: dict[tuple[int, str], int | None] = {}

    async def fetch_image(
        self,
        src: str,
        course_id: int,
    ) -> ImagePayload | None:
        """Fetch image bytes from the given src URL.

        Returns None if the image should be skipped (equation images)
        or cannot be fetched.
        """
        src = (src or "").strip()
        if not src:
            return None

        # Skip equation images
        if EQUATION_IMAGE_PATTERN.search(src):
            _logger.debug("image_fetch_skip_equation", src=src[:80])
            return None

        try:
            if src.startswith("data:"):
                return self._decode_data_uri(src)

            # Handle $CANVAS_COURSE_REFERENCE$ URLs (unresolved import references)
            ref_match = CANVAS_COURSE_REF_PATTERN.search(src)
            if ref_match:
                _logger.debug("image_fetch_skip_unresolved_ref", src=src[:80])
                return None

            file_match = CANVAS_FILE_PATTERN.search(src)
            if file_match:
                return await self._fetch_canvas_file(int(file_match.group("file_id")))

            # Handle legacy Canvas file_contents URLs:
            #   /courses/{id}/file_contents/course files/photo.jpg
            # These can't be fetched via the OAuth Bearer token directly
            # (Canvas redirects them to /login because they're served by the
            # web frontend, not the API), but we CAN resolve the filename to
            # a real file_id via the Files API and then download with auth.
            # Without this, courses imported via IMSCC or with legacy HTML
            # have 100% of their images skipped by alt-text generation
            # (CLU-65).
            file_contents_match = CANVAS_FILE_CONTENTS_PATTERN.search(src)
            if file_contents_match:
                resolved = await self._resolve_file_contents_url(
                    file_contents_match, course_id
                )
                if resolved is not None:
                    return await self._fetch_canvas_file(resolved)
                _logger.debug(
                    "image_fetch_file_contents_unresolved", src=src[:80]
                )
                return None

            if src.startswith(("http://", "https://", "//")):
                return await self._fetch_external(src)

            _logger.warning("image_fetch_unknown_source", src=src[:80])
            return None

        except ImageFetchError as exc:
            _logger.warning("image_fetch_error", src=src[:80], error=str(exc))
            return None
        except Exception as exc:
            _logger.warning("image_fetch_failed", src=src[:80], error=str(exc))
            return None

    async def _resolve_file_contents_url(
        self,
        match: re.Match,
        course_id: int,
    ) -> int | None:
        """Resolve a legacy ``/file_contents/course files/{name}`` URL to a file_id.

        Caches per-course lookups to avoid hammering the Canvas API when the
        same image is referenced from many pages. Returns ``None`` if the
        filename truly doesn't exist in the course (handled by Bug B fallback).
        """
        try:
            file_path = match.group("file_path")
        except IndexError:
            return None

        # Strip the synthetic root folder ("course files" / "course%20files")
        decoded = unquote(file_path)
        # Take the basename — Canvas search_term matches by display_name and
        # we don't have a folder-aware lookup. Two files with the same name
        # in different folders would collide; in practice this is rare for
        # accessibility scans (and the Canvas search returns by recency).
        filename = decoded.rsplit("/", 1)[-1].strip()
        if not filename:
            return None

        # Strip query string from URL-shaped filenames
        filename = filename.split("?", 1)[0]
        if not filename:
            return None

        # course_id may come in as 0 from older callers; bail in that case
        if not course_id:
            return None

        cache_key = (course_id, filename.lower())
        if cache_key in self._file_contents_cache:
            return self._file_contents_cache[cache_key]

        try:
            results = await self._canvas_client.get(
                f"/api/v1/courses/{course_id}/files",
                params={"search_term": filename, "per_page": 5},
            )
        except Exception as exc:
            _logger.warning(
                "image_fetch_file_contents_lookup_failed",
                filename=filename,
                error=str(exc),
            )
            self._file_contents_cache[cache_key] = None
            return None

        if not isinstance(results, list):
            self._file_contents_cache[cache_key] = None
            return None

        # Prefer exact display_name / filename match before fuzzy
        target = filename.lower()
        for item in results:
            if not isinstance(item, dict):
                continue
            display = (item.get("display_name") or "").lower()
            actual = (item.get("filename") or "").lower()
            if display == target or actual == target:
                file_id = item.get("id")
                if isinstance(file_id, int):
                    self._file_contents_cache[cache_key] = file_id
                    return file_id

        # Fallback: first result if any
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                file_id = item["id"]
                self._file_contents_cache[cache_key] = file_id
                return file_id

        self._file_contents_cache[cache_key] = None
        return None

    async def _fetch_canvas_file(self, file_id: int) -> ImagePayload:
        """Fetch image via Canvas Files API.

        Uses /files/{id} metadata to check MIME type, then downloads
        via /files/{id}/download?download_frd=1 with Bearer auth.
        """
        try:
            # Get file metadata to check content type
            file_info = await self._canvas_client.get(f"/api/v1/files/{file_id}")
            file_mime = (file_info.get("content-type") or "").split(";")[0].strip()
            if file_mime and not file_mime.startswith("image/"):
                raise ImageFetchError(f"Not an image: {file_mime}")

            # Download directly with auth (avoids expired temp URLs / login redirects)
            base_url = self._canvas_client.base_url
            download_url = f"{base_url}/files/{file_id}/download?download_frd=1"

            auth_headers: dict[str, str] = {
                "Authorization": f"Bearer {self._canvas_client._access_token}",
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    download_url, follow_redirects=True, headers=auth_headers,
                )
                response.raise_for_status()

            # Validate response is actually an image
            resp_content_type = response.headers.get("content-type", "")
            if "text/html" in resp_content_type:
                raise ImageFetchError(
                    f"Download returned HTML instead of image (file {file_id})"
                )

            mime_type = file_mime or resp_content_type.split(";")[0].strip() or "image/jpeg"

            return ImagePayload(
                image_bytes=response.content,
                mime_type=mime_type,
            )
        except ImageFetchError:
            raise
        except Exception as exc:
            raise ImageFetchError(f"Canvas file fetch failed: {exc}") from exc

    async def _fetch_external(self, url: str) -> ImagePayload:
        """Fetch image from external URL."""
        if url.startswith("//"):
            url = f"https:{url}"

        try:
            async with httpx.AsyncClient(
                timeout=_EXTERNAL_TIMEOUT,
                headers={"User-Agent": "Remedy Canvas LTI/1.0 (Canvas Accessibility Tool)"},
            ) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
        except Exception as exc:
            raise ImageFetchError(f"External fetch failed: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        mime_type = content_type.split(";")[0].strip() or self._guess_mime(url)

        return ImagePayload(
            image_bytes=response.content,
            mime_type=mime_type,
        )

    def _decode_data_uri(self, src: str) -> ImagePayload:
        """Decode a base64 data URI."""
        match = DATA_URI_RE.match(src)
        if not match:
            raise ImageFetchError("Invalid data URI")

        mime_type = (match.group("mime") or "image/jpeg").strip().lower()
        try:
            image_bytes = base64.b64decode(match.group("data"), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageFetchError("Invalid base64 in data URI") from exc

        return ImagePayload(image_bytes=image_bytes, mime_type=mime_type)

    def _detect_source_type(self, src: str) -> str:
        """Detect source type: 'canvas_file', 'data_uri', or 'external'."""
        src = (src or "").strip()
        if src.startswith("data:"):
            return "data_uri"
        if CANVAS_FILE_PATTERN.search(src):
            return "canvas_file"
        return "external"

    def _extract_file_id(self, src: str) -> int | None:
        """Extract Canvas file ID from a Canvas file URL, or None if not found."""
        match = CANVAS_FILE_PATTERN.search(src or "")
        if match:
            return int(match.group("file_id"))
        return None

    @staticmethod
    def _guess_mime(url: str) -> str:
        """Guess MIME type from URL extension."""
        path = url.split("?")[0].split("#")[0]
        guessed, _ = mimetypes.guess_type(path)
        return guessed or "image/jpeg"
