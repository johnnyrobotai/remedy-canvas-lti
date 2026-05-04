"""Background file audit orchestrator: discover files, check PDFs."""

import os
import tempfile
from datetime import UTC, datetime

import structlog
from ulid import ULID

from lti_app.canvas.client import CanvasClient
from lti_app.canvas.file_manager import FileManager
from lti_app.core.documents.liteparse_adapter import LiteParseAdapter, SpatialParseResult
from lti_app.core.pdf.checker import PDFAccessibilityChecker
from lti_app.core.pdf.models import CheckReport
from lti_app.db.repositories import FileAuditRepository
from lti_app.models import (
    CheckReportRef,
    FileAuditEntry,
    FileAuditJob,
    FileReport,
    ScanStatus,
)

_logger = structlog.get_logger(__name__)


class FileAuditService:
    """Orchestrates file discovery and PDF accessibility checking."""

    def __init__(self, file_audit_repo: FileAuditRepository):
        self._repo = file_audit_repo

    def create_job(self, session_id: str, course_id: str) -> FileAuditJob:
        """Create a PENDING file audit job."""
        job = FileAuditJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            status=ScanStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        self._repo.save_job(job)
        return job

    async def run_audit(self, job_id: str, session, course_id: int) -> None:
        """Execute file audit as a background task."""
        job = self._repo.get_job(job_id)
        if not job:
            return

        try:
            job.status = ScanStatus.RUNNING
            self._repo.save_job(job)

            client = CanvasClient(
                base_url=session.canvas_base_url,
                access_token=session.canvas_access_token,
                refresh_token=getattr(session, "canvas_refresh_token", ""),
            )

            try:
                fm = FileManager(client)
                files = await fm.list_course_files(course_id)
                job.files_total = len(files)
                self._repo.save_job(job)

                entries: list[FileAuditEntry] = []
                pdf_total = sum(1 for f in files if f.get("content-type") == "application/pdf")

                for file_info in files:
                    file_id = file_info["id"]
                    filename = file_info.get("display_name", f"file_{file_id}")
                    content_type = file_info.get("content-type", "")
                    size = file_info.get("size", 0)
                    is_pdf = content_type == "application/pdf"

                    if is_pdf:
                        try:
                            data, _ = await fm.download_file(file_id, filename)
                            check_report = self._check_pdf(data, str(file_id), filename)
                            ref = CheckReportRef(
                                total_checks=check_report.total_checks,
                                passed=check_report.passed,
                                failed=check_report.failed,
                                not_applicable=check_report.not_applicable,
                                errors=check_report.errors,
                                pass_rate=check_report.pass_rate,
                            )
                            status = "passed" if check_report.failed == 0 else "failed"
                            # Stamp remediation_status so the ACR's
                            # _file_outcome_breakdown can surface
                            # "N PDFs passed accessibility audit"
                            # under 1.1.1 remarks — otherwise a course
                            # with no failed PDFs still shows a bare
                            # "PDF001 (6)" warning and the instructor
                            # can't tell the linked PDFs were already
                            # checked.
                            remediation_status = (
                                "audit_passed" if status == "passed" else None
                            )
                            entries.append(FileAuditEntry(
                                file_id=file_id, filename=filename,
                                content_type=content_type, size=size,
                                is_pdf=True, check_report=ref, status=status,
                                remediation_status=remediation_status,
                            ))
                        except Exception as e:
                            _logger.warning("pdf_check_failed", file_id=file_id, error=str(e))
                            entries.append(FileAuditEntry(
                                file_id=file_id, filename=filename,
                                content_type=content_type, size=size,
                                is_pdf=True, status="failed",
                            ))

                        job.files_audited += 1
                        job.progress = job.files_audited / max(pdf_total, 1)
                        self._repo.save_job(job)
                    else:
                        entries.append(FileAuditEntry(
                            file_id=file_id, filename=filename,
                            content_type=content_type, size=size,
                            is_pdf=False, status="not_audited",
                        ))

                # Build report
                pdf_entries = [e for e in entries if e.is_pdf]
                report_id = str(ULID())
                report = FileReport(
                    course_id=str(course_id),
                    audited_at=datetime.now(UTC),
                    total_files=len(entries),
                    pdf_count=len(pdf_entries),
                    pdfs_passed=sum(1 for e in pdf_entries if e.status == "passed"),
                    pdfs_failed=sum(1 for e in pdf_entries if e.status == "failed"),
                    entries=entries,
                )
                self._repo.save_report(report_id, report)

                job.report_id = report_id
                job.status = ScanStatus.COMPLETED
                job.completed_at = datetime.now(UTC)
                self._repo.save_job(job)

            finally:
                await client.close()

        except Exception as e:
            _logger.error("file_audit_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)

    async def get_spatial_layout(
        self,
        file_id: int,
        filename: str,
        session,
        course_id: int,
    ) -> SpatialParseResult:
        """Download a file and parse it with LiteParse for spatial layout.

        Args:
            file_id: Canvas file ID.
            filename: Display name of the file (used for format detection).
            session: LTI session with Canvas credentials.
            course_id: Canvas course ID (used only for logging context).

        Returns:
            SpatialParseResult with page layouts and bounding boxes.
        """
        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        try:
            fm = FileManager(client)
            data, resolved_filename = await fm.download_file(file_id, filename)
        finally:
            await client.close()

        _logger.info(
            "spatial_parse_start",
            file_id=file_id,
            filename=resolved_filename,
            course_id=course_id,
            size=len(data),
        )

        adapter = LiteParseAdapter(ocr_enabled=True, dpi=150)
        result = adapter.parse_bytes(data, resolved_filename)

        _logger.info(
            "spatial_parse_complete",
            file_id=file_id,
            pages=len(result.pages),
            total_text_items=result.total_text_items,
        )
        return result

    @staticmethod
    def _check_pdf(data: bytes, file_id: str, filename: str) -> CheckReport:
        """Write PDF bytes to temp file, run checker, clean up."""
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(data)
            tmp.close()
            checker = PDFAccessibilityChecker(tmp.name)
            return checker.run_all(file_id=file_id, filename=filename)
        finally:
            os.unlink(tmp.name)
