"""Document version history: snapshot before remediation, restore."""

from datetime import UTC, datetime

import structlog
from ulid import ULID

from lti_app.canvas.client import CanvasClient
from lti_app.canvas.content_fetcher import ContentFetcher
from lti_app.canvas.content_writer import ContentWriter
from lti_app.db.repositories import VersionRepository
from lti_app.models import ContentVersion, CoursePage

_logger = structlog.get_logger(__name__)


class VersionService:
    """Manages pre-remediation HTML snapshots and restore."""

    def __init__(self, version_repo: VersionRepository):
        self._repo = version_repo

    def snapshot_page(self, course_id: str, page: CoursePage, created_by: str, job_id: str | None = None) -> ContentVersion:
        """Store a snapshot of the page's current HTML."""
        version = ContentVersion(
            id=str(ULID()),
            course_id=course_id,
            page_id=page.id,
            content_type=page.content_type,
            html_content=page.html_content,
            created_at=datetime.now(UTC),
            created_by=created_by,
            remediation_job_id=job_id,
        )
        self._repo.save_version(version)
        return version

    def get_versions(self, course_id: str, page_id: str) -> list[ContentVersion]:
        """List all versions for a page, newest first."""
        return self._repo.get_versions(course_id, page_id)

    def get_version(self, version_id: str) -> ContentVersion | None:
        return self._repo.get_version(version_id)

    async def restore_version(self, version_id: str, session, course_id: int) -> bool:
        """Restore a version by writing its HTML back to Canvas."""
        version = self._repo.get_version(version_id)
        if not version:
            return False

        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )

        try:
            # Find the current page
            fetcher = ContentFetcher(client)
            all_pages = await fetcher.fetch_all(course_id)
            page = next((p for p in all_pages if p.id == version.page_id), None)
            if not page:
                return False

            # Snapshot current state before restoring (so restore itself is reversible)
            self.snapshot_page(course_id=str(course_id), page=page, created_by="manual")

            writer = ContentWriter(client)
            await writer.write_page_content(course_id, page, version.html_content)
            return True
        finally:
            await client.close()
