"""Course-wide one-click remediation orchestrator (AutoRemedy)."""

from datetime import UTC, datetime, timedelta

import structlog
from ulid import ULID

# Reuse an existing scan report in AutoRemedy phase 1 if it's newer than this.
# A user who just ran a full scan and clicks "Fix My Course" shouldn't have to
# sit through another 2-5 minute rescan — the data they just generated is the
# same data AutoRemedy would gather. After this window the course may have
# drifted enough (instructor edits, new uploads) that a fresh scan is worth it.
_SCAN_REUSE_WINDOW = timedelta(minutes=60)

from lti_app.db.repositories import (
    AutoRemedyRepository,
    FileAuditRepository,
    PDFFixRepository,
    RemediationRepository,
    ScanRepository,
    get_exclusion_repository,
)
from lti_app.models import (
    AutoRemedyJob,
    Campus,
    RemediationRequest,
    ScanStatus,
)
from lti_app.services.conversion_service import (
    ConversionService,
    CONVERTIBLE_TYPES,
    should_skip_by_filename,
)
from lti_app.services.file_audit_service import FileAuditService
from lti_app.services.remediation_service import RemediationService

_logger = structlog.get_logger(__name__)


_MAX_LOG_ENTRIES = 50  # Keep last N activity log entries


class AutoRemedyCancelled(BaseException):
    """Raised by cooperative cancellation checks when cancel_requested=True.

    Inherits from BaseException (not Exception) so it propagates cleanly
    through generic ``except Exception`` handlers — including the per-page
    callback wrapper inside RemediationService and the inner-loop catches
    in the conversion phase. Standard pattern: asyncio.CancelledError and
    SystemExit do the same.

    Caught by run_autoremedy's outer handler, which marks the job as failed
    with a friendly "Cancelled by user request" error so the UI can render
    the failure card cleanly (CLU-64).
    """


class AutoRemedyService:
    """Orchestrates scan -> remediate -> file audit -> PDF fix as one job."""

    def __init__(self, autoremedy_repo, scan_repo, remediation_repo, file_audit_repo, pdf_fix_repo=None, conversion_repo=None):
        self._repo = autoremedy_repo
        self._scan_repo = scan_repo
        self._remediation_repo = remediation_repo
        self._file_audit_repo = file_audit_repo
        self._pdf_fix_repo = pdf_fix_repo
        self._conversion_repo = conversion_repo

    def _save(self, job: AutoRemedyJob) -> None:
        """Save the job to the repo, preserving externally-set cancel flag.

        The cancel endpoint writes ``cancel_requested=true`` to the JSONB out
        of band. Without re-reading it before each save, the orchestrator's
        per-page in-memory copy (with ``cancel_requested=False``) would
        clobber the signal as soon as the next page progress write fires
        — the user's click would be silently overwritten within milliseconds
        and the cancel never takes effect (CLU-64).
        """
        fresh = self._repo.get_job(job.id)
        if fresh and fresh.cancel_requested:
            job.cancel_requested = True
        self._repo.save_job(job)

    def _log(self, job: AutoRemedyJob, message: str) -> None:
        """Append a message to the job's activity log and persist."""
        job.activity_log.append(message)
        if len(job.activity_log) > _MAX_LOG_ENTRIES:
            job.activity_log = job.activity_log[-_MAX_LOG_ENTRIES:]
        self._save(job)

    def _check_cancel(self, job: AutoRemedyJob) -> None:
        """Refresh cancel_requested from the DB and raise if set.

        The cancel flag is set by a separate HTTP request (POST /cancel) so
        we can't trust the in-memory job object. Re-read the JSONB on each
        check. Cheap because there are at most a few hundred check points
        per job (phase boundaries + per-page callback).
        """
        latest = self._repo.get_job(job.id)
        if latest and latest.cancel_requested:
            job.cancel_requested = True
            raise AutoRemedyCancelled("Cancelled by user request")

    def _record_audit_outcome(
        self,
        file_report_id: str | None,
        file_id: int,
        *,
        skip_reason: str | None = None,
        remediation_status: str | None = None,
    ) -> None:
        """Best-effort update of a file-audit entry's outcome fields.

        The ACR grouping for LNK007/PDF001/DOC001 reads these back to
        explain per-file remediation outcomes. Failures here are
        logged but never propagated — skipped or failed conversions
        must still continue the AutoRemedy loop.
        """
        if not file_report_id:
            return
        try:
            self._file_audit_repo.update_entry(
                file_report_id,
                file_id,
                skip_reason=skip_reason,
                remediation_status=remediation_status,
            )
        except Exception as exc:
            _logger.warning(
                "autoremedy_audit_update_failed",
                file_report_id=file_report_id,
                file_id=file_id,
                error=str(exc),
            )

    def create_job(self, session_id: str, course_id: str) -> AutoRemedyJob:
        job = AutoRemedyJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            status=ScanStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        self._save(job)
        return job

    async def run_autoremedy(
        self,
        job_id: str,
        session,
        course_id: int,
        skip_page_identifiers: set[str] | None = None,
        skip_file_ids: set[int] | None = None,
    ) -> None:
        """Run the full AutoRemedy pipeline (CLU-85 selective support).

        ``skip_page_identifiers`` and ``skip_file_ids`` are merged with
        the course's permanent exclusions table at run start. The union
        is threaded through phase 2 (HTML remediation) and phase 4
        (document conversion + link replacement). When both kwargs are
        None or empty AND the exclusions table is empty, behaves
        identically to the pre-CLU-85 Fix My Course path.

        Task 7 only plumbs these kwargs through. The actual filter logic
        inside phases 2 and 4 is added by Tasks 8 and 9 respectively —
        until then, the phase helpers accept the kwargs but ignore them.
        """
        job = self._repo.get_job(job_id)
        if not job:
            return

        # Normalize optional arguments
        skip_page_identifiers = set(skip_page_identifiers or set())
        skip_file_ids = set(skip_file_ids or set())

        # CLU-85: merge permanent exclusions from the DB with the
        # request-supplied skip lists. The course_exclusions table is the
        # source of truth for "always skip this item" and must be honored
        # on every run including Fix My Course.
        exclusion_repo = get_exclusion_repository()
        permanent = exclusion_repo.get_identifiers_for_course(str(course_id))
        for ident in permanent:
            if ident.startswith("file-"):
                try:
                    skip_file_ids.add(int(ident.removeprefix("file-")))
                except ValueError:
                    # Malformed file identifier in the DB — log and skip
                    _logger.warning(
                        "autoremedy_invalid_file_exclusion_identifier",
                        course_id=course_id,
                        identifier=ident,
                    )
            else:
                skip_page_identifiers.add(ident)

        try:
            job.status = ScanStatus.RUNNING
            self._save(job)

            # Phase 1: Scan (0% → 25%)
            self._check_cancel(job)
            job.phase = "scanning"
            job.progress = 0.0
            self._save(job)
            await self._run_scan_phase(job, session, course_id)

            # Phase 2: Remediate HTML (25% → 50%)
            self._check_cancel(job)
            job.phase = "remediating_html"
            job.progress = 0.25
            self._save(job)
            await self._run_remediation_phase(
                job,
                session,
                course_id,
                skip_page_identifiers=skip_page_identifiers,
                skip_file_ids=skip_file_ids,
            )

            # Phase 3: Audit files (50% → 75%)
            self._check_cancel(job)
            job.phase = "auditing_files"
            job.progress = 0.50
            self._save(job)
            await self._run_file_audit_phase(job, session, course_id)

            # Phase 4: Convert documents (75% → 100%)
            self._check_cancel(job)
            job.phase = "converting_documents"
            job.progress = 0.75
            self._save(job)
            await self._run_convert_and_replace_phase(
                job,
                session,
                course_id,
                skip_page_identifiers=skip_page_identifiers,
                skip_file_ids=skip_file_ids,
            )

            # Phase 5: Post-remediation rescan (shows updated numbers on dashboard)
            # Must force a fresh scan — reusing the pre-remediation report would
            # defeat the whole purpose of this phase.
            self._check_cancel(job)
            job.phase = "rescanning"
            job.progress = 0.90
            self._save(job)
            self._log(job, "Running post-remediation scan...")
            await self._run_scan_phase(job, session, course_id, force_fresh=True)
            self._log(job, "Post-remediation scan complete")

            # Complete
            job.phase = "complete"
            job.progress = 1.0
            job.status = ScanStatus.COMPLETED
            job.completed_at = datetime.now(UTC)
            self._save(job)

        except AutoRemedyCancelled:
            _logger.info("autoremedy_cancelled", job_id=job_id, phase=job.phase)
            self._log(job, f"Cancelled during phase: {job.phase}")
            job.status = ScanStatus.FAILED
            job.error = "Cancelled by user request"
            job.completed_at = datetime.now(UTC)
            self._save(job)
        except Exception as e:
            _logger.error("autoremedy_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._save(job)

    async def _run_scan_phase(self, job, session, course_id, force_fresh: bool = False):
        """Phase 1: Run HTML accessibility scan (reusing a recent report if possible).

        If the course already has a scan report newer than ``_SCAN_REUSE_WINDOW``
        we adopt it as-is instead of rescanning. This matters because the user's
        typical flow is:

          1. Manual scan (2-5 minutes)
          2. Look at the results
          3. Click "Fix My Course"

        Before this change, step 3 silently re-ran the scan from scratch, adding
        a gratuitous 2-5 minutes and throwing away the report the user was just
        looking at. Now we only re-scan if the existing data is stale enough to
        be misleading.

        Pass ``force_fresh=True`` for the post-remediation rescan (phase 5) —
        that call *must* be fresh because its whole job is to measure the
        after-state of the remediation.
        """
        from lti_app.services.scan_service import ScanService

        # Prefer a recent existing report over a fresh scan — unless the caller
        # needs guaranteed-fresh data (post-remediation rescan).
        if not force_fresh:
            latest_id = self._scan_repo.get_latest_report_id(str(course_id))
            latest_report = (
                self._scan_repo.get_report(latest_id) if latest_id else None
            )
            if latest_id and latest_report and latest_report.analyzed_at:
                analyzed_at = latest_report.analyzed_at
                if analyzed_at.tzinfo is None:
                    analyzed_at = analyzed_at.replace(tzinfo=UTC)
                age = datetime.now(UTC) - analyzed_at
                if age < _SCAN_REUSE_WINDOW:
                    minutes_old = int(age.total_seconds() // 60)
                    self._log(
                        job,
                        f"Reusing recent scan ({latest_report.pages_analyzed} pages, "
                        f"{minutes_old} minutes old)",
                    )
                    job.scan_report_id = latest_id
                    job.html_pages_total = latest_report.pages_analyzed
                    job.issues_found = latest_report.total_issues
                    errors = sum(1 for i in latest_report.issues if i.severity.value == "error")
                    warnings = sum(1 for i in latest_report.issues if i.severity.value == "warning")
                    self._log(
                        job,
                        f"Scan summary: {latest_report.pages_analyzed} pages, "
                        f"{errors} errors, {warnings} warnings",
                    )
                    self._save(job)
                    return

        # Fall through: no recent report, run a fresh scan.
        self._log(job, "Starting accessibility scan...")
        scan_svc = ScanService(self._scan_repo)
        scan_job = scan_svc.create_scan_job(
            job.session_id,
            str(course_id),
            scan_mode="content_only",
        )
        await scan_svc.run_scan(scan_job.id, session, course_id)

        completed_scan = self._scan_repo.get_job(scan_job.id)
        if (
            not completed_scan
            or completed_scan.status != ScanStatus.COMPLETED
            or not completed_scan.report_id
        ):
            raise RuntimeError(
                f"AutoRemedy scan did not produce a report for course {course_id}"
            )

        job.scan_report_id = completed_scan.report_id
        report = self._scan_repo.get_report(completed_scan.report_id)
        if not report:
            raise RuntimeError(
                f"AutoRemedy scan report {completed_scan.report_id} could not be loaded"
            )

        job.html_pages_total = report.pages_analyzed
        job.issues_found = report.total_issues
        errors = sum(1 for i in report.issues if i.severity.value == "error")
        warnings = sum(1 for i in report.issues if i.severity.value == "warning")
        self._log(job, f"Scan complete: {report.pages_analyzed} pages, {errors} errors, {warnings} warnings")
        self._save(job)

    async def _run_remediation_phase(
        self,
        job,
        session,
        course_id,
        skip_page_identifiers: set[str] | None = None,
        skip_file_ids: set[int] | None = None,
    ):
        """Phase 2: Remediate all auto-fixable HTML issues.

        CLU-85: ``skip_page_identifiers`` filters items out of the scan
        report BEFORE RemediationService runs (Task 8). Skipped pages
        are never fetched, never transformed, and never written back to
        Canvas. ``skip_file_ids`` is accepted for symmetry with phase
        4 but is not consumed by phase 2 — file-level skips only matter
        in the conversion phase.
        """
        self._log(job, "Starting AI-powered remediation...")

        # CLU-85 Task 8: filter out issues for items the user (or
        # permanent exclusions) asked to skip. RemediationService re-
        # loads the scan report from the scan repo by id, so the only
        # way to keep skipped pages out of the work list is to mutate
        # the persisted report's `issues` list and save it back. This
        # is purely additive — when skip_page_identifiers is empty
        # (Fix My Course's default) the block is a no-op and behaves
        # identically to pre-Task-8.
        skip_page_identifiers = skip_page_identifiers or set()
        if skip_page_identifiers and job.scan_report_id:
            report = self._scan_repo.get_report(job.scan_report_id)
            if report:
                before_count = len(report.issues)
                report.issues = [
                    i
                    for i in report.issues
                    if (i.page_identifier or i.page_id) not in skip_page_identifiers
                ]
                filtered_count = before_count - len(report.issues)
                if filtered_count:
                    self._scan_repo.save_report(job.scan_report_id, report)
                    self._log(
                        job,
                        f"Skipping {filtered_count} issues on "
                        f"{len(skip_page_identifiers)} excluded items",
                    )

        rem_svc = RemediationService(self._scan_repo, self._remediation_repo)
        # AI alt-text generation is handled by the pre-loop vision pipeline.
        # Layer 3 (AIRemediator) HTML replacement is disabled — LLMs restructure
        # HTML in destructive ways even with content-loss guards.
        # generate_alt_text was hot-patched to False during the CLU-49/CLU-50
        # incidents to dodge the unbounded Ollama retry loop. CLU-58 added a
        # global per-job budget so the AI alt-text path can run without
        # stalling — re-enabled here, protected by alt_text_global_budget_seconds.
        request = RemediationRequest(campus=Campus.LACCD, generate_alt_text=True, use_ai_remediation=False)
        rem_job = rem_svc.create_remediation_job(job.session_id, str(course_id), request)
        rem_job.scan_report_id = job.scan_report_id
        self._remediation_repo.save_job(rem_job)
        job.remediation_job_id = rem_job.id
        self._save(job)

        # Per-page progress callback (CLU-56). The remediation phase is the slow
        # one — without per-page writes the dashboard's progress bar sits at 25%
        # for the entire phase even though pages are actively being fixed. We
        # advance our own counters here so the polling endpoint surfaces real
        # progress, and we map the inner percentage into the 25%-50% slice we
        # allocated to this phase. Also: cooperative cancel check (CLU-64) so
        # the user can stop a long-running remediation between pages.
        def _on_page(idx: int, total: int, pages_written: int) -> None:
            self._check_cancel(job)
            job.html_pages_remediated = pages_written
            job.progress = 0.25 + (idx / max(total, 1)) * 0.25
            self._save(job)

        remediated_ids = await rem_svc.run_remediation(
            rem_job.id, session, course_id,
            on_page_activity=lambda msg: self._log(job, msg),
            on_page_complete=_on_page,
            auto_apply=True,
        )

        completed_rem = self._remediation_repo.get_job(rem_job.id)
        if completed_rem:
            job.html_pages_remediated = len(remediated_ids)

        previews = self._remediation_repo.get_previews(rem_job.id)
        remediated_id_set = set(remediated_ids)
        job.issues_fixed = sum(
            len(preview.issues_fixed)
            for preview in previews
            if preview.page_id in remediated_id_set
        )
        if not remediated_ids:
            self._log(job, "No auto-fixable issues found")
        self._save(job)

    async def _run_file_audit_phase(self, job, session, course_id):
        """Phase 3: Audit all course files."""
        self._log(job, "Auditing course files...")
        audit_svc = FileAuditService(self._file_audit_repo)
        audit_job = audit_svc.create_job(job.session_id, str(course_id))
        await audit_svc.run_audit(audit_job.id, session, course_id)

        completed_audit = self._file_audit_repo.get_job(audit_job.id)
        if (
            not completed_audit
            or completed_audit.status != ScanStatus.COMPLETED
            or not completed_audit.report_id
        ):
            raise RuntimeError(
                f"AutoRemedy file audit did not produce a report for course {course_id}"
            )

        job.files_total = completed_audit.files_total
        job.files_audited = completed_audit.files_audited
        job.file_report_id = completed_audit.report_id
        self._log(job, f"File audit complete: {completed_audit.files_audited} files checked")
        self._save(job)

    async def _run_convert_and_replace_phase(
        self,
        job,
        session,
        course_id,
        skip_page_identifiers: set[str] | None = None,
        skip_file_ids: set[int] | None = None,
    ):
        """Phase 4: Convert PDFs + Office docs to HTML pages, replace links, archive originals.

        For each document (failed PDFs + convertible Office files):
        1. Download from Canvas
        2. Convert to HTML via LiteParse + LLM
        3. Create a new Canvas wiki page with the HTML
        4. Find all links to the original file across course content
        5. Replace those links with the new page URL
        6. Archive the original file to _clu_archived folder

        CLU-85: ``skip_file_ids`` accepted for future filtering (Task 9
        will apply it to the conversion loop). ``skip_page_identifiers``
        accepted for future filtering (Task 9 will apply it to the link-
        replacement sub-step that writes back to unrelated pages).
        """
        if not self._conversion_repo:
            return
        if not job.file_report_id:
            return

        report = self._file_audit_repo.get_report(job.file_report_id)
        if not report:
            raise RuntimeError(
                f"AutoRemedy file audit report {job.file_report_id} could not be loaded"
            )

        from lti_app.canvas.client import CanvasClient
        from lti_app.canvas.content_fetcher import ContentFetcher
        from lti_app.canvas.file_manager import FileManager
        from lti_app.canvas.content_writer import ContentWriter

        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        file_mgr = FileManager(client)
        writer = ContentWriter(client)
        conv_svc = ConversionService(self._conversion_repo)

        # Collect documents to convert: failed PDFs + convertible Office files
        docs_to_convert = []
        for entry in report.entries:
            if entry.is_pdf and entry.status == "failed":
                docs_to_convert.append(entry)
            elif entry.content_type in CONVERTIBLE_TYPES:
                docs_to_convert.append(entry)
            elif not entry.is_pdf and entry.filename.rsplit(".", 1)[-1].lower() in ("docx", "pptx", "xlsx"):
                docs_to_convert.append(entry)

        # CLU-85 Task 9: filter out files the user (or permanent
        # exclusions) asked to skip. Skipped files are not downloaded,
        # not parsed, not converted, and not counted as docs_converted.
        # The empty-set guard preserves Fix My Course behavior.
        if skip_file_ids:
            before_count = len(docs_to_convert)
            docs_to_convert = [
                entry for entry in docs_to_convert
                if entry.file_id not in skip_file_ids
            ]
            skipped_by_user = before_count - len(docs_to_convert)
            if skipped_by_user:
                self._log(
                    job,
                    f"Skipping {skipped_by_user} file conversions (user selection)",
                )
                job.docs_skipped += skipped_by_user
                self._save(job)

        if not docs_to_convert:
            self._save(job)
            return

        # Fetch all course pages once for link scanning
        fetcher = ContentFetcher(client)
        all_pages = await fetcher.fetch_all(course_id)

        # CLU-85 Task 9: filter out pages the user asked to leave alone.
        # Even though phase 2 already skipped them for HTML remediation,
        # phase 4's link-replacement sub-step would otherwise STILL write
        # to those pages when a file reference needs updating. That
        # defeats the "leave this page alone" promise. Closes Codex
        # finding #2 from the original spec review (the psych-001 OZY
        # newsletter escape-hatch use case). The empty-set guard
        # preserves Fix My Course behavior.
        if skip_page_identifiers:
            before_count = len(all_pages)
            all_pages = [
                p for p in all_pages
                if (getattr(p, "identifier", None) or "") not in skip_page_identifiers
            ]
            skipped_link_sweep = before_count - len(all_pages)
            if skipped_link_sweep:
                self._log(
                    job,
                    f"Skipping link replacement on {skipped_link_sweep} excluded pages",
                )

        # Conversion phase occupies the 75%-90% slice of overall progress.
        # Without per-doc progress writes the bar sits at 75% for the whole
        # phase even though docs are being processed (CLU-56).
        total_docs = len(docs_to_convert)
        for doc_idx, entry in enumerate(docs_to_convert, 1):
            self._check_cancel(job)

            # CLU-69: filename-keyword skip (cheap, runs before download).
            # Reference docs (textbooks, catalogs, schedules) are not
            # pedagogically suited to wiki-page conversion. The original
            # file stays in place and link replacement is bypassed.
            filename_skip = should_skip_by_filename(entry.filename)
            if filename_skip:
                job.docs_skipped += 1
                self._log(
                    job,
                    f"Skipped {entry.filename} ({filename_skip})",
                )
                _logger.info(
                    "autoremedy_doc_skipped",
                    file_id=entry.file_id,
                    filename=entry.filename,
                    reason=filename_skip,
                )
                self._record_audit_outcome(
                    job.file_report_id, entry.file_id,
                    skip_reason=filename_skip,
                    remediation_status="skipped",
                )
                job.progress = 0.75 + (doc_idx / max(total_docs, 1)) * 0.15
                self._save(job)
                continue

            try:
                # Step 1-2: Convert document to HTML via ConversionService
                conv_job = conv_svc.create_job(
                    job.session_id, str(course_id),
                    entry.file_id, entry.filename, entry.content_type,
                )
                # CLU-68: pass a cancel check that re-reads the JSONB
                # and raises AutoRemedyCancelled. The conversion service
                # threads this into the LLM chunk loop so user cancels
                # propagate within one chunk instead of 2-8 minutes.
                await conv_svc.run_conversion(
                    conv_job.id,
                    session,
                    course_id,
                    cancel_check=lambda: self._check_cancel(job),
                )

                completed = self._conversion_repo.get_job(conv_job.id)
                if not completed or completed.status.value != "completed":
                    _logger.warning("autoremedy_conversion_failed", file_id=entry.file_id, filename=entry.filename)
                    failure_reason = (
                        completed.error if completed and completed.error
                        else "conversion job did not complete"
                    )
                    self._record_audit_outcome(
                        job.file_report_id, entry.file_id,
                        skip_reason=f"conversion failed: {failure_reason}",
                        remediation_status="skipped",
                    )
                    continue

                # CLU-69: page-count skip (definitive, set inside
                # ConversionService.run_conversion after LiteParse). Skipped
                # docs are still status=COMPLETED but have a skip_reason.
                # Don't archive, don't link-replace, don't count as
                # converted.
                if completed.skip_reason:
                    job.docs_skipped += 1
                    self._log(
                        job,
                        f"Skipped {entry.filename} ({completed.skip_reason})",
                    )
                    _logger.info(
                        "autoremedy_doc_skipped",
                        file_id=entry.file_id,
                        filename=entry.filename,
                        reason=completed.skip_reason,
                    )
                    self._record_audit_outcome(
                        job.file_report_id, entry.file_id,
                        skip_reason=completed.skip_reason,
                        remediation_status="skipped",
                    )
                    job.progress = 0.75 + (doc_idx / max(total_docs, 1)) * 0.15
                    self._save(job)
                    continue

                job.docs_converted += 1
                self._record_audit_outcome(
                    job.file_report_id, entry.file_id,
                    remediation_status="converted",
                )
                # CLU-73: `canvas_page_url` is set from Canvas's `html_url`
                # field which is an ABSOLUTE URL like
                # `https://projectclu.com/courses/4/pages/syllabus`. Pass
                # only the path portion to `replace_file_links` so it
                # doesn't double-host the original href's scheme. Belt
                # and suspenders — the regex in `replace_file_links` was
                # also updated to consume optional host prefixes, but
                # passing path-only is the simpler invariant.
                from urllib.parse import urlparse
                raw_page_url = completed.canvas_page_url or ""
                parsed = urlparse(raw_page_url)
                new_page_url = parsed.path or raw_page_url

                if not new_page_url:
                    _logger.warning("autoremedy_no_page_url", file_id=entry.file_id)
                    self._save(job)
                    continue

                # Step 3-4: Find and replace links to original file in all course pages
                total_links_replaced = 0
                for page in all_pages:
                    updated_html, count = FileManager.replace_file_links(
                        page.html_content, entry.file_id, new_page_url,
                    )
                    if count > 0:
                        try:
                            await writer.write_page_content(course_id, page, updated_html)
                            page.html_content = updated_html
                            total_links_replaced += count
                            _logger.info(
                                "autoremedy_links_replaced",
                                file_id=entry.file_id,
                                page_title=page.title,
                                count=count,
                            )
                        except Exception as write_err:
                            _logger.warning(
                                "autoremedy_link_replace_write_failed",
                                file_id=entry.file_id,
                                page_id=page.id,
                                error=str(write_err),
                            )

                job.links_replaced += total_links_replaced

                # Step 5: Archive original file
                try:
                    await file_mgr.move_file_to_archive(course_id, entry.file_id)
                    job.originals_archived += 1
                except Exception as archive_err:
                    _logger.warning(
                        "autoremedy_archive_failed",
                        file_id=entry.file_id,
                        error=str(archive_err),
                    )

            except Exception as e:
                _logger.warning(
                    "autoremedy_convert_replace_error",
                    file_id=entry.file_id,
                    filename=entry.filename,
                    error=str(e),
                )
                # Best-effort: record the failure on the audit entry so
                # the ACR can explain why this file's LNK007/PDF001
                # warning remains. Only record if the earlier branches
                # didn't already set a skip_reason (convert-success path
                # is upstream of this except; skip branches continue
                # before falling into the outer body).
                self._record_audit_outcome(
                    job.file_report_id, entry.file_id,
                    skip_reason=f"error during conversion: {e!s}",
                    remediation_status="skipped",
                )

            job.progress = 0.75 + (doc_idx / max(total_docs, 1)) * 0.15
            self._save(job)
