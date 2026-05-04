"""Multi-course batch operations service."""

from datetime import UTC, datetime

import structlog
from ulid import ULID

from lti_app.db.repositories import ScanRepository, SettingsRepository
from lti_app.models import BatchJob, ScanStatus

_logger = structlog.get_logger(__name__)


class BatchService:
    """Orchestrates batch operations across multiple courses."""

    def __init__(
        self,
        settings_repo: SettingsRepository,
        scan_repo: ScanRepository,
    ):
        self._settings_repo = settings_repo
        self._scan_repo = scan_repo

    def create_batch_job(
        self,
        deployment_id: str,
        course_ids: list[str],
        operation: str,
    ) -> BatchJob:
        """Create a PENDING batch job."""
        if operation not in ("scan", "remediate", "audit_files"):
            raise ValueError(f"Invalid batch operation: {operation}")

        job = BatchJob(
            id=str(ULID()),
            deployment_id=deployment_id,
            course_ids=course_ids,
            operation=operation,
            status=ScanStatus.PENDING,
            courses_total=len(course_ids),
            created_at=datetime.now(UTC),
        )
        self._save_batch_job(job)
        return job

    def get_batch_job(self, job_id: str) -> BatchJob | None:
        """Retrieve a batch job by ID."""
        data = self._settings_repo.get_settings(f"batch_job:{job_id}")
        if data is None:
            return None
        return BatchJob.model_validate(data)

    async def run_batch(
        self,
        job_id: str,
        session,
        operation: str,
        course_ids: list[str],
    ) -> None:
        """Execute the batch operation across all courses.

        This is meant to be called as a background task.
        Each course operation is run sequentially; progress is updated
        after each course completes.
        """
        job = self.get_batch_job(job_id)
        if not job:
            _logger.error("batch_job_not_found", job_id=job_id)
            return

        try:
            job.status = ScanStatus.RUNNING
            self._save_batch_job(job)

            for i, course_id in enumerate(course_ids):
                try:
                    _logger.info(
                        "batch_course_start",
                        job_id=job_id,
                        course_id=course_id,
                        operation=operation,
                    )

                    if operation == "scan":
                        await self._run_scan_for_course(session, course_id)
                    elif operation == "remediate":
                        await self._run_remediate_for_course(session, course_id)
                    elif operation == "audit_files":
                        await self._run_audit_for_course(session, course_id)

                    job.results[course_id] = "completed"
                except Exception as e:
                    _logger.error(
                        "batch_course_failed",
                        job_id=job_id,
                        course_id=course_id,
                        error=str(e),
                    )
                    job.results[course_id] = f"failed: {str(e)[:200]}"

                job.courses_completed = i + 1
                job.progress = (i + 1) / len(course_ids)
                self._save_batch_job(job)

            job.status = ScanStatus.COMPLETED
            job.completed_at = datetime.now(UTC)
        except Exception as e:
            _logger.error("batch_job_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)[:500]
            job.completed_at = datetime.now(UTC)
        finally:
            self._save_batch_job(job)

    async def _run_scan_for_course(self, session, course_id: str) -> None:
        """Run an accessibility scan for a single course within a batch."""
        from lti_app.services.scan_service import ScanService

        svc = ScanService(self._scan_repo)
        job = svc.create_scan_job(session.session_id, course_id)
        await svc.run_scan(job.id, session, int(course_id))

    async def _run_remediate_for_course(self, session, course_id: str) -> None:
        """Run remediation for a single course within a batch."""
        # Remediation requires a scan report to exist first
        from lti_app.db.repositories import get_remediation_repository
        from lti_app.models import RemediationRequest
        from lti_app.services.remediation_service import RemediationService

        remediation_repo = get_remediation_repository()
        svc = RemediationService(self._scan_repo, remediation_repo)
        request = RemediationRequest()
        job = svc.create_remediation_job(session.session_id, course_id, request)
        await svc.run_remediation(job.id, session, int(course_id))

    async def _run_audit_for_course(self, session, course_id: str) -> None:
        """Run file audit for a single course within a batch."""
        from lti_app.db.repositories import get_file_audit_repository
        from lti_app.services.file_audit_service import FileAuditService

        file_audit_repo = get_file_audit_repository()
        svc = FileAuditService(file_audit_repo)
        job = svc.create_audit_job(session.session_id, course_id)
        await svc.run_audit(job.id, session, int(course_id))

    def _save_batch_job(self, job: BatchJob) -> None:
        """Persist a batch job using the settings repository."""
        self._settings_repo.save_settings(
            f"batch_job:{job.id}",
            job.model_dump(mode="json"),
        )
