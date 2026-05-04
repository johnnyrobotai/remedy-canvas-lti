"""LLM-driven accessibility remediation using function/tool calling (Layer 3)."""

from __future__ import annotations

import structlog

from lti_app.core.ai.canvas_tools import REMEDIATION_SYSTEM_PROMPT, REMEDIATION_TOOLS
from lti_app.core.ai.vision_client import VisionClient
from lti_app.models import AccessibilityIssue, CoursePage

# Only offer the fix_html_content tool because some models ignore
# tool_choice and pick whichever tool they prefer, so limit the list
_FIX_HTML_TOOL = [t for t in REMEDIATION_TOOLS if t["function"]["name"] == "fix_html_content"]

_logger = structlog.get_logger(__name__)


def _format_issues(issues: list[AccessibilityIssue]) -> str:
    """Format issues into a readable list for the LLM prompt."""
    lines: list[str] = []
    for i, issue in enumerate(issues, 1):
        parts = [f"{i}. [{issue.severity.value.upper()}] {issue.rule_id}: {issue.message}"]
        if issue.wcag_criterion:
            parts.append(f"   WCAG: {issue.wcag_criterion}")
        if issue.element_html:
            snippet = issue.element_html[:200]
            parts.append(f"   Element: {snippet}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


class AIRemediator:
    """Layer 3: LLM-driven remediation using function/tool calling.

    Sends HTML content + accessibility issues to the LLM with tool definitions.
    The LLM returns structured tool calls with the fixed HTML. Falls back to
    returning the original HTML if tool calling fails.
    """

    def __init__(
        self,
        vision_client: VisionClient,
        *,
        run_id: str | None = None,
    ) -> None:
        self._vision = vision_client
        self._run_id = run_id

    async def remediate_page(
        self,
        page: CoursePage,
        issues: list[AccessibilityIssue],
    ) -> tuple[str, list[str]]:
        """Fix accessibility issues in a page's HTML using LLM tool calling.

        Returns (fixed_html, changes_made). If tool calling fails, returns
        the original HTML unchanged with an empty changes list.
        """
        if not issues:
            return page.html_content, []

        issues_text = _format_issues(issues)
        user_prompt = (
            f"Page title: {page.title}\n"
            f"Content type: {page.content_type.value}\n\n"
            f"HTML to fix:\n```html\n{page.html_content}\n```\n\n"
            f"Accessibility issues ({len(issues)}):\n{issues_text}"
        )

        messages = [
            {"role": "system", "content": REMEDIATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await self._vision.chat(
                model=self._vision.get_primary_model(),
                messages=messages,
                tools=_FIX_HTML_TOOL,
                tool_choice={"type": "function", "function": {"name": "fix_html_content"}},
                run_id=self._run_id,
                timeout=120.0,
            )

            # Parse tool call response
            if isinstance(result, list) and result:
                for call in result:
                    if call.get("name") == "fix_html_content":
                        args = call.get("arguments", {})
                        fixed_html = args.get("fixed_html", "")
                        changes = args.get("changes_made", [])
                        if fixed_html:
                            _logger.info(
                                "ai_remediation_success",
                                page_id=page.id,
                                changes_count=len(changes),
                            )
                            return fixed_html, changes

            _logger.warning("ai_remediation_no_tool_call", page_id=page.id)
            return page.html_content, []

        except Exception as e:
            _logger.warning(
                "ai_remediation_failed",
                page_id=page.id,
                error=str(e),
            )
            return page.html_content, []
