"""PDF fix + verify + upload orchestrator."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog
from ulid import ULID

from lti_app.canvas.client import CanvasClient
from lti_app.canvas.file_manager import FileManager
from lti_app.core.pdf.checker import PDFAccessibilityChecker
from lti_app.core.pdf.fixer import fix_all
from lti_app.db.repositories import FileAuditRepository, PDFFixRepository
from lti_app.models import CheckReportRef, PDFFixJob, ScanStatus

_logger = structlog.get_logger(__name__)

_FIX_TEMP_DIR = Path(tempfile.gettempdir()) / "canvas-remedy-lti-fixes"


class PDFFixService:
    """Orchestrates PDF fix, verification, and upload."""

    def __init__(self, pdf_fix_repo: PDFFixRepository, file_audit_repo: FileAuditRepository):
        self._repo = pdf_fix_repo
        self._file_audit_repo = file_audit_repo

    def create_job(
        self,
        session_id: str,
        course_id: str,
        file_id: int,
        filename: str,
        checks_before: CheckReportRef,
    ) -> PDFFixJob:
        """Create a PENDING PDF fix job."""
        job = PDFFixJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            file_id=file_id,
            filename=filename,
            checks_before=checks_before,
            created_at=datetime.now(UTC),
        )
        self._repo.save_job(job)
        return job

    async def run_fix(self, job_id: str, session, course_id: int) -> None:
        """Execute PDF fix as a background task.

        Downloads the PDF, applies all automated fixes, runs the accessibility
        checker on the output for verification, and stores the before/after
        CheckReportRef on the job.
        """
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
                data, _ = await fm.download_file(job.file_id, job.filename)

                # Write input to temp
                _FIX_TEMP_DIR.mkdir(parents=True, exist_ok=True)
                in_path = _FIX_TEMP_DIR / f"{job.id}_input.pdf"
                out_path = _FIX_TEMP_DIR / f"{job.id}_output.pdf"

                in_path.write_bytes(data)

                try:
                    # Run fixer
                    fix_report = fix_all(str(in_path), str(out_path))
                    job.fixes_applied = fix_report.fixes_applied
                    job.fixes_skipped = fix_report.fixes_skipped

                    # Verify output
                    checker = PDFAccessibilityChecker(str(out_path))
                    check_report = checker.run_all(
                        file_id=str(job.file_id), filename=job.filename
                    )
                    job.checks_after = CheckReportRef(
                        total_checks=check_report.total_checks,
                        passed=check_report.passed,
                        failed=check_report.failed,
                        not_applicable=check_report.not_applicable,
                        errors=check_report.errors,
                        pass_rate=check_report.pass_rate,
                    )

                    job.status = ScanStatus.COMPLETED
                    job.completed_at = datetime.now(UTC)
                    self._repo.save_job(job)

                    # Stamp the audit entry so the ACR can suppress the
                    # residual PDF001 warning for this file. Only mark
                    # "pdf_fixed" when the post-fix checker actually
                    # shows improvement — a no-op fix leaves the PDF as
                    # inaccessible as before and should still surface in
                    # the ACR. Best-effort across all reports for the
                    # course because pdf fix jobs don't carry the
                    # originating audit report id today.
                    improved = (
                        job.checks_after
                        and job.checks_before
                        and job.checks_after.pass_rate > job.checks_before.pass_rate
                    )
                    if improved:
                        try:
                            latest_report_id = (
                                self._file_audit_repo.get_latest_report_id(
                                    str(job.course_id)
                                )
                            )
                            if latest_report_id:
                                self._file_audit_repo.update_entry(
                                    latest_report_id, job.file_id,
                                    remediation_status="pdf_fixed",
                                )
                        except Exception as audit_err:
                            _logger.warning(
                                "pdf_fix_audit_update_failed",
                                job_id=job.id,
                                error=str(audit_err),
                            )

                finally:
                    # Clean up input (keep output for upload)
                    if in_path.exists():
                        in_path.unlink()

            finally:
                await client.close()

        except Exception as e:
            _logger.error("pdf_fix_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)

    async def upload_fixed(
        self,
        job_id: str,
        session,
        course_id: int,
        mode: str,
    ) -> dict:
        """Upload fixed PDF to Canvas.

        Args:
            job_id: PDFFixJob ID.
            session: LTI session with canvas_base_url and canvas_access_token.
            course_id: Canvas course ID.
            mode: "replace" overwrites original location; "alongside" uploads to
                  an "Accessible Documents" folder with ``_accessible`` suffix.

        Returns:
            Dict with file_id, filename, and url of the uploaded file.
        """
        job = self._repo.get_job(job_id)
        if not job or job.status != ScanStatus.COMPLETED:
            raise ValueError("Job not found or not completed")

        out_path = _FIX_TEMP_DIR / f"{job.id}_output.pdf"
        if not out_path.exists():
            raise FileNotFoundError("Fixed PDF not found (expired?)")

        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )

        try:
            fm = FileManager(client)
            data = out_path.read_bytes()

            if mode == "replace":
                # Upload with same name to course root folder
                folders = await fm.get_course_folders(course_id)
                root_folder = next(
                    (f for f in folders if f.get("parent_folder_id") is None),
                    folders[0] if folders else None,
                )
                if root_folder:
                    result = await fm.upload_file(
                        course_id,
                        root_folder["id"],
                        job.filename,
                        data,
                        "application/pdf",
                    )
                else:
                    raise ValueError("No folder found in course")
            else:
                # Upload to "Accessible Documents" folder with _accessible suffix
                folders = await fm.get_course_folders(course_id)
                acc_folder = next(
                    (f for f in folders if f.get("name") == "Accessible Documents"),
                    None,
                )
                if not acc_folder:
                    acc_folder = await fm.create_folder(course_id, "Accessible Documents")

                name_parts = job.filename.rsplit(".", 1)
                new_name = (
                    f"{name_parts[0]}_accessible.{name_parts[1]}"
                    if len(name_parts) == 2
                    else f"{job.filename}_accessible"
                )
                result = await fm.upload_file(
                    course_id,
                    acc_folder["id"],
                    new_name,
                    data,
                    "application/pdf",
                )

            job.upload_mode = mode
            job.fixed_file_id = result.get("id")
            self._repo.save_job(job)

            # Clean up temp file
            out_path.unlink(missing_ok=True)

            return {
                "file_id": result.get("id"),
                "filename": result.get("display_name", job.filename),
                "url": result.get("url", ""),
            }

        finally:
            await client.close()
