"""Canvas Files API operations: list, download, upload, folders."""

import re
from typing import Any

import httpx
import structlog

from lti_app.canvas.client import CanvasClient

_logger = structlog.get_logger(__name__)


class FileManager:
    """Manages file operations via Canvas Files API."""

    def __init__(self, client: CanvasClient):
        self._client = client

    async def list_course_files(
        self,
        course_id: int,
        content_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List all files in a course, with optional content_type filter.

        Args:
            course_id: Canvas course ID.
            content_types: Optional list of MIME types to include (e.g.
                ``["application/pdf"]``).  When ``None`` all files are returned.

        Returns:
            List of Canvas file objects.
        """
        files: list[dict[str, Any]] = []
        async for file in self._client.get_paginated(
            f"/api/v1/courses/{course_id}/files"
        ):
            if content_types is None or file.get("content-type") in content_types:
                files.append(file)

        _logger.info(
            "canvas_list_course_files",
            course_id=course_id,
            total=len(files),
            filtered=content_types is not None,
        )
        return files

    async def download_file(self, file_id: int, filename: str) -> tuple[bytes, str]:
        """Download file binary from Canvas.

        Canvas returns a signed temporary download URL in the file metadata.
        We fetch that URL with a plain httpx GET (no auth header required —
        the URL is pre-signed by Canvas).

        Args:
            file_id: Canvas file ID.
            filename: Filename used as a fallback if Canvas does not provide
                one in the metadata.

        Returns:
            A ``(bytes, filename)`` tuple.
        """
        # Step 1: Retrieve file metadata to get the download URL.
        file_meta: dict[str, Any] = await self._client.get(
            f"/api/v1/files/{file_id}"
        )
        download_url: str = file_meta.get("url", "")
        resolved_filename: str = file_meta.get("filename", filename)

        _logger.info(
            "canvas_download_file",
            file_id=file_id,
            filename=resolved_filename,
            has_url=bool(download_url),
        )

        if not download_url:
            raise ValueError(f"Canvas file {file_id} has no download URL in metadata")

        # Step 2: Fetch binary from the download URL.
        # Include auth header and follow redirects (local Canvas instances
        # may not generate pre-signed URLs).
        headers = {"Authorization": f"Bearer {self._client._access_token}"}
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as http:
            response = await http.get(download_url, headers=headers)
            response.raise_for_status()
            return response.content, resolved_filename

    async def get_course_folders(self, course_id: int) -> list[dict[str, Any]]:
        """List all folders in a course.

        Args:
            course_id: Canvas course ID.

        Returns:
            List of Canvas folder objects.
        """
        folders: list[dict[str, Any]] = []
        async for folder in self._client.get_paginated(
            f"/api/v1/courses/{course_id}/folders"
        ):
            folders.append(folder)

        _logger.info("canvas_get_course_folders", course_id=course_id, total=len(folders))
        return folders

    async def create_folder(
        self,
        course_id: int,
        name: str,
        parent_folder_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a folder inside a course.

        Args:
            course_id: Canvas course ID.
            name: Name for the new folder.
            parent_folder_id: Optional ID of the parent folder.  When omitted
                Canvas places the folder at the course root.

        Returns:
            Canvas folder object for the newly created folder.
        """
        body: dict[str, Any] = {"name": name}
        if parent_folder_id is not None:
            body["parent_folder_id"] = parent_folder_id

        folder: dict[str, Any] = await self._client.post(
            f"/api/v1/courses/{course_id}/folders", body=body
        )

        _logger.info(
            "canvas_create_folder",
            course_id=course_id,
            name=name,
            folder_id=folder.get("id"),
        )
        return folder

    async def upload_file(
        self,
        course_id: int,
        folder_id: int,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        """Upload a file to Canvas using the 3-step upload protocol.

        Canvas file upload flow:
        1. POST ``/api/v1/courses/{course_id}/files`` — notify Canvas of the
           pending upload; Canvas returns a pre-signed ``upload_url`` and
           ``upload_params`` form fields.
        2. POST ``upload_url`` — multipart upload of the actual file bytes.
           Canvas returns a redirect (3xx) to the confirmed file object.
        3. Follow redirect — the final response body contains the confirmed
           Canvas file object with an ``id``.

        Args:
            course_id: Canvas course ID.
            folder_id: Destination folder ID.
            filename: Name for the file in Canvas.
            data: Raw bytes of the file content.
            content_type: MIME type (e.g. ``"application/pdf"``).

        Returns:
            Canvas file object for the uploaded file.
        """
        # Step 1: Notify Canvas.
        notify_body: dict[str, Any] = {
            "name": filename,
            "size": len(data),
            "content_type": content_type,
            "parent_folder_id": folder_id,
        }
        notify_response: dict[str, Any] = await self._client.post(
            f"/api/v1/courses/{course_id}/files", body=notify_body
        )

        upload_url: str = notify_response["upload_url"]
        upload_params: dict[str, str] = notify_response.get("upload_params", {})

        _logger.info(
            "canvas_upload_file_step1",
            course_id=course_id,
            filename=filename,
            size=len(data),
        )

        # Step 2: POST binary to upload_url (don't follow redirects).
        # Canvas uses a multipart form POST; upload_params are form fields that
        # must precede the file field.
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as http:
            form_fields: list[tuple[str, Any]] = [
                (k, (None, v)) for k, v in upload_params.items()
            ]
            form_fields.append(("file", (filename, data, content_type)))

            upload_response = await http.post(
                upload_url,
                files=form_fields,
            )

            # Step 3: Follow redirect manually with auth to confirm upload.
            # Canvas returns 3xx with Location to create_success endpoint
            # which requires the Bearer token.
            if upload_response.status_code in (301, 302, 303):
                location = upload_response.headers.get("location", "")
                if location:
                    confirm_response = await self._client.get_raw(location)
                    confirmed: dict[str, Any] = confirm_response
                else:
                    upload_response.raise_for_status()
                    confirmed = upload_response.json()
            elif upload_response.is_success:
                confirmed = upload_response.json()
            else:
                upload_response.raise_for_status()
                confirmed = {}

        _logger.info(
            "canvas_upload_file_complete",
            course_id=course_id,
            filename=filename,
            file_id=confirmed.get("id"),
        )
        return confirmed

    # ------------------------------------------------------------------
    # Archive & link replacement helpers
    # ------------------------------------------------------------------

    async def get_or_create_archive_folder(self, course_id: int) -> dict[str, Any]:
        """Get or create the _clu_archived folder in a course."""
        folders = await self.get_course_folders(course_id)
        for folder in folders:
            if folder.get("name") == "_clu_archived":
                return folder
        return await self.create_folder(course_id, "_clu_archived")

    async def move_file_to_archive(self, course_id: int, file_id: int) -> dict[str, Any]:
        """Move a file to the _clu_archived folder."""
        archive_folder = await self.get_or_create_archive_folder(course_id)
        result = await self._client.put(
            f"/api/v1/files/{file_id}",
            body={"parent_folder_id": archive_folder["id"]},
        )
        _logger.info(
            "canvas_file_archived",
            course_id=course_id,
            file_id=file_id,
            folder=archive_folder.get("name"),
        )
        return result

    @staticmethod
    def replace_file_links(html: str, file_id: int, new_page_url: str) -> tuple[str, int]:
        """Replace all links to a Canvas file with a link to a new page.

        Matches patterns like:
          https://host/courses/X/files/{file_id}
          /courses/X/files/{file_id}
          /courses/X/files/{file_id}/download
          /courses/X/files/{file_id}/preview
          /courses/X/files/{file_id}/download/{filename}
          /files/{file_id}/download
          /files/{file_id}
          ...with optional `?wrap=1`, `?download_frd=1`, `?verifier=...` query

        CLU-73 fix: the regex now optionally consumes the `https?://host`
        prefix when present. Without this, callers passing absolute
        `new_page_url` (e.g., from Canvas's `html_url` field via
        `conversion_service.canvas_page_url`) produced corrupted
        `https://X.comhttps://X.com/...` output because only the
        path portion was substituted while the host prefix stayed.

        Returns (updated_html, replacements_count).
        """
        pattern = re.compile(
            rf'(?:https?://[^/\s"\'>]+)?'
            rf'(?:/courses/\d+)?/files/{file_id}'
            rf'(?:/(?:download|preview)(?:/[^?\s"\'>]*)?)?'
            rf'(?:\?[^"\'>\s]*)?',
        )
        count = len(pattern.findall(html))
        if count == 0:
            return html, 0
        updated = pattern.sub(new_page_url, html)
        return updated, count
