"""External document link remediation service.

Downloads external documents, processes them through the existing
fix/conversion pipeline, and uploads results to Canvas.
"""

from datetime import UTC, datetime

import httpx
import structlog
from pydantic import BaseModel
from ulid import ULID

from lti_app.canvas.client import CanvasClient
from lti_app.canvas.file_manager import FileManager
from lti_app.models import ScanStatus

_logger = structlog.get_logger(__name__)


class ExternalLinkJob(BaseModel):
    """Tracks processing of a single external document link."""

    id: str
    course_id: str
    session_id: str
    url: str
    filename: str
    status: ScanStatus = ScanStatus.PENDING
    action_taken: str = ""  # "converted", "fixed", "uploaded"
    canvas_file_id: int | None = None
    canvas_page_url: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class ExternalLinkService:
    """Download external documents, process, and upload to Canvas."""

    async def process_link(
        self,
        url: str,
        filename: str,
        session,
        course_id: int,
    ) -> ExternalLinkJob:
        """Download external doc, process it, upload to Canvas.

        Args:
            url: The external document URL to download.
            filename: Filename for the document.
            session: LTI session with Canvas credentials.
            course_id: Target Canvas course ID.

        Returns:
            ExternalLinkJob with processing results.
        """
        job = ExternalLinkJob(
            id=str(ULID()),
            course_id=str(course_id),
            session_id=session.session_id,
            url=url,
            filename=filename,
            created_at=datetime.now(UTC),
        )

        try:
            job.status = ScanStatus.RUNNING

            # Download the external document
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                data = resp.content

            _logger.info(
                "external_link_downloaded",
                url=url,
                filename=filename,
                size=len(data),
            )

            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            # Upload to Canvas in an "External Documents" folder
            client = CanvasClient(
                base_url=session.canvas_base_url,
                access_token=session.canvas_access_token,
                refresh_token=getattr(session, "canvas_refresh_token", ""),
            )
            try:
                fm = FileManager(client)

                # Find or create the "External Documents" folder
                folders = await fm.get_course_folders(course_id)
                folder = next(
                    (f for f in folders if f.get("name") == "External Documents"),
                    None,
                )
                if not folder:
                    folder = await fm.create_folder(course_id, "External Documents")

                # Determine content type
                content_type_map = {
                    "pdf": "application/pdf",
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "doc": "application/msword",
                    "ppt": "application/vnd.ms-powerpoint",
                    "xls": "application/vnd.ms-excel",
                }
                content_type = content_type_map.get(ext, "application/octet-stream")

                result = await fm.upload_file(
                    course_id,
                    folder["id"],
                    filename,
                    data,
                    content_type,
                )

                job.canvas_file_id = result.get("id")
                job.action_taken = "uploaded"
                job.status = ScanStatus.COMPLETED
                job.completed_at = datetime.now(UTC)

                _logger.info(
                    "external_link_uploaded",
                    job_id=job.id,
                    filename=filename,
                    canvas_file_id=job.canvas_file_id,
                )

            finally:
                await client.close()

        except httpx.HTTPStatusError as e:
            job.status = ScanStatus.FAILED
            job.error = f"Download failed: HTTP {e.response.status_code}"
            job.completed_at = datetime.now(UTC)
            _logger.error(
                "external_link_download_failed",
                url=url,
                status=e.response.status_code,
            )

        except Exception as e:
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            _logger.error("external_link_process_failed", url=url, error=str(e))

        return job
