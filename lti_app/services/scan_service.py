"""Background scan orchestrator with job tracking."""

from datetime import UTC, datetime

import structlog
from ulid import ULID


from lti_app.canvas.client import CanvasClient
from lti_app.canvas.content_fetcher import ContentFetcher
from lti_app.core.accessibility.analyzer import AccessibilityAnalyzer
from lti_app.core.accessibility.rendered_scanner import BrowserPool, CONTENT_URLS
from lti_app.db.repositories import ScanRepository
from lti_app.models import AccessibilityIssue, ScanJob, ScanStatus

_logger = structlog.get_logger(__name__)

# Module-level singleton analyzer (stateless, safe to share)
_analyzer = AccessibilityAnalyzer()


def deduplicate_issues(
    content_issues: list[AccessibilityIssue],
    rendered_issues: list[AccessibilityIssue],
) -> list[AccessibilityIssue]:
    """Merge issues from both scanners, preferring content scanner (has fix capability)."""
    seen: set[tuple] = set()
    merged = list(content_issues)  # Content issues take priority
    for issue in content_issues:
        key = (issue.page_id, issue.rule_id, (issue.element_html or "")[:50])
        seen.add(key)
    for issue in rendered_issues:
        key = (issue.page_id, issue.rule_id, (issue.element_html or "")[:50])
        if key not in seen:
            seen.add(key)
            merged.append(issue)
    return merged


class ScanService:
    """Orchestrates course accessibility scans."""

    def __init__(self, scan_repo: ScanRepository):
        self._repo = scan_repo

    def create_scan_job(
        self,
        session_id: str,
        course_id: str,
        scan_mode: str = "content_only",
    ) -> ScanJob:
        """Create a PENDING scan job and persist it."""
        normalized_scan_mode = scan_mode or "content_only"
        job = ScanJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            status=ScanStatus.PENDING,
            scan_mode=normalized_scan_mode,
            created_at=datetime.now(UTC),
        )
        self._repo.save_job(job)
        return job

    async def run_scan(self, job_id: str, session, course_id: int) -> None:
        """Execute the scan. Call this as a background task.

        Scan modes:
          - content_only (default): HTML content analysis only
          - rendered_only: Browser-rendered axe-core scan only
          - full: Both phases, deduplicated
        """
        job = self._repo.get_job(job_id)
        if not job:
            _logger.error("scan_job_not_found", job_id=job_id)
            return
        scan_mode = job.scan_mode or "content_only"

        try:
            # Mark as running
            job.status = ScanStatus.RUNNING
            job.scan_mode = scan_mode
            self._repo.save_job(job)

            # Build Canvas client
            _logger.info(
                "scan_debug_session",
                base_url=session.canvas_base_url,
                has_token=bool(session.canvas_access_token),
                token_len=len(session.canvas_access_token),
            )
            client = CanvasClient(
                base_url=session.canvas_base_url,
                access_token=session.canvas_access_token,
                refresh_token=getattr(session, "canvas_refresh_token", ""),
            )

            try:
                # Fetch pages
                _logger.info("scan_fetching_content", job_id=job_id, course_id=course_id)
                fetcher = ContentFetcher(client)
                try:
                    pages = await fetcher.fetch_all(course_id)
                except Exception as fetch_err:
                    _logger.error("scan_fetch_error", job_id=job_id, error=str(fetch_err), error_type=type(fetch_err).__name__)
                    raise

                job.pages_total = len(pages)
                self._repo.save_job(job)

                _logger.info("scan_pages_fetched", job_id=job_id, pages=len(pages))

                # ── Phase 1: Content scan ────────────────────────────
                content_issues = []
                report = None

                if scan_mode in ("content_only", "full"):
                    job.phase = "content"
                    self._repo.save_job(job)

                    # Track progress as pages are analyzed
                    for _page in pages:
                        job.pages_scanned += 1
                        job.progress = job.pages_scanned / max(job.pages_total, 1)
                        job.current_page = getattr(_page, "title", "") or _page.identifier
                        self._repo.save_job(job)

                    # Build report (analyze_course builds the final issue list internally)
                    report = await _analyzer.analyze_course(pages, str(course_id))
                    content_issues = list(report.issues)

                    # Stamp canvas_url, content_type, and page_title on all issues using page maps
                    page_canvas_url: dict[str, str | None] = {}
                    page_content_type: dict[str, str] = {}
                    page_title_map: dict[str, str] = {}
                    for page in pages:
                        if page.canvas_url:
                            page_canvas_url[page.identifier] = page.canvas_url
                            page_canvas_url[page.id] = page.canvas_url
                        page_content_type[page.identifier] = page.content_type.value
                        page_content_type[page.id] = page.content_type.value
                        if page.title:
                            page_title_map[page.identifier] = page.title
                            page_title_map[page.id] = page.title

                    for issue in content_issues:
                        lookup = issue.page_identifier or issue.page_id
                        if lookup:
                            if lookup in page_canvas_url:
                                issue.canvas_url = page_canvas_url[lookup]
                            if lookup in page_content_type:
                                issue.content_type = page_content_type[lookup]
                            if lookup in page_title_map:
                                issue.page_title = page_title_map[lookup]

                    _logger.info(
                        "content_scan_complete",
                        job_id=job_id,
                        issues=len(content_issues),
                    )

                # ── Phase 2: Rendered scan ───────────────────────────
                rendered_issues: list[AccessibilityIssue] = []

                if scan_mode in ("full", "rendered_only"):
                    job.phase = "rendered"
                    job.pages_scanned = 0
                    job.progress = 0.0
                    job.queue_position = BrowserPool.get_queue_position()
                    self._repo.save_job(job)

                    for i, page in enumerate(pages):
                        # Prefer the exact Canvas route captured during fetch.
                        canvas_path = page.canvas_url
                        if not canvas_path:
                            ct_value = (
                                page.content_type.name
                                if hasattr(page.content_type, "name")
                                else str(page.content_type)
                            )
                            url_template = CONTENT_URLS.get(ct_value, "")
                            if not url_template:
                                continue
                            canvas_path = url_template.format(
                                course_id=course_id,
                                identifier=page.canvas_id or page.identifier,
                            )

                        page_issues = await BrowserPool.scan_page(
                            canvas_base_url=session.canvas_base_url,
                            access_token=session.canvas_access_token,
                            canvas_path=canvas_path,
                            page_id=page.id,
                        )
                        for issue in page_issues:
                            issue.page_identifier = page.identifier
                            issue.content_type = page.content_type.value
                            if page.title:
                                issue.page_title = page.title
                            if page.canvas_url:
                                issue.canvas_url = page.canvas_url
                        rendered_issues.extend(page_issues)

                        # Update progress for rendered phase
                        job.pages_scanned = i + 1
                        job.progress = (i + 1) / max(len(pages), 1)
                        job.current_page = getattr(page, "title", "") or page.identifier
                        job.queue_position = BrowserPool.get_queue_position()
                        self._repo.save_job(job)

                    _logger.info(
                        "rendered_scan_complete",
                        job_id=job_id,
                        issues=len(rendered_issues),
                    )

                # ── Phase 3: Merge and save ──────────────────────────
                job.phase = "merging"
                self._repo.save_job(job)

                if scan_mode == "content_only":
                    final_issues = content_issues
                elif scan_mode == "rendered_only":
                    final_issues = rendered_issues
                else:
                    # full mode — deduplicate across both scanners
                    final_issues = deduplicate_issues(content_issues, rendered_issues)

                # If content scan ran, we have a report; otherwise build a minimal one
                if report is not None:
                    report.issues = final_issues
                    report.total_issues = len(final_issues)
                    report.errors = sum(
                        1 for i in final_issues if i.severity.value == "error"
                    )
                    report.warnings = sum(
                        1 for i in final_issues if i.severity.value == "warning"
                    )
                else:
                    # rendered_only mode — build report from rendered issues
                    from lti_app.models import AccessibilityReport

                    report = AccessibilityReport(
                        course_id=str(course_id),
                        analyzed_at=datetime.now(UTC),
                        total_issues=len(final_issues),
                        errors=sum(
                            1 for i in final_issues if i.severity.value == "error"
                        ),
                        warnings=sum(
                            1 for i in final_issues if i.severity.value == "warning"
                        ),
                        info=sum(
                            1 for i in final_issues if i.severity.value == "info"
                        ),
                        pages_analyzed=len(pages),
                        issues=final_issues,
                    )

                report_id = str(ULID())
                self._repo.save_report(report_id, report)

                # Mark completed
                job.status = ScanStatus.COMPLETED
                job.phase = ""
                job.report_id = report_id
                job.completed_at = datetime.now(UTC)
                self._repo.save_job(job)

                _logger.info(
                    "scan_completed",
                    job_id=job_id,
                    scan_mode=scan_mode,
                    content_issues=len(content_issues),
                    rendered_issues=len(rendered_issues),
                    total_issues=report.total_issues,
                    pages=report.pages_analyzed,
                )
            finally:
                await client.close()

        except Exception as e:
            _logger.error("scan_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)

    @staticmethod
    def calculate_score(errors: int, warnings: int, pages: int = 0) -> float:
        """Calculate 0-100 accessibility score.

        Thin wrapper around the unified scoring service (CLU-52). Kept on
        ScanService for backwards compatibility with existing call sites.
        ``pages`` defaults to 0 only so old test fixtures don't break at
        import time — every real call should pass the page count.
        """
        from lti_app.services.scoring_service import compute_course_score

        return compute_course_score(errors, warnings, pages)
