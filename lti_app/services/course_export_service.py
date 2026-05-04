"""Canvas course export service backed by the Content Exports API."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from lti_app.canvas.client import CanvasClient
from lti_app.models import CourseExportStatus

_logger = structlog.get_logger(__name__)


@dataclass
class CourseExportDownload:
    """Open download stream for an exported course package."""

    stream: AsyncIterator[bytes]
    media_type: str
    filename: str
    content_disposition: str | None
    close: Callable[[], Awaitable[None]]


class CourseExportService:
    """Create, inspect, and download Canvas Common Cartridge exports."""

    def __init__(self, client: CanvasClient):
        self._client = client

    async def start_course_export(
        self,
        course_id: int,
        export_type: str = "common_cartridge",
        *,
        skip_notifications: bool = True,
    ) -> CourseExportStatus:
        """Start a new course export job in Canvas."""
        export = await self._client.post(
            f"/api/v1/courses/{course_id}/content_exports",
            body={
                "export_type": export_type,
                "skip_notifications": skip_notifications,
            },
        )
        return await self._build_status(course_id, export)

    async def get_course_export(
        self,
        course_id: int,
        export_id: int,
    ) -> CourseExportStatus:
        """Fetch current status for a specific course export."""
        export = await self._client.get(
            f"/api/v1/courses/{course_id}/content_exports/{export_id}"
        )
        return await self._build_status(course_id, export)

    async def open_course_export_download(
        self,
        course_id: int,
        export_id: int,
    ) -> CourseExportDownload:
        """Open a streaming download for an exported IMSCC package."""
        export = await self._client.get(
            f"/api/v1/courses/{course_id}/content_exports/{export_id}"
        )
        workflow_state = export.get("workflow_state", "created")
        attachment = export.get("attachment") or {}
        download_url = attachment.get("url")
        if workflow_state != "exported" or not download_url:
            raise ValueError("Canvas export is not ready for download yet")

        filename = (
            attachment.get("filename")
            or attachment.get("display_name")
            or f"course-{course_id}-export-{export_id}.imscc"
        )
        headers = {"Authorization": f"Bearer {self._client._access_token}"}
        http = httpx.AsyncClient(timeout=None, follow_redirects=True)

        try:
            request = http.build_request("GET", download_url, headers=headers)
            response = await http.send(request, stream=True)
            response.raise_for_status()
        except Exception:
            await http.aclose()
            raise

        async def _close() -> None:
            await response.aclose()
            await http.aclose()

        return CourseExportDownload(
            stream=response.aiter_bytes(),
            media_type=response.headers.get("content-type", "application/octet-stream"),
            filename=filename,
            content_disposition=response.headers.get("content-disposition"),
            close=_close,
        )

    async def _build_status(
        self,
        course_id: int,
        export: dict[str, Any],
    ) -> CourseExportStatus:
        """Normalize Canvas content export + progress payloads for the frontend."""
        progress: dict[str, Any] | None = None
        progress_url = export.get("progress_url")

        if progress_url:
            try:
                progress = await self._fetch_json(progress_url)
            except Exception as exc:
                _logger.warning(
                    "course_export_progress_lookup_failed",
                    course_id=course_id,
                    export_id=export.get("id"),
                    error=str(exc),
                )

        workflow_state = export.get("workflow_state", "created")

        # Canvas may mark the progress object completed slightly before the export
        # record exposes its attachment; refetch once in that case.
        if (
            workflow_state != "exported"
            and progress
            and progress.get("workflow_state") == "completed"
        ):
            export = await self._client.get(
                f"/api/v1/courses/{course_id}/content_exports/{export['id']}"
            )
            workflow_state = export.get("workflow_state", workflow_state)

        attachment = export.get("attachment") or {}
        download_ready = workflow_state == "exported" and bool(attachment.get("url"))

        return CourseExportStatus(
            export_id=export["id"],
            course_id=str(course_id),
            export_type=export.get("export_type", "common_cartridge"),
            workflow_state=workflow_state,
            created_at=export.get("created_at"),
            progress_url=progress_url,
            progress_completion=progress.get("completion") if progress else None,
            progress_state=progress.get("workflow_state") if progress else None,
            progress_message=progress.get("message") if progress else None,
            download_ready=download_ready,
            download_url=(
                f"/api/courses/{course_id}/exports/{export['id']}/download"
                if download_ready
                else None
            ),
        )

    async def _fetch_json(self, path_or_url: str) -> Any:
        """Fetch JSON from either a Canvas API path or an absolute URL."""
        if path_or_url.startswith("http"):
            return await self._client.get_raw(path_or_url)
        return await self._client.get(path_or_url)
