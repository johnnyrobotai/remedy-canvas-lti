"""Fetches Canvas content and converts to CoursePage models."""

import asyncio
from typing import Any

import structlog
from ulid import ULID

from lti_app.canvas.client import CanvasClient
from lti_app.models import ContentType, CoursePage

_logger = structlog.get_logger(__name__)

_FETCH_CONCURRENCY = 10


# ---------------------------------------------------------------------------
# Config table for 6 simple content types
# ---------------------------------------------------------------------------

_CONTENT_TYPE_CONFIGS: list[dict[str, Any]] = [
    {
        "type": ContentType.ASSIGNMENT,
        "list_path": "/api/v1/courses/{course_id}/assignments",
        "html_field": "description",
        "id_field": "id",
        "title_field": "name",
        "params": {},
    },
    {
        "type": ContentType.DISCUSSION,
        "list_path": "/api/v1/courses/{course_id}/discussion_topics",
        "html_field": "message",
        "id_field": "id",
        "title_field": "title",
        "params": {},
    },
    {
        "type": ContentType.ANNOUNCEMENT,
        "list_path": "/api/v1/courses/{course_id}/discussion_topics",
        "html_field": "message",
        "id_field": "id",
        "title_field": "title",
        "params": {"only_announcements": "true"},
    },
    {
        "type": ContentType.SYLLABUS,
        "single_path": "/api/v1/courses/{course_id}",
        "html_field": "syllabus_body",
        "id_field": "id",
        "title_field": None,  # Always "Syllabus"
        "params": {"include[]": "syllabus_body"},
    },
    {
        "type": ContentType.CALENDAR_EVENT,
        "list_path": "/api/v1/calendar_events",
        "html_field": "description",
        "id_field": "id",
        "title_field": "title",
        "params_template": {"context_codes[]": "course_{course_id}", "type": "event"},
    },
    {
        "type": ContentType.RUBRIC,
        "list_path": "/api/v1/courses/{course_id}/rubrics",
        "html_field": "description",
        "id_field": "id",
        "title_field": "title",
        "params": {},
    },
]


class ContentFetcher:
    """Fetches Canvas course content and converts to CoursePage models."""

    def __init__(self, client: CanvasClient):
        self._client = client

    # ------------------------------------------------------------------
    # Top-level: fetch everything
    # ------------------------------------------------------------------

    async def fetch_all(self, course_id: int) -> list[CoursePage]:
        """Fetch all content types and return unified list of CoursePages."""
        all_pages: list[CoursePage] = []

        # Wiki pages (existing method)
        pages = await self.fetch_pages(course_id)
        all_pages.extend(pages)

        # Config-driven types
        for config in _CONTENT_TYPE_CONFIGS:
            try:
                if config["type"] == ContentType.SYLLABUS:
                    result = await self.fetch_syllabus(course_id)
                elif config["type"] == ContentType.ASSIGNMENT:
                    result = await self.fetch_assignments(course_id)
                elif config["type"] == ContentType.DISCUSSION:
                    result = await self.fetch_discussions(course_id)
                elif config["type"] == ContentType.ANNOUNCEMENT:
                    result = await self.fetch_announcements(course_id)
                elif config["type"] == ContentType.CALENDAR_EVENT:
                    result = await self.fetch_calendar_events(course_id)
                elif config["type"] == ContentType.RUBRIC:
                    result = await self.fetch_rubrics(course_id)
                else:
                    result = await self._fetch_by_config(course_id, config)
                all_pages.extend(result)
            except Exception as e:
                _logger.warning(
                    "content_fetcher_type_failed",
                    content_type=config["type"].value,
                    error=str(e),
                )

        # Quizzes
        try:
            quiz_pages = await self.fetch_quizzes(course_id)
            all_pages.extend(quiz_pages)
        except Exception as e:
            _logger.warning("content_fetcher_quizzes_failed", error=str(e))

        # New Quizzes (graceful skip if not enabled)
        try:
            new_quiz_pages = await self.fetch_new_quizzes(course_id)
            all_pages.extend(new_quiz_pages)
        except Exception as e:
            _logger.debug("content_fetcher_new_quizzes_skipped", error=str(e))

        _logger.info(
            "content_fetcher_all_complete",
            course_id=course_id,
            total_pages=len(all_pages),
        )
        return all_pages

    # ------------------------------------------------------------------
    # Wiki pages (original method, unchanged)
    # ------------------------------------------------------------------

    async def fetch_pages(self, course_id: int) -> list[CoursePage]:
        """Fetch all wiki pages with content (both published and unpublished).

        Unpublished pages are scanned and remediated because Canvas IMSCC
        exports include them by default, so students or re-import workflows
        will see them. Skipping unpublished meant ~20% of a course went
        unremediated.

        Canvas's ``/api/v1/courses/:id/pages`` endpoint accepts a
        ``published`` query parameter. Without it, behavior depends on the
        token's permissions: tokens without ``manage_wiki`` (e.g. some
        LTI Advantage flows) only return published pages, while teacher
        tokens return both. We make TWO explicit calls — one with
        ``published=true`` and one with ``published=false`` — and merge
        the results, so unpublished pages are guaranteed to come back
        regardless of token scope (Art103 production regression).
        """
        page_summaries: list[dict] = []
        seen_ids: set[int] = set()

        for published_filter in ("true", "false"):
            try:
                async for page in self._client.get_paginated(
                    f"/api/v1/courses/{course_id}/pages",
                    params={"published": published_filter},
                ):
                    page_id = page.get("page_id")
                    if page_id is not None and page_id in seen_ids:
                        continue  # Already collected from the other filter
                    if page_id is not None:
                        seen_ids.add(page_id)
                    page_summaries.append(page)
            except Exception as exc:
                _logger.warning(
                    "content_fetcher_published_filter_failed",
                    course_id=course_id,
                    published=published_filter,
                    error=str(exc),
                )

        _logger.info("content_fetcher_pages_listed", course_id=course_id, count=len(page_summaries))

        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)
        tasks = [
            self._fetch_page_body(course_id, summary, semaphore)
            for summary in page_summaries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        pages = []
        for result in results:
            if isinstance(result, Exception):
                _logger.warning("content_fetcher_page_failed", error=str(result))
                continue
            if result is not None:
                pages.append(result)

        _logger.info("content_fetcher_pages_fetched", course_id=course_id, pages=len(pages))
        return pages

    async def _fetch_page_body(
        self,
        course_id: int,
        summary: dict,
        semaphore: asyncio.Semaphore,
    ) -> CoursePage | None:
        """Fetch a single page's body and convert to CoursePage."""
        async with semaphore:
            page_url = summary.get("url", "")
            full_page = await self._client.get(
                f"/api/v1/courses/{course_id}/pages/{page_url}"
            )

            body = full_page.get("body") or ""
            if not body.strip():
                return None

            return CoursePage(
                id=str(ULID()),
                title=full_page.get("title", "Untitled"),
                identifier=f"page-{full_page.get('page_id', '')}",
                html_content=body,
                content_type=ContentType.WIKI_PAGE,
                canvas_id=full_page.get("page_id"),
                canvas_url=f"/courses/{course_id}/pages/{page_url}",
            )

    # ------------------------------------------------------------------
    # Config-driven content type methods
    # ------------------------------------------------------------------

    async def _fetch_by_config(
        self, course_id: int, config: dict[str, Any]
    ) -> list[CoursePage]:
        """Generic fetch using a config entry. For paginated list types."""
        path = config["list_path"].format(course_id=course_id)
        params = dict(config.get("params", {}))

        # Handle params_template (e.g. calendar events need course_{course_id})
        if "params_template" in config:
            params = {
                k: v.format(course_id=course_id) if isinstance(v, str) else v
                for k, v in config["params_template"].items()
            }

        pages: list[CoursePage] = []
        async for item in self._client.get_paginated(path, params=params):
            html = item.get(config["html_field"]) or ""
            if not html.strip():
                continue

            title_field = config.get("title_field", "title")
            title = item.get(title_field, "Untitled") if title_field else "Untitled"

            canvas_id = item.get(config["id_field"])
            content_type = config["type"]

            # Build a canvas_url for types with known URL patterns
            if content_type == ContentType.ASSIGNMENT:
                item_canvas_url: str | None = f"/courses/{course_id}/assignments/{canvas_id}"
            elif content_type in (ContentType.DISCUSSION, ContentType.ANNOUNCEMENT):
                item_canvas_url = f"/courses/{course_id}/discussion_topics/{canvas_id}"
            elif content_type == ContentType.CALENDAR_EVENT:
                item_canvas_url = f"/calendar?event_id={canvas_id}"
            else:
                item_canvas_url = None

            pages.append(CoursePage(
                id=str(ULID()),
                title=title,
                identifier=f"{content_type.value}-{canvas_id}",
                html_content=html,
                content_type=content_type,
                canvas_id=canvas_id,
                canvas_url=item_canvas_url,
            ))

        _logger.info(
            "content_fetcher_type_fetched",
            course_id=course_id,
            content_type=config["type"].value,
            count=len(pages),
        )
        return pages

    async def fetch_assignments(self, course_id: int) -> list[CoursePage]:
        """Fetch all assignments with descriptions."""
        config = next(c for c in _CONTENT_TYPE_CONFIGS if c["type"] == ContentType.ASSIGNMENT)
        return await self._fetch_by_config(course_id, config)

    async def fetch_discussions(self, course_id: int) -> list[CoursePage]:
        """Fetch all discussion topics."""
        config = next(c for c in _CONTENT_TYPE_CONFIGS if c["type"] == ContentType.DISCUSSION)
        return await self._fetch_by_config(course_id, config)

    async def fetch_announcements(self, course_id: int) -> list[CoursePage]:
        """Fetch all announcements."""
        config = next(c for c in _CONTENT_TYPE_CONFIGS if c["type"] == ContentType.ANNOUNCEMENT)
        return await self._fetch_by_config(course_id, config)

    async def fetch_calendar_events(self, course_id: int) -> list[CoursePage]:
        """Fetch calendar events for the course."""
        config = next(c for c in _CONTENT_TYPE_CONFIGS if c["type"] == ContentType.CALENDAR_EVENT)
        return await self._fetch_by_config(course_id, config)

    async def fetch_rubrics(self, course_id: int) -> list[CoursePage]:
        """Fetch rubrics with descriptions."""
        config = next(c for c in _CONTENT_TYPE_CONFIGS if c["type"] == ContentType.RUBRIC)
        return await self._fetch_by_config(course_id, config)

    async def fetch_syllabus(self, course_id: int) -> list[CoursePage]:
        """Fetch course syllabus body (single GET, not paginated)."""
        config = next(c for c in _CONTENT_TYPE_CONFIGS if c["type"] == ContentType.SYLLABUS)
        path = config["single_path"].format(course_id=course_id)
        params = dict(config.get("params", {}))

        data = await self._client.get(path, params=params)
        html = data.get(config["html_field"]) or ""
        if not html.strip():
            return []

        canvas_id = data.get(config["id_field"])
        return [CoursePage(
            id=str(ULID()),
            title="Syllabus",
            identifier=f"syllabus-{canvas_id}",
            html_content=html,
            content_type=ContentType.SYLLABUS,
            canvas_id=canvas_id,
            canvas_url=f"/courses/{course_id}/assignments/syllabus",
        )]

    # ------------------------------------------------------------------
    # Quizzes (dedicated methods for parent/child)
    # ------------------------------------------------------------------

    async def fetch_quizzes(self, course_id: int) -> list[CoursePage]:
        """Fetch classic quizzes and their questions."""
        pages: list[CoursePage] = []

        async for quiz in self._client.get_paginated(
            f"/api/v1/courses/{course_id}/quizzes",
        ):
            quiz_type = quiz.get("quiz_type", "")
            # Skip New Quizzes (quiz_type == "quizzes.next")
            if quiz_type == "quizzes.next":
                continue

            quiz_id = quiz.get("id")
            description = quiz.get("description") or ""

            if description.strip():
                pages.append(CoursePage(
                    id=str(ULID()),
                    title=quiz.get("title", "Untitled Quiz"),
                    identifier=f"quiz-{quiz_id}",
                    html_content=description,
                    content_type=ContentType.QUIZ,
                    canvas_id=quiz_id,
                    canvas_url=f"/courses/{course_id}/quizzes/{quiz_id}",
                ))

            # Fetch questions for this quiz
            try:
                async for question in self._client.get_paginated(
                    f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/questions",
                ):
                    q_text = question.get("question_text") or ""
                    if not q_text.strip():
                        continue

                    pages.append(CoursePage(
                        id=str(ULID()),
                        title=question.get("question_name", "Untitled Question"),
                        identifier=f"quiz_question-{question.get('id')}",
                        html_content=q_text,
                        content_type=ContentType.QUIZ_QUESTION,
                        canvas_id=question.get("id"),
                        parent_id=quiz_id,
                        canvas_url=f"/courses/{course_id}/quizzes/{quiz_id}",
                    ))
            except Exception as e:
                _logger.warning(
                    "content_fetcher_quiz_questions_failed",
                    quiz_id=quiz_id,
                    error=str(e),
                )

        _logger.info(
            "content_fetcher_quizzes_fetched",
            course_id=course_id,
            count=len(pages),
        )
        return pages

    async def fetch_new_quizzes(self, course_id: int) -> list[CoursePage]:
        """Fetch New Quizzes and their items. Skips gracefully if not enabled."""
        pages: list[CoursePage] = []

        async for quiz in self._client.get_paginated(
            f"/api/v1/courses/{course_id}/quizzes",
        ):
            if quiz.get("quiz_type") != "quizzes.next":
                continue

            quiz_id = quiz.get("id")
            description = quiz.get("description") or ""

            if description.strip():
                pages.append(CoursePage(
                    id=str(ULID()),
                    title=quiz.get("title", "Untitled New Quiz"),
                    identifier=f"new_quiz-{quiz_id}",
                    html_content=description,
                    content_type=ContentType.NEW_QUIZ,
                    canvas_id=quiz_id,
                    canvas_url=f"/courses/{course_id}/quizzes/{quiz_id}",
                ))

            # Fetch items for this New Quiz
            try:
                async for item in self._client.get_paginated(
                    f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/items",
                ):
                    item_body = item.get("entry", {}).get("item_body") or ""
                    if not item_body.strip():
                        continue

                    pages.append(CoursePage(
                        id=str(ULID()),
                        title=item.get("entry", {}).get("title", "Untitled Item"),
                        identifier=f"new_quiz_item-{item.get('id')}",
                        html_content=item_body,
                        content_type=ContentType.NEW_QUIZ_ITEM,
                        canvas_id=item.get("id"),
                        parent_id=quiz_id,
                        canvas_url=f"/courses/{course_id}/quizzes/{quiz_id}",
                    ))
            except Exception as e:
                _logger.debug(
                    "content_fetcher_new_quiz_items_skipped",
                    quiz_id=quiz_id,
                    error=str(e),
                )

        _logger.info(
            "content_fetcher_new_quizzes_fetched",
            course_id=course_id,
            count=len(pages),
        )
        return pages
