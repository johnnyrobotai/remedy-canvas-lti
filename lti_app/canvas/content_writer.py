"""Write remediated HTML back to Canvas via REST API."""

import structlog

from lti_app.canvas.client import CanvasAPIError, CanvasClient
from lti_app.core.remediation.canvas_validator import CanvasHTMLValidator
from lti_app.models import ContentType, CoursePage

_logger = structlog.get_logger(__name__)


class ContentWriter:
    """Writes remediated HTML content back to Canvas."""

    def __init__(self, client: CanvasClient):
        self._client = client

    async def write_page_content(
        self, course_id: int, page: CoursePage, html: str
    ) -> None:
        """Route to the correct write method based on content_type.

        Always sanitizes HTML through CanvasHTMLValidator before writing.
        """
        validator = CanvasHTMLValidator()
        sanitized_html, result = validator.sanitize(html)

        if result.tags_stripped or result.attributes_stripped:
            _logger.info(
                "content_writer_sanitized",
                page_id=page.id,
                tags_stripped=result.tags_stripped,
                attributes_stripped=result.attributes_stripped,
            )

        ct = page.content_type
        if ct == ContentType.WIKI_PAGE:
            # Extract the URL slug from canvas_url (e.g. "/courses/1/pages/my-page" → "my-page")
            # page.identifier is "page-{id}" which is NOT the Canvas API slug
            slug = (page.canvas_url or "").rsplit("/", 1)[-1] or page.identifier
            await self._write_page(course_id, slug, sanitized_html)
        elif ct == ContentType.ASSIGNMENT:
            await self._write_assignment(course_id, page.canvas_id, sanitized_html)
        elif ct in (ContentType.DISCUSSION, ContentType.ANNOUNCEMENT):
            await self._write_discussion(course_id, page.canvas_id, sanitized_html)
        elif ct == ContentType.SYLLABUS:
            await self._write_syllabus(course_id, sanitized_html)
        elif ct == ContentType.QUIZ:
            await self._write_quiz(course_id, page.canvas_id, sanitized_html)
        elif ct == ContentType.QUIZ_QUESTION:
            await self._write_quiz_question(
                course_id, page.parent_id, page.canvas_id, sanitized_html
            )
        elif ct == ContentType.CALENDAR_EVENT:
            await self._write_calendar_event(page.canvas_id, sanitized_html)
        elif ct == ContentType.RUBRIC:
            await self._write_rubric(course_id, page.canvas_id, sanitized_html)
        elif ct == ContentType.NEW_QUIZ:
            await self._write_new_quiz(course_id, page.canvas_id, sanitized_html)
        elif ct == ContentType.NEW_QUIZ_ITEM:
            await self._write_new_quiz_item(
                course_id, page.parent_id, page.canvas_id, sanitized_html
            )
        else:
            _logger.warning(
                "content_writer_unsupported_type",
                content_type=ct.value,
                page_id=page.id,
            )

    async def _write_page(
        self, course_id: int, page_url: str, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}/pages/{page_url}",
            {"wiki_page": {"body": html}},
        )

    async def _write_assignment(
        self, course_id: int, assignment_id: int, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
            {"assignment": {"description": html}},
        )

    async def _write_discussion(
        self, course_id: int, topic_id: int, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}/discussion_topics/{topic_id}",
            {"message": html},
        )

    async def _write_syllabus(self, course_id: int, html: str) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}",
            {"course": {"syllabus_body": html}},
        )

    async def _write_quiz(
        self, course_id: int, quiz_id: int, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}/quizzes/{quiz_id}",
            {"quiz": {"description": html}},
        )

    async def _write_quiz_question(
        self, course_id: int, quiz_id: int, question_id: int, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/questions/{question_id}",
            {"question": {"question_text": html}},
        )

    async def _write_calendar_event(
        self, event_id: int, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/calendar_events/{event_id}",
            {"calendar_event": {"description": html}},
        )

    async def _write_rubric(
        self, course_id: int, rubric_id: int, html: str
    ) -> None:
        await self._client.put(
            f"/api/v1/courses/{course_id}/rubrics/{rubric_id}",
            {"rubric": {"description": html}},
        )

    async def _write_new_quiz(
        self, course_id: int, quiz_id: int, html: str
    ) -> None:
        """Write a New Quiz's instructions via the /api/quiz/v1 endpoint.

        New Quizzes use a separate sub-API from classic quizzes and may
        require an additional LTI scope on the developer key. If the
        PUT returns 401/403/404 we log a warning and continue rather
        than crash the whole AutoRemedy job — the HTML fixes are still
        worth applying to every other content type.
        """
        try:
            await self._client.put(
                f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}",
                {"quiz": {"instructions": html}},
            )
        except CanvasAPIError as exc:
            if exc.status in (401, 403, 404):
                _logger.warning(
                    "content_writer_new_quiz_unsupported",
                    course_id=course_id,
                    quiz_id=quiz_id,
                    status=exc.status,
                )
                return
            raise

    async def _write_new_quiz_item(
        self, course_id: int, quiz_id: int, item_id: int, html: str
    ) -> None:
        """Write a New Quiz item's stem via the /api/quiz/v1 endpoint.

        Same graceful-degradation pattern as _write_new_quiz.
        """
        try:
            await self._client.put(
                f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items/{item_id}",
                {"item": {"entry": {"item_body": html}}},
            )
        except CanvasAPIError as exc:
            if exc.status in (401, 403, 404):
                _logger.warning(
                    "content_writer_new_quiz_item_unsupported",
                    course_id=course_id,
                    quiz_id=quiz_id,
                    item_id=item_id,
                    status=exc.status,
                )
                return
            raise
