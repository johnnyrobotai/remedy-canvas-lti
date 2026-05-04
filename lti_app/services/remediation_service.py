"""Two-phase remediation orchestrator: produce previews, then apply."""

from datetime import UTC, datetime

import structlog
from ulid import ULID


from lti_app.canvas.client import CanvasClient
from lti_app.canvas.content_fetcher import ContentFetcher
from lti_app.canvas.content_writer import ContentWriter
from lti_app.core.ai.alt_text import AltTextGenerator
from lti_app.core.ai.image_fetcher import ImageFetcher
from lti_app.core.ai.vision_client import get_vision_client
from lti_app.core.remediation.canvas_validator import CanvasHTMLValidator
from lti_app.core.remediation.transformer import HTMLTransformer
from lti_app.db.repositories import RemediationRepository, ScanRepository
from lti_app.models import (
    AccessibilityIssue,
    AltTextGenerationResult,
    CourseImage,
    CoursePage,
    IssueCategory,
    RemediationJob,
    RemediationPreview,
    RemediationRequest,
    RemediationResult,
    ScanStatus,
)

_logger = structlog.get_logger(__name__)

# Map fix_* request fields to IssueCategory
_FIX_CATEGORY_MAP: dict[str, IssueCategory] = {
    "fix_headings": IssueCategory.HEADINGS,
    "fix_tables": IssueCategory.TABLES,
    "fix_links": IssueCategory.LINKS,
    "fix_contrast": IssueCategory.CONTRAST,
    "fix_structure": IssueCategory.STRUCTURE,
    "fix_media": IssueCategory.MEDIA,
    "fix_math": IssueCategory.MATH,
    "fix_images": IssueCategory.IMAGES,
}


def _enabled_categories(request: RemediationRequest) -> set[IssueCategory]:
    """Return the set of categories the instructor enabled."""
    categories = set()
    for field_name, category in _FIX_CATEGORY_MAP.items():
        if getattr(request, field_name, False):
            categories.add(category)
    return categories


class RemediationService:
    """Orchestrates remediation: preview generation and application."""

    def __init__(
        self,
        scan_repo: ScanRepository,
        remediation_repo: RemediationRepository,
    ):
        self._scan_repo = scan_repo
        self._remediation_repo = remediation_repo

    def create_remediation_job(
        self,
        session_id: str,
        course_id: str,
        request: RemediationRequest,
    ) -> RemediationJob:
        """Create a PENDING remediation job."""
        job = RemediationJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            status=ScanStatus.PENDING,
            created_at=datetime.now(UTC),
            request=request,
        )
        self._remediation_repo.save_job(job)
        return job

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_images_needing_alt(
        pages: list[CoursePage],
        report_issues: list[AccessibilityIssue],
    ) -> list[CourseImage]:
        """Extract CourseImage objects for images that need alt text."""
        from bs4 import BeautifulSoup
        from lti_app.core.accessibility.image_alt import assess_image_tag

        images: list[CourseImage] = []
        # Use page_identifier (stable) when available, fall back to page_id
        image_page_keys = set()
        for i in report_issues:
            if i.category == IssueCategory.IMAGES and i.can_auto_fix:
                image_page_keys.add(i.page_identifier or i.page_id)

        for page in pages:
            if page.identifier not in image_page_keys and page.id not in image_page_keys:
                continue
            soup = BeautifulSoup(page.html_content, "html.parser")
            for idx, img in enumerate(soup.find_all("img")):
                src = img.get("src", "")
                if not src:
                    continue
                assessment = assess_image_tag(img)
                images.append(CourseImage(
                    id=f"{page.id}-img-{idx}",
                    src=src,
                    alt_text=img.get("alt"),
                    page_id=page.id,
                    needs_alt_text=assessment.needs_generation,
                    alt_text_skip_reason=assessment.skip_reason,
                ))

        return images

    # ------------------------------------------------------------------
    # Phase A: Remediate (produce previews)
    # ------------------------------------------------------------------

    async def run_remediation(
        self, job_id: str, session, course_id: int,
        on_page_activity=None,
        on_page_complete=None,
        auto_apply: bool = False,
    ) -> list[str]:
        """Execute remediation as a background task. Produces previews.

        Args:
            on_page_activity: Optional callback for free-form progress messages.
            on_page_complete: Optional callback ``(idx, total, pages_written)`` invoked
                once per page after the inner transform/write loop finishes that page.
                Used by AutoRemedyService to advance the structured progress fields
                (CLU-56) so the dashboard can show real-time per-page progress.
            auto_apply: If True, apply previews to Canvas immediately after generating.

        Returns:
            List of page_ids that were remediated (for auto-apply tracking).
        """
        job = self._remediation_repo.get_job(job_id)
        if not job:
            _logger.error("remediation_job_not_found", job_id=job_id)
            return

        try:
            job.status = ScanStatus.RUNNING
            self._remediation_repo.save_job(job)

            client = CanvasClient(
                base_url=session.canvas_base_url,
                access_token=session.canvas_access_token,
                refresh_token=getattr(session, "canvas_refresh_token", ""),
            )

            try:
                # Get latest scan report for issues
                if job.scan_report_id:
                    report = self._scan_repo.get_report(job.scan_report_id)
                    if not report:
                        raise RuntimeError(
                            f"Scan report {job.scan_report_id} not found for remediation job {job.id}"
                        )
                else:
                    report = self._scan_repo.get_latest_report(str(course_id))
                if not report or not report.issues:
                    job.status = ScanStatus.COMPLETED
                    job.completed_at = datetime.now(UTC)
                    self._remediation_repo.save_job(job)
                    return

                # Filter to enabled categories, group by stable page_identifier
                enabled = _enabled_categories(job.request)
                auto_fixable: dict[str, list[AccessibilityIssue]] = {}
                for issue in report.issues:
                    if issue.can_auto_fix and issue.category in enabled:
                        # Use page_identifier (stable across fetches) if available,
                        # fall back to page_id (ULID) for backward compat
                        key = issue.page_identifier or issue.page_id
                        auto_fixable.setdefault(key, []).append(issue)

                # Batch fix: if target_rule_id is set, only include pages with that rule
                if job.request.apply_to_all_instances and job.request.target_rule_id:
                    target_rule = job.request.target_rule_id
                    filtered: dict[str, list[AccessibilityIssue]] = {}
                    for page_id, page_issues in auto_fixable.items():
                        matching = [i for i in page_issues if i.rule_id == target_rule]
                        if matching:
                            filtered[page_id] = matching
                    auto_fixable = filtered

                if not auto_fixable:
                    job.status = ScanStatus.COMPLETED
                    job.completed_at = datetime.now(UTC)
                    self._remediation_repo.save_job(job)
                    return

                # Re-fetch pages from Canvas (avoid stale content)
                fetcher = ContentFetcher(client)
                all_pages = await fetcher.fetch_all(course_id)

                # Build page lookup by identifier (stable across fetches)
                # Also index by id for backward compat with old reports
                page_map: dict[str, CoursePage] = {}
                for p in all_pages:
                    page_map[p.identifier] = p
                    page_map[p.id] = p

                # Filter to selected pages if specified
                selected_ids = job.request.selected_page_ids

                # When batch fixing, ignore selected_page_ids
                if job.request.apply_to_all_instances and job.request.target_rule_id:
                    selected_ids = None

                pages_to_fix: list[tuple[CoursePage, list[AccessibilityIssue]]] = []
                for page_id, issues in auto_fixable.items():
                    page = page_map.get(page_id)
                    if page is None:
                        continue
                    if selected_ids and page_id not in selected_ids:
                        continue
                    pages_to_fix.append((page, issues))

                job.pages_total = len(pages_to_fix)
                self._remediation_repo.save_job(job)

                # --- Alt text generation (before per-page loop) ---
                alt_texts: dict[str, str] = {}
                all_alt_results: dict[str, list[AltTextGenerationResult]] = {}

                if job.request.generate_alt_text:
                    try:
                        images = self._extract_images_needing_alt(
                            list(page_map.values()), report.issues
                        )
                        if images:
                            image_fetcher = ImageFetcher(client)
                            alt_gen = AltTextGenerator(
                                image_fetcher,
                                run_id=job.id,
                            )
                            alt_response = await alt_gen.generate_for_course(
                                images,
                                list(page_map.values()),
                                course_id=course_id,
                            )
                            alt_texts = alt_response.alt_texts

                            # Group results by page_id
                            for result in alt_response.results:
                                all_alt_results.setdefault(
                                    result.page_id, []
                                ).append(result)

                            _logger.info(
                                "alt_text_generation_complete",
                                generated=alt_response.generated_count,
                                skipped=alt_response.skipped_count,
                                errors=alt_response.error_count,
                            )
                    except Exception as e:
                        _logger.warning(
                            "alt_text_generation_failed",
                            error=str(e),
                        )

                # --- Layer 3 setup (optional) ---
                from lti_app.core.ai.ai_remediator import AIRemediator

                ai_remediator: AIRemediator | None = None
                if job.request.use_ai_remediation:
                    try:
                        vision_client = get_vision_client()
                        ai_remediator = AIRemediator(
                            vision_client=vision_client,
                            run_id=job.id,
                        )
                    except Exception as e:
                        _logger.warning(
                            "layer3_init_failed",
                            error=str(e),
                        )

                # --- Per-page transform loop ---
                from lti_app.core.remediation.html_strategy_remediator import HTMLStrategyRemediator
                transformer = HTMLTransformer(colors=None)
                validator = CanvasHTMLValidator()
                pre_remediator = HTMLStrategyRemediator()

                def _notify(msg: str) -> None:
                    if on_page_activity:
                        on_page_activity(msg)

                auto_apply_count = 0
                changed_page_ids: list[str] = []
                written_page_ids: list[str] = []

                for idx, (page, issues) in enumerate(pages_to_fix, 1):
                    try:
                        issue_rules = list({i.rule_id for i in issues})
                        _notify(f"[{idx}/{len(pages_to_fix)}] {page.title} — {len(issues)} issues ({', '.join(issue_rules[:3])})")

                        # Layer 1: Deterministic pre-fixes (HTMLStrategyRemediator)
                        html, pre_fixes = pre_remediator.remediate(page.html_content)
                        if pre_fixes:
                            _notify(f"  ↳ Layer 1: {len(pre_fixes)} deterministic fixes")

                        # Layer 2: Issue-driven fixes (HTMLTransformer)
                        fixed_html = transformer.transform(
                            html,
                            issues,
                            alt_texts=alt_texts,
                            page_title=page.title,
                        )

                        # Layer 3: AI tool-calling remediation with content-loss guard
                        if ai_remediator:
                            try:
                                _notify("  ↳ Layer 3: Sending to AI (kimi-k2.6)...")
                                ai_input_page = page.model_copy(
                                    update={"html_content": fixed_html}
                                )
                                ai_html, ai_changes = await ai_remediator.remediate_page(
                                    ai_input_page,
                                    issues,
                                )
                                if ai_changes and ai_html:
                                    # Guard: reject AI output if it lost >20% of content length
                                    # (LLMs often truncate large HTML documents)
                                    if len(ai_html) >= len(fixed_html) * 0.8:
                                        fixed_html = ai_html
                                        for change in ai_changes[:5]:
                                            _notify(f"  ✓ {change}")
                                    else:
                                        _logger.warning(
                                            "layer3_content_loss",
                                            page_id=page.id,
                                            original_len=len(fixed_html),
                                            ai_len=len(ai_html),
                                        )
                                        _notify(f"  ⚠ AI output too short ({len(ai_html)} vs {len(fixed_html)} chars), keeping Layer 2 fixes")
                            except Exception as e:
                                _logger.warning("layer3_ai_failed", page_id=page.id, error=str(e))
                                _notify(f"  ⚠ AI failed: {str(e)[:80]}")

                        # Layer 4: Canvas sanitizer
                        sanitized_html, val_result = validator.sanitize(fixed_html)

                        html_changed = sanitized_html != page.html_content
                        if html_changed:
                            changed_page_ids.append(page.id)

                        fixed_rule_ids = [
                            i.rule_id for i in issues if i.can_auto_fix
                        ]
                        # Merge Layer 1 pre-fix descriptions with Layer 2 rule IDs
                        all_issues_fixed = fixed_rule_ids + pre_fixes if html_changed else []

                        preview = RemediationPreview(
                            job_id=job.id,
                            page_id=page.id,
                            page_identifier=page.identifier,
                            page_title=page.title,
                            content_type=page.content_type,
                            original_html=page.html_content,
                            remediated_html=sanitized_html,
                            issues_fixed=all_issues_fixed,
                            canvas_tags_stripped=val_result.tags_stripped,
                            canvas_attributes_stripped=val_result.attributes_stripped,
                            alt_text_results=all_alt_results.get(page.id, []),
                        )
                        self._remediation_repo.save_preview(preview)

                        # Auto-apply: write directly to Canvas in the loop
                        if auto_apply:
                            _logger.info("auto_apply_check", page_id=page.id, changed=html_changed, orig_len=len(page.html_content), fixed_len=len(sanitized_html))
                        if auto_apply and html_changed:
                            try:
                                writer = ContentWriter(client)
                                await writer.write_page_content(course_id, page, sanitized_html)
                                _notify("  → Written to Canvas")
                                auto_apply_count += 1
                                written_page_ids.append(page.id)
                            except Exception as write_err:
                                _logger.warning("auto_apply_write_failed", page_id=page.id, error=str(write_err))
                                _notify(f"  ⚠ Write failed: {str(write_err)[:60]}")

                    except Exception as e:
                        _logger.warning(
                            "remediation_page_failed",
                            page_id=page.id,
                            error=str(e),
                        )

                    job.pages_remediated += 1
                    job.progress = job.pages_remediated / max(job.pages_total, 1)
                    self._remediation_repo.save_job(job)

                    # Notify the orchestrator (AutoRemedyService) so it can
                    # advance its own structured progress fields per page (CLU-56).
                    if on_page_complete:
                        try:
                            on_page_complete(idx, len(pages_to_fix), auto_apply_count)
                        except Exception as cb_err:
                            _logger.warning(
                                "on_page_complete_callback_failed",
                                page_id=page.id,
                                error=str(cb_err),
                            )

                if auto_apply:
                    _notify(f"Applied fixes to {auto_apply_count} pages in Canvas")

                job.status = ScanStatus.COMPLETED
                job.completed_at = datetime.now(UTC)
                self._remediation_repo.save_job(job)

                _logger.info(
                    "remediation_completed",
                    job_id=job.id,
                    pages_remediated=job.pages_remediated,
                )

                return written_page_ids if auto_apply else changed_page_ids

            finally:
                await client.close()

        except Exception as e:
            _logger.error("remediation_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._remediation_repo.save_job(job)

        return []

    # ------------------------------------------------------------------
    # Phase B: Apply approved previews
    # ------------------------------------------------------------------

    async def apply_previews(
        self,
        job_id: str,
        session,
        course_id: int,
        page_ids: list[str],
        alt_text_overrides: dict[str, str] | None = None,
    ) -> RemediationResult:
        """Write approved remediated content to Canvas."""
        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )

        try:
            writer = ContentWriter(client)

            # Refetch pages to get current canvas_url, canvas_id, parent_id
            fetcher = ContentFetcher(client)
            all_pages = await fetcher.fetch_all(course_id)
            # Index by both identifier (stable) and id for flexible matching
            page_map: dict[str, CoursePage] = {}
            for p in all_pages:
                page_map[p.identifier] = p
                page_map[p.id] = p

            pages_updated = 0
            issues_fixed = 0

            for page_id in page_ids:
                preview = self._remediation_repo.get_preview(job_id, page_id)
                if not preview:
                    _logger.warning(
                        "apply_preview_not_found",
                        job_id=job_id,
                        page_id=page_id,
                    )
                    continue

                # Match by page_identifier (stable) first, fall back to page_id
                lookup_key = preview.page_identifier or page_id
                page = page_map.get(lookup_key)
                if not page:
                    # Try the other key as fallback
                    page = page_map.get(page_id)
                if not page:
                    _logger.warning(
                        "apply_page_not_found",
                        page_id=page_id,
                        page_identifier=preview.page_identifier,
                    )
                    continue

                try:
                    html_to_write = preview.remediated_html

                    # Apply alt text overrides from instructor
                    if alt_text_overrides:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html_to_write, "html.parser")
                        modified = False
                        for img in soup.find_all("img"):
                            src = img.get("src", "")
                            if src in alt_text_overrides:
                                img["alt"] = alt_text_overrides[src]
                                modified = True
                        if modified:
                            html_to_write = str(soup)

                    await writer.write_page_content(
                        course_id, page, html_to_write
                    )
                    pages_updated += 1
                    issues_fixed += len(preview.issues_fixed)
                except Exception as e:
                    _logger.error(
                        "apply_write_failed",
                        page_id=page_id,
                        error=str(e),
                    )

            return RemediationResult(
                course_id=str(course_id),
                remediated_at=datetime.now(UTC),
                pages_updated=pages_updated,
                issues_fixed=issues_fixed,
            )

        finally:
            await client.close()
