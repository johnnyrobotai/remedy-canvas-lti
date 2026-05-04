"""Document conversion orchestrator: download → extract → convert → create Canvas page."""

from datetime import UTC, datetime
from html import escape as _html_escape

import structlog
from ulid import ULID


from lti_app.canvas.client import CanvasClient
from lti_app.canvas.content_fetcher import ContentFetcher
from lti_app.canvas.content_writer import ContentWriter
from lti_app.canvas.file_manager import FileManager
from lti_app.core.accessibility.analyzer import AccessibilityAnalyzer
from lti_app.core.documents.liteparse_adapter import LiteParseAdapter
from lti_app.core.documents.llm_converter import DocumentToHTMLService
from lti_app.core.remediation.canvas_validator import CanvasHTMLValidator
from lti_app.core.remediation.transformer import HTMLTransformer
from lti_app.db.repositories import ConversionRepository
from lti_app.models import (
    ConversionJob,
    CoursePage,
    ScanStatus,
)

_logger = structlog.get_logger(__name__)

# Map content-type → source_format
_FORMAT_MAP = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/msword": "docx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.ms-excel": "xlsx",
}

CONVERTIBLE_TYPES = set(_FORMAT_MAP.keys())

# Extension fallback — must stay in sync with the values of _FORMAT_MAP.
# Used when content_type lookup fails (e.g., Canvas returns empty mime type).
_SUPPORTED_EXTS = ("pdf", "docx", "pptx", "xlsx", "doc", "ppt", "xls")

# When a PDF exceeds this many source pages, ConversionService tries to
# split it into chapters and produce multi-page Canvas output (CLU-67).
# Smaller documents continue to use single-page output (faster, simpler).
_CHAPTERS_THRESHOLD_PAGES = 20

# CLU-69: PDFs with more than this many pages are SKIPPED entirely.
# Real example: a 315-page Art Appreciation textbook took 49 minutes to
# chapter-convert, producing 16 Canvas pages students would never read.
# Reference documents (textbooks, catalogs, course schedules) are meant
# to be downloaded as-is, not transposed into wiki pages. Skipped docs
# stay in Canvas Files unmodified.
_DOC_SKIP_PAGES_THRESHOLD = 50

# Filename keywords that signal "reference document, do not convert".
# Matched as case-insensitive substrings against the filename. We
# deliberately do NOT include "syllabus" — those are short, pedagogical,
# and benefit from being a Canvas wiki page.
_DOC_SKIP_KEYWORDS = frozenset({
    "textbook",
    "catalog",
    "schedule",
    "calendar",
    "manual",
    "handbook",
    "reference",
    "readings",
})


def should_skip_by_filename(filename: str) -> str | None:
    """Return a human-readable reason if the filename matches a skip
    keyword, otherwise None.

    The check is case-insensitive substring match — "Course Catalog
    2024.pdf" and "course_catalog.pdf" both match "catalog". Used by
    ``AutoRemedyService._run_convert_and_replace_phase`` to bypass the
    download + parse + LLM cycle for documents that obviously shouldn't
    be converted.
    """
    if not filename:
        return None
    name = filename.lower()
    for kw in _DOC_SKIP_KEYWORDS:
        if kw in name:
            return f"filename keyword '{kw}'"
    return None


def _build_toc_html(title: str, chapter_titles_and_urls: list[tuple[str, str]]) -> str:
    """Build a Canvas wiki page body that links to each chapter.

    The TOC page is what AutoRemedy's link replacement points to (via
    ConversionJob.canvas_page_url), so it must be self-explanatory.
    """
    safe_title = _html_escape(title)
    parts = [
        f"<h2>{safe_title}</h2>",
        "<p>This document was automatically split into chapters for"
        " accessibility. Click any chapter below to read it.</p>",
        "<ol>",
    ]
    for ch_title, ch_url in chapter_titles_and_urls:
        parts.append(
            f'<li><a href="{_html_escape(ch_url)}">{_html_escape(ch_title)}</a></li>'
        )
    parts.append("</ol>")
    return "\n".join(parts)


class ConversionService:
    def __init__(self, repo: ConversionRepository):
        self._repo = repo

    def _accessibility_postprocess(self, html: str, title: str) -> str:
        """Run the accessibility analyzer + HTMLTransformer on LiteParse
        output before posting to Canvas.

        CLU-74: Without this, LiteParse-generated Canvas pages bypass
        every accessibility fixer that AutoRemedy runs on existing
        course content. LAMC Athletics showed the damage:
        unremediated → remediated added **48 skipped heading level**
        alerts, **10 possible list** alerts, **4 no heading
        structure** alerts, and 1 empty table header error — none of
        which existed in the source course.

        The converted HTML has the same accessibility problems as any
        freshly-authored Canvas page: arbitrary leaf-level headings,
        empty duplicate headings from PDF column splits, dash-prefixed
        lines that should be semantic lists, etc. Running the
        analyzer + transformer here ensures converted pages arrive
        in the same clean state that AutoRemedy produces on existing
        pages.

        Best-effort: if the transformer crashes on any edge case, the
        helper logs a warning and returns the input HTML unchanged
        rather than failing the whole conversion.
        """
        if not html:
            return html
        try:
            # The analyzer wants a CoursePage — wrap minimally.
            synthetic_page = CoursePage(
                id="conversion-postprocess",
                title=title or "converted page",
                identifier="conversion-postprocess",
                html_content=html,
            )
            analyzer = AccessibilityAnalyzer()
            # analyze_page is a coroutine, but its implementation is
            # synchronous — it just calls each rule's `check(soup, id)`
            # sequentially. Calling it synchronously here by driving
            # the coroutine to completion avoids making this whole
            # helper async.
            import asyncio
            try:
                # If we're already inside an event loop (e.g., the
                # run_conversion call chain), schedule via run_coroutine
                # isn't available; use a fresh loop path instead.
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None:
                issues = asyncio.run(analyzer.analyze_page(synthetic_page))
            else:
                # Inside an async context — run the (sync-body) rules
                # directly without event loop gymnastics. We mirror
                # `analyze_page` here without the async wrapping.
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                issues = []
                for rule in analyzer.rules:
                    try:
                        rule_issues = rule.check(soup, synthetic_page.id)
                        for issue in rule_issues:
                            issue.page_identifier = synthetic_page.identifier
                        issues.extend(rule_issues)
                    except Exception as rule_err:
                        _logger.warning(
                            "conversion_postprocess_rule_failed",
                            rule=rule.rule_id,
                            title=title,
                            error=str(rule_err),
                        )
            transformer = HTMLTransformer()
            transformed = transformer.transform(
                html,
                issues=issues,
                page_title=title or "",
            )
            return transformed
        except Exception as exc:
            _logger.warning(
                "conversion_accessibility_postprocess_failed",
                title=title,
                error=str(exc),
            )
            return html

    async def replace_file_links_across_course(
        self,
        session,
        course_id: int,
        original_file_id: int,
        new_page_url: str,
    ) -> int:
        """Find and replace links to the original file across all pages.

        CLU-13: After a document is converted to a Canvas wiki page,
        every other page in the course that linked to the original file
        should now link to the new page instead. AutoRemedy phase 4 has
        always done this in its inner loop, but the standalone
        `POST /api/courses/{id}/files/{file_id}/convert` route bypassed
        the loop entirely — leaving stale links to the now-archived
        original file scattered across the course.

        Pulled into a helper so both call sites share the same logic:
          - AutoRemedyService._run_convert_and_replace_phase
          - api/routes/files.start_conversion (after run_conversion completes)

        Returns the total count of links replaced across all pages.
        Failures on individual page writes are logged but don't abort
        the sweep — partial progress is better than none.
        """
        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        try:
            fetcher = ContentFetcher(client)
            writer = ContentWriter(client)

            all_pages = await fetcher.fetch_all(course_id)

            total_replaced = 0
            for page in all_pages:
                updated_html, count = FileManager.replace_file_links(
                    page.html_content,
                    original_file_id,
                    new_page_url,
                )
                if count <= 0:
                    continue
                try:
                    await writer.write_page_content(course_id, page, updated_html)
                    page.html_content = updated_html
                    total_replaced += count
                    _logger.info(
                        "conversion_links_replaced",
                        course_id=course_id,
                        file_id=original_file_id,
                        page_id=page.id,
                        page_title=page.title,
                        count=count,
                    )
                except Exception as write_err:
                    _logger.warning(
                        "conversion_link_replace_write_failed",
                        course_id=course_id,
                        file_id=original_file_id,
                        page_id=page.id,
                        error=str(write_err),
                    )
            return total_replaced
        finally:
            try:
                await client.close()
            except Exception:
                _logger.warning(
                    "conversion_canvas_client_close_failed",
                    course_id=course_id,
                )

    def create_job(self, session_id, course_id, file_id, filename, content_type=""):
        fmt = _FORMAT_MAP.get(content_type, "")
        if not fmt:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            fmt = ext if ext in _SUPPORTED_EXTS else ""
        if not fmt:
            raise ValueError(
                f"Unsupported document format for {filename!r} "
                f"(content_type={content_type!r}). "
                f"Supported: {sorted(_SUPPORTED_EXTS)}"
            )
        job = ConversionJob(
            id=str(ULID()), course_id=course_id, session_id=session_id,
            file_id=file_id, filename=filename, source_format=fmt,
            created_at=datetime.now(UTC),
        )
        self._repo.save_job(job)
        return job

    async def run_conversion(
        self,
        job_id,
        session,
        course_id,
        cancel_check=None,
    ):
        """Run conversion job. Optional `cancel_check` callback fires
        between LLM chunks (CLU-68) so AutoRemedy cancel propagates
        promptly even when stuck mid-document."""
        job = self._repo.get_job(job_id)
        if not job:
            return

        try:
            job.status = ScanStatus.RUNNING
            self._repo.save_job(job)

            if job.source_format == "ppt":
                job.status = ScanStatus.FAILED
                job.error = (
                    "Legacy .ppt format is not supported. Please open the file in "
                    "PowerPoint, save as .pptx, and re-upload to Canvas."
                )
                job.completed_at = datetime.now(UTC)
                self._repo.save_job(job)
                return

            client = CanvasClient(
                base_url=session.canvas_base_url,
                access_token=session.canvas_access_token,
                refresh_token=getattr(session, "canvas_refresh_token", ""),
            )

            try:
                # 1. Download file from Canvas
                fm = FileManager(client)
                data, _ = await fm.download_file(job.file_id, job.filename)
                job.progress = 0.25
                self._repo.save_job(job)

                # 2. Parse with LiteParse (Node CLI + LibreOffice under the hood)
                adapter = LiteParseAdapter()
                spatial = adapter.parse_bytes(data, job.filename)
                job.progress = 0.5
                self._repo.save_job(job)

                # CLU-69: bypass conversion for very large reference PDFs.
                # The LLM pipeline is meant for short-form pedagogical
                # content; running it on a 315-page textbook wastes
                # ~50 minutes and produces output of dubious value.
                # Mark the job COMPLETED with a skip_reason so the
                # AutoRemedy phase 4 loop knows not to archive or
                # link-replace, and exits cleanly.
                if (
                    job.source_format == "pdf"
                    and len(spatial.pages) > _DOC_SKIP_PAGES_THRESHOLD
                ):
                    job.skip_reason = (
                        f"PDF has {len(spatial.pages)} pages "
                        f"(>{_DOC_SKIP_PAGES_THRESHOLD} threshold)"
                    )
                    job.status = ScanStatus.COMPLETED
                    job.progress = 1.0
                    job.completed_at = datetime.now(UTC)
                    self._repo.save_job(job)
                    return

                title = job.filename.rsplit(".", 1)[0] if "." in job.filename else job.filename
                doc_svc = DocumentToHTMLService()
                validator = CanvasHTMLValidator()

                # 3a. Multi-page branch (CLU-67): large PDFs get split into
                # one Canvas page per chapter + a TOC page on top.
                use_multipage = (
                    job.source_format == "pdf"
                    and len(spatial.pages) > _CHAPTERS_THRESHOLD_PAGES
                )
                chapters = doc_svc.split_into_chapters(spatial) if use_multipage else []

                if use_multipage and len(chapters) >= 2:
                    heading_map = doc_svc.build_heading_map(spatial)
                    chapter_titles_and_urls: list[tuple[str, str]] = []

                    for ch_idx, chapter in enumerate(chapters, 1):
                        ch_html = await doc_svc.convert_chapter(
                            chapter, heading_map, cancel_check=cancel_check
                        )
                        ch_sanitized, _ = validator.sanitize(ch_html)
                        ch_title = f"{title} - {chapter.title}"
                        # CLU-74: run accessibility postprocess so
                        # chapter pages don't ship with LiteParse
                        # heading/list/table artifacts.
                        ch_sanitized = self._accessibility_postprocess(
                            ch_sanitized, ch_title
                        )
                        ch_page = await client.post(
                            f"/api/v1/courses/{course_id}/pages",
                            body={"wiki_page": {
                                "title": ch_title,
                                "body": ch_sanitized,
                                "published": False,
                            }},
                        )
                        ch_url = ch_page.get("html_url", "")
                        if not ch_url:
                            raise RuntimeError(
                                f"Canvas did not return html_url for chapter {ch_idx}"
                            )
                        job.child_page_urls.append(ch_url)
                        chapter_titles_and_urls.append((chapter.title, ch_url))
                        # Spread progress 0.5 → 0.9 across chapters
                        job.progress = 0.5 + (ch_idx / len(chapters)) * 0.4
                        self._repo.save_job(job)

                    # Build and POST the TOC page
                    toc_html = _build_toc_html(title, chapter_titles_and_urls)
                    toc_sanitized, _ = validator.sanitize(toc_html)
                    # CLU-74: run accessibility postprocess on the TOC.
                    toc_sanitized = self._accessibility_postprocess(
                        toc_sanitized, title
                    )
                    job.converted_html = toc_sanitized
                    toc_page = await client.post(
                        f"/api/v1/courses/{course_id}/pages",
                        body={"wiki_page": {
                            "title": title,
                            "body": toc_sanitized,
                            "published": False,
                        }},
                    )
                    job.canvas_page_id = toc_page.get("page_id") or toc_page.get("url")
                    job.canvas_page_url = toc_page.get("html_url", "")

                    job.status = ScanStatus.COMPLETED
                    job.progress = 1.0
                    job.completed_at = datetime.now(UTC)
                    self._repo.save_job(job)
                    return

                # 3b. Single-page branch (existing behavior for small docs)
                html = await doc_svc.convert(
                    spatial, title=title, cancel_check=cancel_check
                )
                job.progress = 0.75
                self._repo.save_job(job)

                # 4. Sanitize for Canvas
                sanitized, _ = validator.sanitize(html)
                # CLU-74: run accessibility postprocess so single-page
                # conversions (forms, waivers, small PDFs) don't ship
                # with LiteParse heading/list/table artifacts.
                sanitized = self._accessibility_postprocess(sanitized, title)
                job.converted_html = sanitized

                # 5. Create Canvas wiki page (unpublished by default)
                page_data = await client.post(
                    f"/api/v1/courses/{course_id}/pages",
                    body={"wiki_page": {"title": title, "body": sanitized, "published": False}},
                )
                job.canvas_page_id = page_data.get("page_id") or page_data.get("url")
                job.canvas_page_url = page_data.get("html_url", "")

                job.status = ScanStatus.COMPLETED
                job.progress = 1.0
                job.completed_at = datetime.now(UTC)
                self._repo.save_job(job)

            finally:
                try:
                    await client.close()
                except Exception:
                    _logger.warning("canvas_client_close_failed", job_id=job_id)

        except Exception as e:
            _logger.error("conversion_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)
